"""Name-recognition serving: load the model once, grade one sheet per call.

Loads the TorchScript char model + label metadata at startup (see
``load_recognition_assets``) and exposes ``grade_sheet_image`` which runs the
full per-sheet pipeline: segment -> detect boxes -> recognise characters ->
postprocess -> (return predicted fields + QR for the caller to match).
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn.functional as F

from app.recognition import name_preprocess as npp
from app.recognition.postprocess import postprocess_field
from app.recognition.sheet_pipeline_v11 import process_sheet


class RecognitionError(RuntimeError):
    """Raised when a sheet cannot be recognised (e.g. no boxes detected)."""


@dataclass
class RecognitionAssets:
    model: torch.jit.ScriptModule
    idx_to_char: dict[int, str]
    img_size: int
    max_field_lengths: dict[str, int]
    known_fields: list[str]
    provisional: bool


def load_recognition_assets(models_dir: str | Path) -> RecognitionAssets:
    """Load the scripted model + labels. Fails loudly if anything is missing.

    Validates that the label mapping size matches the model's output dimension
    so a stale/wrong ``name_reco_labels.json`` is caught at startup, not per
    request.
    """
    models_dir = Path(models_dir)
    model_path = models_dir / "MobileNetV3_scripted.pt"
    labels_path = models_dir / "name_reco_labels.json"

    if not model_path.is_file():
        raise FileNotFoundError(
            f"Scripted recognition model not found: {model_path}. "
            "Run scripts/build_recognition_assets.py to generate it."
        )
    if not labels_path.is_file():
        raise FileNotFoundError(
            f"Recognition labels not found: {labels_path}. "
            "Run scripts/build_recognition_assets.py to generate it."
        )

    model = torch.jit.load(str(model_path), map_location="cpu")
    model.eval()

    labels = json.loads(labels_path.read_text())
    idx_to_char = {int(k): v for k, v in labels["idx_to_char"].items()}
    img_size = int(labels.get("img_size", npp.IMG_SIZE))

    # Dry forward to confirm the head size lines up with the label mapping.
    with torch.no_grad():
        out = model(torch.zeros(1, 1, img_size, img_size, dtype=torch.float32))
    n_out = int(out.shape[1])
    if n_out != len(idx_to_char):
        raise RuntimeError(
            f"Model outputs {n_out} classes but name_reco_labels.json maps "
            f"{len(idx_to_char)}. Regenerate the labels with the correct mapping "
            "(scripts/build_recognition_assets.py --csv/--dropped)."
        )

    return RecognitionAssets(
        model=model,
        idx_to_char=idx_to_char,
        img_size=img_size,
        max_field_lengths=labels.get("max_field_lengths", {}),
        known_fields=labels.get("known_fields", list(npp.FIELD_NAMES)),
        provisional=bool(labels.get("idx_to_char_provisional", False)),
    )


@torch.no_grad()
def _recognize_char(assets: RecognitionAssets, gray_crop: np.ndarray) -> str:
    norm = npp.binarize_crop(gray_crop, assets.img_size)
    x = torch.from_numpy(np.ascontiguousarray(norm))[None, None]  # (1,1,H,W)
    probs = F.softmax(assets.model(x), dim=1)[0]
    idx = int(probs.argmax().item())
    return assets.idx_to_char.get(idx, "")


def _recognize_personal_info(assets: RecognitionAssets, color_crop: np.ndarray) -> dict[str, str]:
    """Recognise the four roster fields from a personal-info colour crop."""
    boxes, _, _ = npp.detect_boxes(color_crop)
    if not boxes:
        raise RecognitionError("No character boxes detected on the personal-info region.")
    fields = npp.assign_fields(boxes)
    gray = cv2.cvtColor(color_crop, cv2.COLOR_BGR2GRAY) if color_crop.ndim == 3 else color_crop
    h, w = gray.shape[:2]

    predicted: dict[str, str] = {}
    for field in assets.known_fields:
        field_boxes = sorted(fields.get(field, []), key=lambda b: b[0])
        cap = assets.max_field_lengths.get(field)
        if cap:
            field_boxes = field_boxes[:cap]
        chars = []
        for x, y, bw, bh in field_boxes:
            y1, y2 = max(0, y + 1), min(h, y + bh - 1)
            x1, x2 = max(0, x + 1), min(w, x + bw - 1)
            crop = gray[y1:y2, x1:x2]
            if crop.size == 0 or npp.is_empty_box(crop):
                continue
            chars.append(_recognize_char(assets, crop))
        predicted[field] = postprocess_field("".join(chars), field)
    return predicted


def grade_sheet_image(
    image_path: str | Path, assets: RecognitionAssets
) -> dict:
    """Segment + recognise one answer-sheet image.

    Returns ``{"predicted": {...four fields...}, "qr_data": <str|None>}``.
    Manages (and cleans up) its own per-request temp dir.
    """
    image_path = Path(image_path)
    with tempfile.TemporaryDirectory(prefix="omr_grade_") as tmp:
        info = process_sheet(str(image_path), tmp)
        if not info.get("ok"):
            raise RecognitionError(
                f"Sheet segmentation failed: {info.get('note', 'unknown error')}"
            )
        stem = info["name"]
        regions = Path(tmp) / stem / "regions"
        color_path = regions / "personal_info_color.png"
        if not color_path.is_file():
            raise RecognitionError("Segmentation did not produce personal_info_color.png.")
        color_crop = cv2.imread(str(color_path))
        if color_crop is None:
            raise RecognitionError("Could not read the personal-info colour crop.")

        predicted = _recognize_personal_info(assets, color_crop)
        return {"predicted": predicted, "qr_data": info.get("qr_data")}
