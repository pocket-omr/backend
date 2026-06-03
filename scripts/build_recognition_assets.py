"""Convert the trained char-recognition checkpoint into serving assets.

The training notebook saved the MobileNetV3-small char model as a bare
``state_dict`` (``omr_MobileNetV3_best.pt``) and the *effective* label mapping
(``idx_to_char`` after rare classes were dropped) was NOT persisted alongside
it. This script:

  1. Loads the ``state_dict``, infers ``num_classes`` from the final layer.
  2. Rebuilds the architecture, loads the weights, and exports a TorchScript
     model (``recognition_models/MobileNetV3_scripted.pt``) so the API can load
     it with ``torch.jit.load`` — no model class needed at serving time.
  3. Writes ``recognition_models/name_reco_labels.json`` with ``idx_to_char``,
     ``img_size``, ``normalization``, ``max_field_lengths`` and ``known_fields``.

Recovering the exact 36-of-38 mapping
-------------------------------------
The full alphabet is 38 classes: ' ', '-', 0-9, A-Z. The trained head has 36,
so 2 rare classes (<2 training samples) were dropped and the rest remapped to a
compact 0..N-1 space in ascending original order. To reproduce the exact mapping,
in order of preference:

  BEST: re-run the notebook's cell-21 save and point --ckpt at the resulting
        recognition_models/MobileNetV3.pt — it embeds the authoritative
        idx_to_char, which this script reads directly (no flags needed).

  --csv  <omr_dataset.xlsx|csv>   replay the notebook's drop logic on the
                                  ground-truth labels, or
  --dropped "Q,W"                pass the dropped characters explicitly.

With none of these (and the bare state_dict), a PROVISIONAL mapping is written
(flagged in the JSON and at startup). Only trustworthy once the real mapping is set.

Usage:
  # authoritative: checkpoint that embeds idx_to_char (notebook cell 21)
  python scripts/build_recognition_assets.py --ckpt path/to/MobileNetV3.pt

  # or recover from the bare state_dict
  python scripts/build_recognition_assets.py \
      [--ckpt ../models/omr_MobileNetV3_best.pt] [--out recognition_models] \
      [--csv path/to/omr_dataset.xlsx] [--dropped "C1,C2"]
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.recognition.char_model import build_mobilenetv3_char

# Full character set the alphabet was defined over (notebook CHAR_CLASSES).
CHAR_CLASSES = [
    " ", "-", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
    "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M",
    "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z",
]
CHAR_TO_IDX = {c: i for i, c in enumerate(CHAR_CLASSES)}

# Box caps per field — caps over-detected trailing cells to the printed field
# widths. Values from the canonical training notebook (models/pocket-omr).
MAX_FIELD_LENGTHS = {
    "first_name": 16,
    "last_name": 11,
    "group": 2,
    "registration_number": 12,
}
KNOWN_FIELDS = ["first_name", "last_name", "group", "registration_number"]

# Default provisional drop if the real mapping can't be recovered. These are the
# rarest letters in typical Latin name data — A GUESS, flagged in the output.
DEFAULT_PROVISIONAL_DROPPED = ["Q", "W", "X"]


def _infer_num_classes(state_dict):
    for key in ("classifier.3.weight", "classifier.3.bias"):
        if key in state_dict:
            return int(state_dict[key].shape[0])
    raise SystemExit("Could not find classifier head in state_dict to infer num_classes.")


def _dropped_from_csv(csv_path):
    """Replay the notebook's <2-sample drop on the ground-truth labels."""
    import pandas as pd

    p = Path(csv_path)
    if p.suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(p, dtype=str)
    else:
        df = pd.read_csv(p, dtype=str)
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]

    counts = Counter()
    for _, row in df.iterrows():
        for field in ("first_name", "last_name"):
            for ch in str(row.get(field, "")).upper():
                if ch in CHAR_TO_IDX:
                    counts[ch] += 1
        for field in ("group", "registration_number"):
            for ch in str(row.get(field, "")):
                if ch in CHAR_TO_IDX:
                    counts[ch] += 1
    # Space class is synthesised from blank boxes during training; treat as common.
    counts[" "] += 2
    return [c for c in CHAR_CLASSES if counts[c] < 2]


def build_idx_to_char(num_classes, dropped):
    """Reproduce IDX_TO_CHAR_EFF: drop classes, remap survivors ascending."""
    valid = [c for c in CHAR_CLASSES if c not in set(dropped)]
    if len(valid) != num_classes:
        raise SystemExit(
            f"Dropped {dropped} leaves {len(valid)} classes but the model has "
            f"{num_classes}. Provide the correct dropped set (--csv/--dropped)."
        )
    return {str(i): c for i, c in enumerate(valid)}


