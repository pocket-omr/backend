"""Answer-bubble extraction + filled/empty classification.

Pipeline (mirrors the training notebook
``fork-of-d-tection-de-bulles-omr-filled-vs-empty-4.ipynb``):

  1. A YOLOv8 detector (``bubble_yolo.pt``) finds every bubble box on the
     warped *answers* region crop.
  2. ``assign_grid`` orders the boxes into a (question, choice) grid, handling
     multi-block layouts.
  3. A small CNN (``bubble_cnn_scripted.pt``, TorchScript) classifies each
     64x64 grayscale crop as filled (sigmoid > 0.5) or empty.

Output is the set of filled choices per question, which the grading seam turns
into a per-question answer and compares against the teacher's key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import torch

BUBBLE_SIZE = 64


# ---------------------------------------------------------------------------
# Geometry helpers (from the notebook)
# ---------------------------------------------------------------------------
def assign_grid(bubbles, num_questions, num_choices):
    """Assign each detected bubble to a (question, choice) cell.

    `bubbles` is a list of (cx, cy, r, x1, y1, x2, y2, conf). Returns a dict
    {(q, c): bubble} with q in 1..num_questions and c in 1..num_choices.
    Handles single-block and multi-block (side-by-side / stacked) layouts.
    """
    if not bubbles:
        return {}

    ys = sorted(b[1] for b in bubbles)
    xs = sorted(b[0] for b in bubbles)
    r_med = max(1, int(np.median([b[2] for b in bubbles])))

    def median_gap(positions):
        if len(positions) < 2:
            return r_med
        gaps = [
            positions[i + 1] - positions[i]
            for i in range(len(positions) - 1)
            if positions[i + 1] - positions[i] > 2
        ]
        return float(np.median(gaps)) if gaps else float(r_med)

    def cluster(positions, tol):
        if not positions:
            return []
        sorted_pos = sorted(positions)
        groups = [[sorted_pos[0]]]
        for p in sorted_pos[1:]:
            if p - groups[-1][-1] <= tol:
                groups[-1].append(p)
            else:
                groups.append([p])
        return [int(np.mean(g)) for g in groups]

    gap_y = median_gap(ys)
    gap_x = median_gap(xs)
    tol_y = max(r_med * 0.8, gap_y * 0.4)
    tol_x = max(r_med * 0.8, gap_x * 0.4)

    row_centers = cluster(ys, tol_y)
    col_centers = cluster(xs, tol_x)

    n_rows = len(row_centers)
    n_cols = len(col_centers)
    n_col_blocks = max(1, round(n_cols / num_choices)) if num_choices else 1
    n_row_blocks = max(1, round(n_rows / num_questions)) if num_questions else 1

    grid = {}
    for b in bubbles:
        cx, cy = b[0], b[1]
        row_idx = int(np.argmin([abs(cy - r) for r in row_centers]))
        col_idx = int(np.argmin([abs(cx - c) for c in col_centers]))

        if n_col_blocks > 1:
            cols_per_block = max(1, n_cols // n_col_blocks)
            block_col = col_idx // cols_per_block
            local_col = col_idx % cols_per_block
            rows_per_block = max(1, n_rows // n_row_blocks)
            q = block_col * rows_per_block + row_idx + 1
            c = local_col + 1
        elif n_row_blocks > 1:
            rows_per_block = max(1, n_rows // n_row_blocks)
            block_row = row_idx // rows_per_block
            local_row = row_idx % rows_per_block
            q = local_row + 1
            c = block_row * (n_cols // max(1, n_row_blocks)) + col_idx + 1
        else:
            q = row_idx + 1
            c = col_idx + 1

        if 1 <= q <= num_questions and 1 <= c <= num_choices and (q, c) not in grid:
            grid[(q, c)] = b

    return grid


def extract_bubble_crop(img_gray, x1, y1, x2, y2, size=BUBBLE_SIZE, pad=0):
    """Crop a bubble and resize to size x size grayscale.

    Uses the detected box with no padding by default: padding bleeds the
    neighbouring whitespace / printed ring into the crop, which makes the
    filled/empty CNN read empty and filled bubbles alike.
    """
    h, w = img_gray.shape[:2]
    x1c, y1c = max(0, x1 - pad), max(0, y1 - pad)
    x2c, y2c = min(w, x2 + pad), min(h, y2 + pad)
    crop = img_gray[y1c:y2c, x1c:x2c]
    if crop.size == 0:
        return np.zeros((size, size), dtype=np.uint8)
    return cv2.resize(crop, (size, size), interpolation=cv2.INTER_AREA)


@dataclass
class SheetAnswers:
    """Result of reading one answer sheet's bubbles.

    - answers: per-question detected choice index (0-based), ordered by question
      number; None when a question has zero or more-than-one filled bubble.
    - confidence: 0..100 mean classification confidence over detected bubbles.
    - question_confidence: 0..100 per question = weakest bubble decision on it.
    - flagged_questions: 1-based question numbers the model is unsure about
      (a bubble below the review threshold, or a double-mark).
    - needs_review: True if any question is flagged.
    - filled: per-question list of filled choice indices (0-based) — the raw
      detected set, used for multiple-correct grading.
    - detected_bubbles / expected_bubbles: coverage diagnostics.
    """

    answers: list[int | None]
    confidence: float
    question_confidence: list[float]
    flagged_questions: list[int]
    needs_review: bool
    detected_bubbles: int
    expected_bubbles: int
    filled: list[list[int]] = field(default_factory=list)


class BubbleGrader:
    """Loads the YOLO detector + filled/empty CNN once, grades sheets on demand."""

    def __init__(self, yolo_path: str | Path, cnn_path: str | Path,
                 conf: float = 0.30, iou: float = 0.30, review_threshold: float = 0.80):
        from ultralytics import YOLO

        yolo_path = Path(yolo_path)
        cnn_path = Path(cnn_path)
        if not yolo_path.is_file():
            raise FileNotFoundError(f"Bubble YOLO model not found: {yolo_path}")
        if not cnn_path.is_file():
            raise FileNotFoundError(f"Bubble CNN model not found: {cnn_path}")

        self.yolo = YOLO(str(yolo_path))
        self.cnn = torch.jit.load(str(cnn_path), map_location="cpu")
        self.cnn.eval()
        self.conf = conf
        self.iou = iou
        self.review_threshold = review_threshold

    def _detect(self, img_bgr: np.ndarray, conf: float):
        res = self.yolo(img_bgr, conf=conf, iou=self.iou, verbose=False)[0]
        if res.boxes is None or len(res.boxes) == 0:
            return []
        boxes = res.boxes.xyxy.cpu().numpy()
        confs = res.boxes.conf.cpu().numpy()
        bubbles = []
        for (x1, y1, x2, y2), c in zip(boxes, confs):
            cx, cy = int((x1 + x2) / 2), int((y1 + y2) / 2)
            r = int(max(x2 - x1, y2 - y1) / 2)
            bubbles.append((cx, cy, r, int(x1), int(y1), int(x2), int(y2), float(c)))
        bubbles.sort(key=lambda b: (b[1], b[0]))
        return bubbles

    @torch.no_grad()
    def _classify(self, crops: list[np.ndarray]) -> np.ndarray:
        """Return filled-probability per crop (sigmoid of the CNN logit)."""
        batch = np.stack([c.astype(np.float32) / 255.0 for c in crops])  # (N,H,W)
        tensor = torch.from_numpy(batch).unsqueeze(1)  # (N,1,H,W)
        logits = self.cnn(tensor).reshape(-1)
        return torch.sigmoid(logits).cpu().numpy()

    def grade(self, answers_bgr, num_questions, num_choices, classify_bgr=None) -> SheetAnswers:
        """Grade one answers region.

        ``answers_bgr`` is used for bubble DETECTION (YOLO). If ``classify_bgr``
        is given, character crops for filled/empty CLASSIFICATION are taken from
        it instead — use the bold ``answers.png`` to detect and the colour
        ``answers_color.png`` to classify (the bold crop loses the fill signal).
        """
        expected = num_questions * num_choices
        classify_src = classify_bgr if classify_bgr is not None else answers_bgr
        if classify_src.ndim == 3:
            img_gray = cv2.cvtColor(classify_src, cv2.COLOR_BGR2GRAY)
        else:
            img_gray = classify_src

        bubbles = self._detect(answers_bgr, self.conf)
        # Too many detections => NMS too loose; retighten (notebook heuristic).
        if expected and len(bubbles) > expected * 3:
            bubbles = self._detect(answers_bgr, max(self.conf, 0.70))

        grid = assign_grid(bubbles, num_questions, num_choices)

        # Classify every assigned bubble.
        keys = list(grid.keys())
        crops = [
            extract_bubble_crop(img_gray, *grid[k][3:7])
            for k in keys
        ]
        answers: list[int | None] = [None] * num_questions
        q_conf: list[float] = [0.0] * num_questions
        if not crops:
            flagged = list(range(1, num_questions + 1))
            return SheetAnswers(
                answers, 0.0, q_conf, flagged, bool(flagged), len(bubbles), expected,
                filled=[[] for _ in range(num_questions)],
            )

        probs = self._classify(crops)

        # Per question: collect filled choices and the decision confidence of
        # each bubble (max(p, 1-p) — how sure the model is either way).
        filled_by_q: dict[int, list[int]] = {q: [] for q in range(1, num_questions + 1)}
        decided_by_q: dict[int, list[float]] = {q: [] for q in range(1, num_questions + 1)}
        all_decisions: list[float] = []
        for (q, c), p in zip(keys, probs):
            d = float(max(p, 1.0 - p))
            all_decisions.append(d)
            decided_by_q[q].append(d)
            if p > 0.5:
                filled_by_q[q].append(c)

        flagged: list[int] = []
        for q in range(1, num_questions + 1):
            choices = filled_by_q[q]
            decisions = decided_by_q[q]
            # Single-answer MCQ: exactly one filled bubble => that choice (0-based).
            # Zero or multiple => unanswered/ambiguous (counts as wrong).
            if len(choices) == 1:
                answers[q - 1] = choices[0] - 1
            # Weakest bubble decision drives the question's confidence.
            weakest = min(decisions) if decisions else 0.0
            q_conf[q - 1] = round(weakest * 100, 1)
            # Flag if the model was unsure of a bubble, a bubble is missing, or
            # the student double-marked (more than one filled).
            if (
                not decisions
                or weakest < self.review_threshold
                or len(choices) > 1
            ):
                flagged.append(q)

        # Filled choice indices (0-based) per question — for multi-correct grading.
        filled = [sorted(c - 1 for c in filled_by_q[q]) for q in range(1, num_questions + 1)]

        confidence = round(float(np.mean(all_decisions)) * 100, 1) if all_decisions else 0.0
        return SheetAnswers(
            answers, confidence, q_conf, flagged, bool(flagged), len(bubbles), expected,
            filled=filled,
        )
