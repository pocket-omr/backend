"""
MCQ Sheet Scanner & Segmentation Pipeline (v11 — robust, zero-rejection)
========================================================================

Why v11 exists
--------------
v10 rejected 44 sheets and segmented 4 upside-down. Root causes found by
inspecting the actual photos:

  1. EXIF ORIENTATION WAS IGNORED.  `cv2.imread` does not apply the EXIF
     orientation flag.  245 / 261 phone photos carry orientation=6 (shot
     sideways), so v10 processed them rotated 90 deg.  A landscape page
     forced onto a portrait canvas stretches the markers out of square, so
     marker detection fails; and the QR / L-marker orientation logic was
     run on a sideways page, producing the upside-down crops.
     FIX: load with PIL + ImageOps.exif_transpose -> every sheet starts
     in its intended orientation.

  2. MARKER-COLUMN SELECTION WAS FRAGILE.  v10 took the single most
     edge-hugging blob per row band, so a stray blob at the very paper
     edge, or the 3rd answer-bubble column of a 39-question sheet, was
     picked instead of the real marker column.
     FIX: cluster candidates by x, score each cluster by how many members
     it has, how tall it spans, how tightly it lines up and how close it
     hugs the edge, and keep the best cluster per side.

  3. PAGE BOX SOMETIMES INCLUDED THE DESK.  A loose page box shifts the
     marker x-fractions, but the marker-rectangle warp re-rectifies from
     the markers themselves, so a loose page box no longer matters as long
     as the markers are found.

  4. OVER-STRICT SANITY CHECKS rejected valid, slightly-tilted sheets
     (the left/right column span ratio test).  Relaxed, and — crucially —
     a sheet is NEVER rejected: if the marker warp cannot be trusted we
     fall back to the deskewed, correctly-oriented page with fixed
     fractional ROIs (which, by design, nearly coincide with the marker
     canvas), so every sheet always produces personal_info + answers.

Pipeline:
  1. Load with EXIF applied.
  2. Detect the page (HSV paper mask -> minAreaRect) and warp aspect-correct.
  3. Detect marker blobs; select the left & right marker columns robustly.
  4. Resolve orientation (QR first, then L-marker, then marker-row layout).
  5. Warp onto the canonical marker rectangle (anchored on the stable
     student-info rows 1 & 3); fall back to the deskewed page if needed.
  6. Clean + crop fixed ROIs -> uniform personal_info / answers crops.

Usage:
  python sheet_pipeline_v11.py <input_dir> <output_dir>
"""

import sys
import json
import cv2
import numpy as np
from pathlib import Path

try:
    from PIL import Image, ImageOps
    _HAVE_PIL = True
except Exception:                       # pragma: no cover
    _HAVE_PIL = False


# ---------------------------------------------------------------------------
# CANONICAL GEOMETRY  (unchanged from v10 so the ROIs stay calibrated)
# ---------------------------------------------------------------------------
OUT_W, OUT_H = 1240, 1754              # A4 portrait at ~150 dpi

MARK_L = 0.040      # left  column anchor x
MARK_R = 0.973      # right column anchor x
MARK_T = 0.085      # anchor row 1 (student-info top)
MARK_B = 0.373      # anchor row 3 (student-info lower)

ROI_CANVAS = {
    "personal_info": (0.090, 0.075, 0.915, 0.375),
    # right edge pushed 0.900 -> 0.952 (just inside the right marker at 0.973)
    # so the 3rd column / 6th bubble of a 39-question sheet is no longer clipped;
    # bottom pushed 0.740 -> 0.775 so the last row (e.g. row 14 of a 14-row
    # column on a 40-question sheet) is no longer cut off.
    "answers":       (0.105, 0.355, 0.952, 0.775),
}
CROP_SIZE = {
    "personal_info": (1080, 360),
    # width 960 -> 1040 (wider ROI) and height 470 -> 520 (taller ROI) so
    # bubbles stay round and the extra bottom row has room.
    "answers":       (1040, 520),
}

