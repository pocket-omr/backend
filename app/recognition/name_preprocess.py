"""Personal-info box detection and per-character crop preparation.

OpenCV/numpy only — no torch. The detection + preprocessing logic is lifted
verbatim from the training notebook ``final-name-recognition.ipynb`` so that
inference at serving time matches exactly how the model was trained. The public
names (``detect_boxes``, ``assign_fields``, ``binarize_crop``, ``is_empty_box``,
``FIELD_NAMES``, ``IMG_SIZE``) are the seam the recognition service wires to.
"""

import cv2
import numpy as np

# Character image size the model was trained on (square, grayscale).
IMG_SIZE = 48

# Field reading order on the sheet. The personal-info grid lays out fields in
# this order top-to-bottom / left-to-right, so the box->field assignment below
# relies on it. (Order matches the training notebook exactly — do not reorder.)
FIELD_NAMES = ["first_name", "group", "last_name", "registration_number"]

FIELD_COLORS_BGR = {
    "first_name": (0, 0, 255),
    "group": (0, 180, 0),
    "last_name": (255, 0, 0),
    "registration_number": (0, 165, 255),
}


# ---------------------------------------------------------------------------
# Box detection
# ---------------------------------------------------------------------------
def _extract_grid_from_thresh(thresh):
    h_w = max(25, thresh.shape[1] // 40)
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (h_w, 1))
    horiz = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, h_kernel, iterations=1)

    v_h = max(15, thresh.shape[0] // 24)
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_h))
    vert = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, v_kernel, iterations=1)

    grid = cv2.bitwise_or(horiz, vert)
    return grid, horiz, vert


