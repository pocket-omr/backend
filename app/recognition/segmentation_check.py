"""Lightweight 'can this sheet be read?' check for the upload pipeline.

Runs the OpenCV segmentation pipeline and verifies the personal-info region was
produced and contains detectable character cells. OpenCV/numpy only — NO torch —
so it can run during image upload regardless of whether the recognition model is
loaded. Returns a human-readable reason when a sheet can't be segmented, or None
when it's fine.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import cv2

from app.recognition import name_preprocess as npp
from app.recognition.sheet_pipeline_v11 import process_sheet


def segmentation_failure_reason(image_bytes: bytes, filename: str = "sheet.png") -> str | None:
    """Return why a sheet failed segmentation, or None if it segments cleanly.

    Failure modes surfaced to the user:
      - the image couldn't be decoded,
      - the page/personal-info region couldn't be located,
      - no name character cells were found in the personal-info region.
    """
    suffix = Path(filename).suffix or ".png"
    with tempfile.TemporaryDirectory(prefix="omr_segcheck_") as tmp:
        img_path = Path(tmp) / f"in{suffix}"
        img_path.write_bytes(image_bytes)

        try:
            info = process_sheet(str(img_path), tmp)
        except Exception:
            return "could not process image"

        if not info.get("ok"):
            return "could not read image"

        regions = Path(tmp) / info["name"] / "regions"
        color_path = regions / "personal_info_color.png"
        if not color_path.is_file():
            return "page not detected"

        crop = cv2.imread(str(color_path))
        if crop is None:
            return "personal-info region not found"

        boxes, _, _ = npp.detect_boxes(crop)
        if not boxes:
            return "no name boxes detected"

    return None