# Black-and-white styling. After binarisation ink is the foreground; we dilate
# it (thicken strokes) before downscaling so the result stays legible/detectable.
# Tuned at the canonical 1240x1754 resolution.
REGION_STYLE = {
    # personal info: thicker, bolder, more legible handwriting/print
    "personal_info": {"close": 2, "dilate": 3, "iters": 1},
    # answers: solidify bubble rings + bolden the printed line numbers.
    # dilate 3 makes the thin numbers clearly legible; drop to 2 if any
    # adjacent filled bubbles start to merge on your sheets.
    "answers":       {"close": 3, "dilate": 3, "iters": 1},
}


# ---------------------------------------------------------------------------
# 0. EXIF-AWARE LOADING   (the single biggest fix)
# ---------------------------------------------------------------------------
def load_image(path):
    """Load an image as BGR with the EXIF orientation already applied."""
    if _HAVE_PIL:
        try:
            im = ImageOps.exif_transpose(Image.open(path))
            return cv2.cvtColor(np.array(im.convert("RGB")), cv2.COLOR_RGB2BGR)
        except Exception:
            pass
    return cv2.imread(str(path))         # fallback


# ---------------------------------------------------------------------------
# 1. PAGE DETECTION  (HSV paper mask -> tight, tilt-aware box)
# ---------------------------------------------------------------------------
def order_points(pts):
    pts = np.asarray(pts, dtype="float32").reshape(4, 2)
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1)
    rect[0] = pts[np.argmin(s)]      # top-left
    rect[2] = pts[np.argmax(s)]      # bottom-right
    rect[1] = pts[np.argmin(d)]      # top-right
    rect[3] = pts[np.argmax(d)]      # bottom-left
    return rect


def _paper_mask(small):
    """White-paper mask: low saturation AND high value. Deliberately does
    NOT OR-in an Otsu mask, because a bright wooden/tiled desk passes Otsu
    and would be merged with the page, inflating the page box."""
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    s, v = hsv[:, :, 1], hsv[:, :, 2]
    mask = cv2.bitwise_and((s < 60).astype(np.uint8) * 255,
                           (v > 120).astype(np.uint8) * 255)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (13, 13))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=3)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k, iterations=2)
    return mask


def find_page(image):
    """Return (ordered_quad, area_ratio). The quad is the min-area rectangle
    of the largest white-paper blob, so the subsequent warp DESKEWS the
    sheet (markers become true vertical columns and separate cleanly from
    the answer bubbles). A 4-corner polygon is used only when it is clearly
    tighter (mild perspective), else the deskewing rectangle wins."""
    H, W = image.shape[:2]
    scale = 1400.0 / max(H, W)
    small = cv2.resize(image, (int(W * scale), int(H * scale)))
    sh, sw = small.shape[:2]

    mask = _paper_mask(small)
    cs, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    full = np.array([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]],
                    dtype="float32")
    if not cs:
        return order_points(full), 0.0
    biggest = max(cs, key=cv2.contourArea)
    area_ratio = cv2.contourArea(biggest) / float(sh * sw)

    # When the paper sits on a same-colour (white/light) background, or under
    # glare, the mask cannot isolate it (it grabs a fragment or the whole
    # frame). In that case use the whole frame and let the marker warp
    # deskew it from the registration squares.
    if area_ratio < 0.45 or area_ratio > 0.97:
        return order_points(full), area_ratio

    rect = cv2.minAreaRect(biggest)
    rect_box = cv2.boxPoints(rect).astype(np.float32)
    rect_area = max(1.0, rect[1][0] * rect[1][1])

    # Use a 4-corner polygon only if it is convex AND nearly as large as the
    # min-area rect (i.e. the page is a clean quad, not a desk-inflated blob).
    quad = rect_box
    peri = cv2.arcLength(biggest, True)
    for eps in (0.02, 0.03, 0.04):
        ap = cv2.approxPolyDP(biggest, eps * peri, True)
        if len(ap) == 4 and cv2.isContourConvex(ap):
            if cv2.contourArea(ap) > 0.92 * rect_area:
                quad = ap.reshape(4, 2).astype(np.float32)
            break

    return order_points(quad / scale), area_ratio