def _find_boxes_in_grid(grid, aspect_lo=0.4, aspect_hi=2.5, min_side=5):
    contours, _ = cv2.findContours(grid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w < min_side or h < min_side:
            continue
        aspect = w / float(h)
        if aspect_lo <= aspect <= aspect_hi:
            candidates.append((x, y, w, h))
    if not candidates:
        return []
    widths = np.array([b[2] for b in candidates])
    heights = np.array([b[3] for b in candidates])
    median_w = np.median(widths)
    median_h = np.median(heights)
    boxes = [
        (x, y, w, h)
        for (x, y, w, h) in candidates
        if 0.45 * median_w <= w <= 1.55 * median_w
        and 0.45 * median_h <= h <= 1.55 * median_h
    ]
    return boxes


def detect_boxes(personal_img_bgr, personal_img_bw=None):
    """Detect the handwriting cells in a personal-info crop.

    Returns ``(boxes, grid, thresh)`` where ``boxes`` is a list of
    ``(x, y, w, h)`` tuples. ``personal_img_bgr`` is the colour reference crop
    (``personal_info_color.png``).
    """
    gray = (
        cv2.cvtColor(personal_img_bgr, cv2.COLOR_BGR2GRAY)
        if personal_img_bgr.ndim == 3
        else personal_img_bgr
    )
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    thresh = cv2.adaptiveThreshold(
        blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 10
    )
    grid, horiz, vert = _extract_grid_from_thresh(thresh)
    boxes = _find_boxes_in_grid(grid)

    if len(boxes) == 0 and personal_img_bw is not None:
        bw_inv = cv2.bitwise_not(personal_img_bw)
        bw_blurred = cv2.GaussianBlur(bw_inv, (5, 5), 0)
        bw_thresh = cv2.adaptiveThreshold(
            bw_blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 10
        )
        grid2, _, _ = _extract_grid_from_thresh(bw_thresh)
        boxes2 = _find_boxes_in_grid(grid2)
        if len(boxes2) > 0:
            grid = grid2
            boxes = boxes2

    if len(boxes) == 0:
        for block_size in [11, 21, 31, 41]:
            for c_val in [5, 10, 15, 20]:
                try:
                    alt_thresh = cv2.adaptiveThreshold(
                        blurred,
                        255,
                        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                        cv2.THRESH_BINARY_INV,
                        block_size,
                        c_val,
                    )
                    alt_grid, _, _ = _extract_grid_from_thresh(alt_thresh)
                    alt_boxes = _find_boxes_in_grid(alt_grid)
                    if len(alt_boxes) > len(boxes):
                        boxes = alt_boxes
                        grid = alt_grid
                        thresh = alt_thresh
                        if len(boxes) > 10:
                            break
                except Exception:
                    continue
            if len(boxes) > 10:
                break

    return boxes, grid, thresh


# ---------------------------------------------------------------------------
# Row grouping + field assignment
# ---------------------------------------------------------------------------
def group_into_rows(boxes, y_tol=None):
    if not boxes:
        return []
    if y_tol is None:
        median_h = np.median([b[3] for b in boxes])
        y_tol = median_h * 0.5
    boxes_sorted = sorted(boxes, key=lambda b: b[1])
    rows, current_row, anchor_y = [], [], None
    for b in boxes_sorted:
        if anchor_y is None or abs(b[1] - anchor_y) <= y_tol:
            current_row.append(b)
            anchor_y = b[1] if anchor_y is None else anchor_y
        else:
            rows.append(sorted(current_row, key=lambda x: x[0]))
            current_row, anchor_y = [b], b[1]
    if current_row:
        rows.append(sorted(current_row, key=lambda x: x[0]))
    return rows


def split_row_by_gaps(row, gap_factor=2.5):
    if len(row) < 2:
        return [row]
    gaps = [row[i + 1][0] - (row[i][0] + row[i][2]) for i in range(len(row) - 1)]
    median_gap = np.median(gaps)
    splits = [i + 1 for i, g in enumerate(gaps) if g > gap_factor * max(median_gap, 5)]
    if not splits:
        return [row]
    groups, prev = [], 0
    for s in splits:
        groups.append(row[prev:s])
        prev = s
    groups.append(row[prev:])
    return groups


def assign_fields(boxes):
    """Map detected boxes to named fields.

    Returns a dict with keys from ``FIELD_NAMES`` (and ``field_<n>`` for any
    extra groups), each a list of ``(x, y, w, h)`` boxes sorted left-to-right.
    """
    rows = group_into_rows(boxes)
    fields = {}
    field_idx = 0
    for row in rows:
        sub_groups = split_row_by_gaps(row)
        for group in sub_groups:
            label = FIELD_NAMES[field_idx] if field_idx < len(FIELD_NAMES) else f"field_{field_idx}"
            fields[label] = sorted(group, key=lambda b: b[0])
            field_idx += 1
    return fields


# ---------------------------------------------------------------------------
# Per-character crop preparation
# ---------------------------------------------------------------------------
def is_empty_box(crop, ink_pixel_threshold=15):
    """True if a character cell holds no significant handwritten ink."""
    if crop is None or crop.size == 0:
        return True
    if crop.ndim == 3:
        crop = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    h, w = crop.shape
    if h < 5 or w < 5:
        return True
    brd = min(4, h // 4, w // 4)
    if h > 2 * brd and w > 2 * brd:  # noqa: SIM108 - ternary would exceed line length
        inner = crop[brd : h - brd, brd : w - brd].copy()
    else:
        inner = crop.copy()
    if inner.size == 0:
        return True
    inv = cv2.bitwise_not(inner)
    _, binary = cv2.threshold(inv, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = np.ones((2, 2), np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    # Any connected component above the ink threshold means the cell is non-empty.
    return all(stats[i, cv2.CC_STAT_AREA] < ink_pixel_threshold for i in range(1, num_labels))


def _erase_border(crop, border=3):
    """Zero the outer border so printed cell lines don't leak into the crop."""
    h, w = crop.shape[:2]
    if h <= 2 * border or w <= 2 * border:
        return crop
    crop = crop.copy()
    crop[:border, :] = 0
    crop[-border:, :] = 0
    crop[:, :border] = 0
    crop[:, -border:] = 0
    return crop


def binarize_crop(gray_crop, img_size=IMG_SIZE):
    """Normalise a grayscale character crop to the model's input distribution.

    CLAHE -> Otsu (inverted, ink=255) -> erase border -> resize -> scale to
    ink=+1.0 / background=-1.0. Returns a ``float32`` ``(img_size, img_size)``
    array ready to be wrapped as a ``(1, 1, H, W)`` tensor.
    """
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    eq = clahe.apply(gray_crop)
    _, binary = cv2.threshold(eq, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    binary = _erase_border(binary, border=3)
    resized = cv2.resize(binary, (img_size, img_size), interpolation=cv2.INTER_CUBIC)
    return (resized.astype(np.float32) / 255.0 - 0.5) / 0.5