def main():
    ap = argparse.ArgumentParser()
    here = Path(__file__).resolve().parents[1]
    ap.add_argument("--ckpt", default=str(here.parent / "models" / "omr_MobileNetV3_best.pt"))
    ap.add_argument("--out", default=str(here / "recognition_models"))
    ap.add_argument("--csv", default=None, help="ground-truth labels to recover exact mapping")
    ap.add_argument("--dropped", default=None, help="comma-separated dropped characters")
    args = ap.parse_args()

    ckpt_path = Path(args.ckpt)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not ckpt_path.is_file():
        raise SystemExit(f"Checkpoint not found: {ckpt_path}")

    print(f"Loading checkpoint: {ckpt_path}")
    raw = torch.load(ckpt_path, map_location="cpu", weights_only=False)

    # The notebook's cell-21 save is a dict carrying the authoritative mapping:
    #   {'model_state_dict', 'num_classes', 'idx_to_char'}.
    # The bare cell-14 save (omr_MobileNetV3_best.pt) is just the state_dict.
    ckpt_idx_to_char = None
    if isinstance(raw, dict) and "model_state_dict" in raw:
        state_dict = raw["model_state_dict"]
        if raw.get("idx_to_char"):
            ckpt_idx_to_char = {str(k): v for k, v in raw["idx_to_char"].items()}
    else:
        state_dict = raw
    num_classes = _infer_num_classes(state_dict)
    print(f"Inferred num_classes = {num_classes} (full alphabet = {len(CHAR_CLASSES)})")

    # Determine the label mapping. Priority: embedded -> --dropped -> --csv ->
    # identity (no drop) -> provisional guess.
    provisional = False
    if ckpt_idx_to_char is not None:
        if len(ckpt_idx_to_char) != num_classes:
            raise SystemExit(
                f"Checkpoint idx_to_char has {len(ckpt_idx_to_char)} entries but the "
                f"model head is {num_classes}-wide."
            )
        idx_to_char = ckpt_idx_to_char
        dropped = [c for c in CHAR_CLASSES if c not in set(idx_to_char.values())]
        print(f"Using idx_to_char embedded in checkpoint (authoritative). dropped={dropped!r}")
    else:
        if args.dropped:
            dropped = [c if c != "" else " " for c in args.dropped.split(",")]
            print(f"Using explicit dropped classes: {dropped!r}")
        elif args.csv:
            dropped = _dropped_from_csv(args.csv)
            print(f"Recovered dropped classes from {args.csv}: {dropped!r}")
        elif num_classes == len(CHAR_CLASSES):
            dropped = []
        else:
            n_drop = len(CHAR_CLASSES) - num_classes
            dropped = DEFAULT_PROVISIONAL_DROPPED[:n_drop]
            provisional = True
            print(
                f"WARNING: no embedded/--csv/--dropped mapping and model has "
                f"{num_classes} != {len(CHAR_CLASSES)} classes. Writing a PROVISIONAL "
                f"mapping (dropped guess = {dropped!r}). Recognition output is NOT "
                f"reliable until the real mapping is supplied."
            )
        idx_to_char = build_idx_to_char(num_classes, dropped)

    # Rebuild architecture, load weights, export TorchScript.
    model = build_mobilenetv3_char(num_classes, pretrained=False)
    missing, unexpected = model.load_state_dict(state_dict, strict=False)
    if missing or unexpected:
        print(f"  state_dict missing={list(missing)} unexpected={list(unexpected)}")
    model.eval()

    example = torch.zeros(1, 1, 48, 48, dtype=torch.float32)
    scripted = torch.jit.trace(model, example)
    scripted_path = out_dir / "MobileNetV3_scripted.pt"
    scripted.save(str(scripted_path))
    print(f"Saved TorchScript model -> {scripted_path}")

    # Sanity: scripted output shape.
    with torch.no_grad():
        out = scripted(example)
    assert tuple(out.shape) == (1, num_classes), out.shape
    print(f"  scripted forward OK, output shape {tuple(out.shape)}")

    labels = {
        "idx_to_char": idx_to_char,
        "img_size": 48,
        "normalization": {"mean": 0.5, "std": 0.5, "ink_positive": True},
        "max_field_lengths": MAX_FIELD_LENGTHS,
        "known_fields": KNOWN_FIELDS,
        "num_classes": num_classes,
        "char_classes_full": CHAR_CLASSES,
        "dropped_classes": dropped,
        "idx_to_char_provisional": provisional,
    }
    labels_path = out_dir / "name_reco_labels.json"
    labels_path.write_text(json.dumps(labels, indent=2, ensure_ascii=False))
    print(f"Saved labels -> {labels_path}")
    if provisional:
        print(
            "\n*** PROVISIONAL MAPPING WRITTEN — re-run with --csv <omr_dataset> "
            "or --dropped to set the authoritative idx_to_char. ***"
        )


if __name__ == "__main__":
    main()