def warp_page(image, quad):
    """Perspective-warp the page onto an aspect-correct canvas. The long
    physical edge maps to OUT_H and the short to OUT_W, so markers stay
    square regardless of how the sheet was rotated in the photo."""
    rect = order_points(quad)
    tl, tr, br, bl = rect
    pw = (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / 2.0
    ph = (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) / 2.0
    out_w, out_h = (OUT_H, OUT_W) if pw > ph else (OUT_W, OUT_H)
    dst = np.array([[0, 0], [out_w - 1, 0],
                    [out_w - 1, out_h - 1], [0, out_h - 1]], dtype="float32")
    M = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(image, M, (out_w, out_h))


# ---------------------------------------------------------------------------
# 2. MARKER DETECTION + ROBUST COLUMN SELECTION
# ---------------------------------------------------------------------------
def detect_markers(page_img):
    """Find candidate registration-marker blobs: solid, square-ish, the
    right size, near the left/right margins."""
    gray = cv2.cvtColor(page_img, cv2.COLOR_BGR2GRAY) if page_img.ndim == 3 \
        else page_img
    H, W = gray.shape
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    th = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                               cv2.THRESH_BINARY_INV, 101, 20)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, k, iterations=2)

    cs, _ = cv2.findContours(th, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    base = 0.019 * min(W, H)             # marker side ~1.9% of page width
    smin, smax = 0.45 * base, 3.2 * base

    cands = []
    for c in cs:
        x, y, w, h = cv2.boundingRect(c)
        if not (smin <= w <= smax and smin <= h <= smax):
            continue
        if max(w, h) / max(1, min(w, h)) > 2.0:
            continue
        roi = th[y:y + h, x:x + w]
        raw_fill = float((roi > 0).sum()) / float(max(1, w * h))
        if raw_fill < 0.55:              # hollow name-grid boxes -> reject
            continue
        # Squareness = contour area / bounding-box area. A SOLID SQUARE
        # marker fills ~0.83-0.9; a round answer bubble fills ~0.65-0.78;
        # the top-left L-marker fills ~0.5-0.65. We keep everything down to
        # a low floor (so the L-marker survives) and use squareness later,
        # in column SELECTION, to prefer the real marker columns over the
        # dense answer-bubble grid on 30-40 question sheets.
        squareness = cv2.contourArea(c) / float(max(1, w * h))
        if squareness < 0.45:
            continue
        cx, cy = x + w / 2.0, y + h / 2.0
        if not (cx < 0.22 * W or cx > 0.78 * W):   # keep margins only
            continue
        cands.append({"cx": cx, "cy": cy, "w": w, "h": h,
                      "extent": raw_fill, "squareness": squareness})
    return cands, (W, H)


def _select_column(side_cands, side, W, H):
    """From one side's candidates, pick the real marker column.

    Cluster by x; the winning cluster is the one that best looks like a
    column of registration markers: several members, a tall vertical span,
    tightly aligned in x, and hugging the page edge.
    """
    if not side_cands:
        return []
    side_cands = sorted(side_cands, key=lambda m: m["cx"])
    clusters = [[side_cands[0]]]
    for m in side_cands[1:]:
        cmean = np.mean([c["cx"] for c in clusters[-1]])
        if abs(m["cx"] - cmean) < 0.035 * W:
            clusters[-1].append(m)
        else:
            clusters.append([m])

    def score(cl):
        cxs = [c["cx"] for c in cl]
        cys = [c["cy"] for c in cl]
        n = len(cl)
        yspan = (max(cys) - min(cys)) / H
        cxstd = np.std(cxs) / W
        mean_cx = np.mean(cxs)
        prox = (W - mean_cx) / W if side == "R" else mean_cx / W
        sq = np.mean([c.get("squareness", 0.8) for c in cl])
        # A real marker column has ~4 SQUARE members hugging the edge. Cap
        # the member reward (so a dense answer-bubble grid with 10+ members
        # does not win), weight edge-proximity heavily (markers hug the very
        # paper edge), and reward squareness (markers are square, bubbles
        # are round).
        n_eff = min(n, 4)
        over = max(0, n - 5)
        return (2.0 * n_eff + 4.0 * yspan + 6.0 * sq
                - 16.0 * prox - 12.0 * cxstd - 0.6 * over)

    best = max(clusters, key=score)
    # drop obvious x-outliers from the chosen cluster
    med = np.median([c["cx"] for c in best])
    best = [c for c in best if abs(c["cx"] - med) < 0.03 * W]
    return sorted(best, key=lambda m: m["cy"])


def select_grid(cands, W, H):
    """Return (left_col, right_col) marker lists, sorted top->bottom."""
    left = _select_column([m for m in cands if m["cx"] < 0.45 * W], "L", W, H)
    right = _select_column([m for m in cands if m["cx"] > 0.55 * W], "R", W, H)
    return left, right


# On every template the student-info block has the same proportions: the
# gap from row1 to row2 is ~2.65x the tight row2->row3 gap. This lets us
# reconstruct row1 from rows 2 & 3 when the row-1 square (or the L-marker)
# was not detected.
ROW1_RATIO = 2.65


def identify_rows(col):
    """Return (row2, row3, row1_or_None) for a column sorted top->bottom.

    rows 2 & 3 are the closest-spaced consecutive pair (name / registration
    lines), present on every sheet. row1 is the marker above them, or None
    when it (often the L-marker) was not detected.
    """
    if len(col) < 2:
        return None, None, None
    col = sorted(col, key=lambda m: m["cy"])
    if len(col) == 2:
        return col[0], col[1], None      # assume rows 2 & 3
    gaps = [col[i + 1]["cy"] - col[i]["cy"] for i in range(len(col) - 1)]
    j = int(np.argmin(gaps))             # pair (j, j+1) = rows 2 & 3
    row2, row3 = col[j], col[j + 1]
    row1 = col[j - 1] if j >= 1 else None
    return row2, row3, row1


def _row1_anchor(col, row2, row3):
    """(x, y) of row 1 for a column: the detected marker if present, else
    reconstructed above row 2 using the fixed template proportions."""
    _, _, row1 = identify_rows(col)
    if row1 is not None:
        return row1["cx"], row1["cy"]
    a, b = _fit_x_of_y(col)
    y1 = row2["cy"] - ROW1_RATIO * (row3["cy"] - row2["cy"])
    return a * y1 + b, y1


# ---------------------------------------------------------------------------
# 3. ORIENTATION
# ---------------------------------------------------------------------------
def detect_qr(img):
    """Return (data, (cx_frac, cy_frac)) for the sheet QR, or (None, None)."""
    det = cv2.QRCodeDetector()
    H, W = img.shape[:2]
    for scale in (1.0, 0.6, 0.45, 0.33):
        sm = cv2.resize(img, (max(1, int(W * scale)), max(1, int(H * scale))))
        try:
            data, pts, _ = det.detectAndDecode(sm)
        except cv2.error:
            pts = None
        if pts is None or len(pts) == 0:
            try:
                ok, pts = det.detect(sm)
                if not ok:
                    pts = None
            except cv2.error:
                pts = None
            data = data if 'data' in dir() else ""
        if pts is not None and len(pts) > 0:
            c = np.asarray(pts).reshape(-1, 2).mean(axis=0)
            return data, (float(c[0] / sm.shape[1]), float(c[1] / sm.shape[0]))
    return None, None


def _rot(img, k):
    if k == 90:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    if k == 180:
        return cv2.rotate(img, cv2.ROTATE_180)
    if k == 270:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return img


def resolve_orientation(page):
    """Return (oriented_page, rotation_deg, source, qr_data).

    QR (bottom-right when upright) is the primary, most reliable cue. If no
    QR, use the marker layout: the two marker columns must be vertical
    (page taller than the column gap) and the stable student-info rows
    (1,2,3) must sit in the TOP half. That resolves the 0-vs-180 ambiguity
    without the unreliable L-marker extent heuristic.
    """
    # --- primary: QR ---
    data, c = detect_qr(page)
    if c is not None:
        cx, cy = c
        right, bottom = cx > 0.5, cy > 0.5
        if right and bottom:
            return page, 0, "qr", data
        if (not right) and bottom:
            return _rot(page, 90), 90, "qr", data
        if (not right) and (not bottom):
            return _rot(page, 180), 180, "qr", data
        return _rot(page, 270), 270, "qr", data

    # --- fallback: marker layout, choose rotation putting rows1-3 on top ---
    best = (page, 0, "asis", None)
    best_score = -1e9
    for k in (0, 90, 180, 270):
        rp = _rot(page, k)
        cands, (W, H) = detect_markers(rp)
        left, right = select_grid(cands, W, H)
        if len(left) < 2 and len(right) < 2:
            continue
        col = left if len(left) >= len(right) else right
        r2, r3, _ = identify_rows(col)
        if r2 is None:
            continue
        # student-info rows near the top -> reward; columns vertical -> reward
        top_term = (0.5 - (r2["cy"] / H))           # +ve if rows in top half
        n_term = 0.15 * (len(left) + len(right))
        score = top_term + n_term
        if score > best_score:
            best_score = score
            best = (rp, k, "markers", None)
    return best


# ---------------------------------------------------------------------------
# 4. CANONICAL MARKER-RECTANGLE WARP (+ page fallback)
# ---------------------------------------------------------------------------
def _clean_columns(cands, W, H, sq=0.80, ext=0.72):
    """Left and right columns of CLEAN SQUARE markers only.

    A high squareness cutoff removes every answer bubble (round, <0.80) and
    name-grid letter, leaving the solid registration squares. It also drops
    the top-left L-marker (it is L-shaped, ~0.5-0.75) — that corner is
    reconstructed by extrapolation in warp_to_canonical.
    """
    def col(side):
        if side == "L":
            pool = [m for m in cands if m["cx"] < 0.40 * W]
        else:
            pool = [m for m in cands if m["cx"] > 0.60 * W]
        pool = [m for m in pool
                if m["squareness"] >= sq and m["extent"] >= ext]
        if not pool:
            return []
        # keep the edge-most x-cluster (collinear markers; tolerant of the
        # perspective slant so the whole column stays together)
        edge = min(m["cx"] for m in pool) if side == "L" \
            else max(m["cx"] for m in pool)
        col = [m for m in pool if abs(m["cx"] - edge) < 0.08 * W]
        return sorted(col, key=lambda m: m["cy"])
    return col("L"), col("R")


def _fit_x_of_y(col):
    """Least-squares x = a*y + b for a marker column; returns (a, b)."""
    ys = np.array([m["cy"] for m in col], dtype=float)
    xs = np.array([m["cx"] for m in col], dtype=float)
    if len(col) == 1:
        return 0.0, xs[0]
    a, b = np.polyfit(ys, xs, 1)
    return float(a), float(b)


_DST4 = np.array([[MARK_L * OUT_W, MARK_T * OUT_H],
                  [MARK_R * OUT_W, MARK_T * OUT_H],
                  [MARK_R * OUT_W, MARK_B * OUT_H],
                  [MARK_L * OUT_W, MARK_B * OUT_H]], dtype="float32")


def _solve_anchors(cands, W, H, sq, ext):
    """Two-column solve: return the 4 src anchor points (row1-L, row1-R,
    row3-R, row3-L) or None, for the given squareness/extent thresholds.
    Reconstructs whichever column is missing its (often L-shaped) row 1."""
    L, R = _clean_columns(cands, W, H, sq, ext)
    if len(R) < 2 or len(L) < 2:
        return None, len(L) + len(R)

    l2, l3, _ = identify_rows(L)
    r2, r3, _ = identify_rows(R)
    if None in (r2, r3, l2, l3):
        return None, len(L) + len(R)

    top_l = _row1_anchor(L, l2, l3)      # detected or reconstructed row 1
    top_r = _row1_anchor(R, r2, r3)

    if abs(l3["cy"] - top_l[1]) < 0.08 * H:   # baseline too short
        return None, len(L) + len(R)

    src = np.array([list(top_l), list(top_r),
                    [r3["cx"], r3["cy"]],
                    [l3["cx"], l3["cy"]]], dtype="float32")
    return src, len(L) + len(R)


def _solve_single_column(cands, W, H, sq, ext):
    """One-column fallback: when only one margin's markers were detected,
    place that column's row 1 & row 3 onto their canonical positions with a
    similarity transform (rotation + uniform scale + translation), assuming
    the sheet has little perspective. Returns a 2x3 affine M or None."""
    L, R = _clean_columns(cands, W, H, sq, ext)
    for col, mark_x in ((L, MARK_L), (R, MARK_R)):
        if len(col) < 3:
            continue
        c2, c3, _ = identify_rows(col)
        if c2 is None:
            continue
        c1x, c1y = _row1_anchor(col, c2, c3)   # detected or reconstructed
        if abs(c3["cy"] - c1y) < 0.08 * H:
            continue
        src = np.array([[c1x, c1y], [c3["cx"], c3["cy"]]], dtype="float32")
        dst = np.array([[mark_x * OUT_W, MARK_T * OUT_H],
                        [mark_x * OUT_W, MARK_B * OUT_H]], dtype="float32")
        M, _ = cv2.estimateAffinePartial2D(src, dst)
        if M is not None:
            return M
    return None


def warp_to_canonical(page, cands, W, H):
    """Warp so marker rows 1 & 3 land on the fixed canonical rectangle.

    1) two-column perspective warp (strict squares, then relaxed);
    2) single-column similarity warp when only one margin was detected.
    Returns (canonical, ok, n_markers)."""
    for sq, ext in ((0.80, 0.72), (0.73, 0.60)):
        src, n = _solve_anchors(cands, W, H, sq, ext)
        if src is not None:
            M = cv2.getPerspectiveTransform(src, _DST4)
            canonical = cv2.warpPerspective(page, M, (OUT_W, OUT_H),
                                            flags=cv2.INTER_CUBIC,
                                            borderMode=cv2.BORDER_REPLICATE)
            return canonical, True, n

    for sq, ext in ((0.80, 0.72), (0.73, 0.60)):
        M = _solve_single_column(cands, W, H, sq, ext)
        if M is not None:
            canonical = cv2.warpAffine(page, M, (OUT_W, OUT_H),
                                       flags=cv2.INTER_CUBIC,
                                       borderMode=cv2.BORDER_REPLICATE)
            return canonical, True, -1
    return None, False, 0


def verify_canonical(canonical):
    """Self-consistency check on a warp result. After a CORRECT warp the
    registration squares land on the canonical left/right anchor columns
    (x = MARK_L and x = MARK_R). Re-detect clean squares and require at
    least two on each side to sit on those columns. A degenerate or
    over-keystoned warp fails this, so the caller can try another page
    hypothesis instead of emitting a distorted crop. The count-based test
    is robust to stray answer-bubble detections (which never sit on the
    anchor columns)."""
    cands, (W, H) = detect_markers(canonical)
    sq = [m for m in cands if m["squareness"] >= 0.80 and m["extent"] >= 0.72]
    n_left = sum(1 for m in sq if abs(m["cx"] / W - MARK_L) < 0.05)
    n_right = sum(1 for m in sq if abs(m["cx"] / W - MARK_R) < 0.05)
    return n_left >= 2 and n_right >= 2


def page_to_canonical(page):
    """Fallback: resize the deskewed, oriented page straight onto the
    canonical canvas. The page edges nearly coincide with the marker
    rectangle, so fixed ROIs still land on the right content."""
    return cv2.resize(page, (OUT_W, OUT_H), interpolation=cv2.INTER_CUBIC)


# ---------------------------------------------------------------------------
# 5. CLEANING + SEGMENTATION
# ---------------------------------------------------------------------------
def remove_shadow(image):
    out = []
    for p in cv2.split(image):
        bg = cv2.medianBlur(cv2.dilate(p, np.ones((7, 7), np.uint8)), 21)
        diff = 255 - cv2.absdiff(p, bg)
        out.append(cv2.normalize(diff, None, 0, 255, cv2.NORM_MINMAX,
                                 cv2.CV_8UC1))
    return cv2.merge(out)


def _ink_score(bgr):
    """Per-pixel 'how far from white' map. White paper -> 0, while any ink
    scores high regardless of hue: black ink is dark in every channel, and
    coloured pens (blue / red / green) are dark in at least one channel, so
    255 - min(B,G,R) is large for all of them. This is what makes blue/red/
    green ink collapse to solid black in the binary output."""
    b, g, r = cv2.split(bgr)
    mn = cv2.min(cv2.min(b, g), r)
    return 255 - mn


def clean_image(image):
    """Return (denoised_colour, ink_mask).

    ink_mask is a foreground mask where ink = 255 (white) on a 0 (black)
    background. Saving it inverted gives black ink on white paper, with every
    pen colour rendered as black."""
    no_shadow = remove_shadow(image)
    denoised = cv2.fastNlMeansDenoisingColored(no_shadow, None, 5, 5, 7, 21)
    ink = _ink_score(denoised)
    ink = cv2.GaussianBlur(ink, (3, 3), 0)          # stabilise Otsu a touch
    _, ink_mask = cv2.threshold(ink, 0, 255,
                                cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return denoised, ink_mask


def crop_fraction(image, frac, out_size):
    H, W = image.shape[:2]
    x0, y0, x1, y1 = frac
    X0, Y0 = max(0, int(x0 * W)), max(0, int(y0 * H))
    X1, Y1 = min(W, int(x1 * W)), min(H, int(y1 * H))
    crop = image[Y0:Y1, X0:X1]
    if crop.size == 0:
        return crop
    return cv2.resize(crop, out_size, interpolation=cv2.INTER_CUBIC)


def _style_bw(ink_crop, style):
    """Turn a cropped ink mask (ink=255) into a clean black-on-white image,
    thickening strokes per the region style so it stays bold/detectable after
    the downscale."""
    m = ink_crop
    c = style.get("close", 0)
    if c and c > 1:                                  # close broken rings/strokes
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE,
                             np.ones((c, c), np.uint8))
    d = style.get("dilate", 0)
    if d and d > 1:                                  # thicken (bolder / darker)
        m = cv2.dilate(m, np.ones((d, d), np.uint8),
                      iterations=style.get("iters", 1))
    return cv2.bitwise_not(m)                         # ink -> black, paper -> white


def segment_regions(canonical_color, canonical_ink, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, frac in ROI_CANVAS.items():
        size = CROP_SIZE[name]
        # Primary output: bold black-and-white (blue/red/green ink -> black).
        ink = crop_fraction(canonical_ink, frac, size)
        if ink.size:
            bw = _style_bw(ink, REGION_STYLE.get(name, {}))
            cv2.imwrite(str(out_dir / f"{name}.png"), bw)
            # Extra output for the answers region: the SAME black-and-white
            # crop but WITHOUT the bold thickening (raw ink mask, just
            # inverted to black-on-white). Useful when the un-dilated bubbles
            # are wanted for detection.
            if name == "answers":
                raw = cv2.bitwise_not(ink)
                cv2.imwrite(str(out_dir / f"{name}_raw.png"), raw)
        # Keep the colour crop too, for reference / fallback.
        color = crop_fraction(canonical_color, frac, size)
        if color.size:
            cv2.imwrite(str(out_dir / f"{name}_color.png"), color)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def process_sheet(input_path, output_root, save_debug=False):
    """Process one sheet. ALWAYS produces output (never rejects)."""
    name = Path(input_path).stem
    sheet_out = Path(output_root) / name
    sheet_out.mkdir(parents=True, exist_ok=True)

    image = load_image(input_path)
    if image is None:
        info = {"name": name, "ok": False, "note": "could_not_read_image"}
        return info

    h, w = image.shape[:2]
    full_quad = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]],
                         dtype="float32")
    quad, area_ratio = find_page(image)
    if quad is None:
        quad = full_quad
        area_ratio = 1.0

    # Try the detected page box, then the whole frame. Accept the first warp
    # that passes the self-consistency check; keep any unverified warp only
    # as a last resort before the resize fallback.
    verified = None
    unverified = None
    for q in ([quad, full_quad] if not np.array_equal(quad, full_quad)
              else [full_quad]):
        page = warp_page(image, q)
        page, rotation, osrc, qr = resolve_orientation(page)
        cands, (W, H) = detect_markers(page)
        left, right = select_grid(cands, W, H)
        canonical, ok, n_markers = warp_to_canonical(page, cands, W, H)
        attempt = (page, rotation, osrc, qr, left, right, canonical, n_markers)
        if ok and verify_canonical(canonical):
            verified = attempt
            break
        if ok and unverified is None:
            unverified = attempt
        if unverified is None:
            unverified = (page, rotation, osrc, qr, left, right, None, 0)

    if verified is not None:
        page, rotation, osrc, qr, left, right, canonical, n_markers = verified
        method = "markers"
    else:
        page, rotation, osrc, qr, left, right, canonical, n_markers = unverified
        if canonical is not None:
            method = "markers_unverified"
        else:
            canonical = page_to_canonical(page)
            method = "page_fallback"

    cv2.imwrite(str(sheet_out / "00_original.jpg"), image,
                [cv2.IMWRITE_JPEG_QUALITY, 90])
    cv2.imwrite(str(sheet_out / "01_canonical.png"), canonical)

    cleaned_color, cleaned_ink = clean_image(canonical)
    cv2.imwrite(str(sheet_out / "02_cleaned.png"), cleaned_color)
    # full-page black-on-white preview (ink of any colour rendered black)
    cv2.imwrite(str(sheet_out / "03_binary.png"), cv2.bitwise_not(cleaned_ink))
    segment_regions(cleaned_color, cleaned_ink, sheet_out / "regions")

    if save_debug:
        dbg = sheet_out / "_debug"
        dbg.mkdir(exist_ok=True)
        vis = page.copy()
        for col, color in ((left, (0, 0, 255)), (right, (0, 255, 0))):
            for m in col:
                cv2.rectangle(vis,
                              (int(m["cx"] - m["w"] / 2), int(m["cy"] - m["h"] / 2)),
                              (int(m["cx"] + m["w"] / 2), int(m["cy"] + m["h"] / 2)),
                              color, 3)
        cv2.imwrite(str(dbg / "markers.png"), vis)

    info = {
        "name": name, "ok": True, "method": method,
        "rotation_applied_deg": rotation, "orientation_source": osrc,
        "qr_data": qr, "n_markers_used": n_markers,
        "left_markers": len(left), "right_markers": len(right),
        "page_area_ratio": round(float(area_ratio), 3),
    }
    with open(sheet_out / "report.json", "w") as f:
        json.dump(info, f, indent=2)
    return info


def run(input_dir, output_dir, save_debug=False):
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    exts = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
    files = sorted(p for p in input_dir.rglob("*")
                   if p.suffix.lower() in exts and p.is_file())
    if not files:
        print(f"No image files under {input_dir}")
        return []

    summary = []
    for i, f in enumerate(files, 1):
        info = process_sheet(f, output_dir, save_debug=save_debug)
        summary.append(info)
        tag = info.get("method", info.get("note"))
        print(f"[{i}/{len(files)}] {f.name[:50]:52s} -> {tag} "
              f"rot={info.get('rotation_applied_deg')} "
              f"L{info.get('left_markers')}R{info.get('right_markers')}")

    with open(output_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    n_fb = sum(1 for s in summary if s.get("method") == "page_fallback")
    n_bad = sum(1 for s in summary if not s.get("ok"))
    print(f"\nDone. {len(summary)} sheets, {n_fb} via page-fallback, "
          f"{n_bad} unreadable, 0 rejected.")
    return summary


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python sheet_pipeline_v11.py <input_dir> <output_dir>")
        sys.exit(1)
    run(sys.argv[1], sys.argv[2],
        save_debug=("--debug" in sys.argv))
