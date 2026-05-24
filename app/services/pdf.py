import io
import json
import string

import qrcode
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from app.schemas.pdf import PDFRequest

CHOICE_LABELS = list(string.ascii_uppercase)
PAGE_W, PAGE_H = A4  # 595.27, 841.89 points

# Margins
MARGIN_LEFT = 25 * mm
MARGIN_RIGHT = 25 * mm
MARGIN_TOP = 20 * mm
MARGIN_BOTTOM = 20 * mm
CONTENT_W = PAGE_W - MARGIN_LEFT - MARGIN_RIGHT

# Alignment marker dimensions
MARKER_SIZE = 4 * mm                # standard square marker side length
ORIGIN_MARKER_ARM = 6.4 * mm        # L-shape arm length (~1.6× MARKER_SIZE)
ORIGIN_MARKER_THICKNESS = 4 * mm    # L-shape arm thickness (same as MARKER_SIZE)

# Marker X positions — flush near page edges
MARKER_X_LEFT = 5 * mm
MARKER_X_RIGHT = PAGE_W - 5 * mm - MARKER_SIZE

# Colors
DARK = colors.HexColor("#333333")
GREY = colors.HexColor("#888888")
LIGHT_GREY = colors.HexColor("#CCCCCC")


def _draw_section_markers(c: canvas.Canvas, y: float, is_origin: bool):
    """Draw a left/right marker pair at the given y position.

    The distinct marker is always top-left — find it to recover page
    rotation and flip.

    Args:
        c: ReportLab canvas.
        y: Bottom edge y-coordinate of the marker row.
        is_origin: If True, draw the top-left marker as an L-shape
            instead of a plain square.
    """
    m = MARKER_SIZE
    c.setFillColor(colors.black)

    if is_origin:
        # L-shape: horizontal arm extends right, vertical arm extends down.
        # Top-left corner of the L is at (MARKER_X_LEFT, y + ORIGIN_MARKER_ARM).
        arm = ORIGIN_MARKER_ARM
        t = ORIGIN_MARKER_THICKNESS
        # Horizontal arm (top part of L)
        c.rect(MARKER_X_LEFT, y + arm - t, arm, t, fill=1, stroke=0)
        # Vertical arm (left part of L)
        c.rect(MARKER_X_LEFT, y, t, arm, fill=1, stroke=0)
    else:
        c.rect(MARKER_X_LEFT, y, m, m, fill=1, stroke=0)

    # Right marker is always a plain square
    c.rect(MARKER_X_RIGHT, y, m, m, fill=1, stroke=0)



def _draw_student_info(c: canvas.Canvas, y_start: float) -> float:
    """Draw student info section. Returns y position after section."""
    y = y_start
    x = MARGIN_LEFT
    m = MARKER_SIZE

    # Top markers for student info section (origin L-shape at top-left)
    _draw_section_markers(c, y + 2, is_origin=True)
    y -= 6

    # Instruction text
    c.setFont("Helvetica", 8)
    c.setFillColor(GREY)
    c.drawString(x, y, "Write clearly in UPPERCASE letters, "
                 "as it appears in your student ID.")
    y -= 16

    box_w = 22
    box_h = 26
    gap = 2
    label_font_size = 9

    def draw_boxes(label, bx, by, count):
        c.setFont("Helvetica", label_font_size)
        c.setFillColor(DARK)
        c.drawString(bx, by + box_h + 4, label)
        c.setStrokeColor(DARK)
        c.setLineWidth(0.8)
        for i in range(count):
            c.rect(bx + i * (box_w + gap), by, box_w, box_h, fill=0)

    # First Name + Group on same row
    name_count = 16
    draw_boxes("First Name", x, y - box_h, name_count)
    group_x = PAGE_W - MARGIN_RIGHT - 2 * (box_w + gap)
    draw_boxes("Group", group_x, y - box_h, 2)
    y -= box_h + 24

    # Last Name
    draw_boxes("Last Name", x, y - box_h, name_count)
    y -= box_h + 24

    # Registration Number
    draw_boxes("Registration Number", x, y - box_h, 12)
    y -= box_h + 12

    # Bottom markers for student info section
    _draw_section_markers(c, y, is_origin=False)
    y -= m + 4

    return y


def _draw_bubble_grid(
    c: canvas.Canvas,
    y_start: float,
    num_questions: int,
    num_choices: int,
    grid_layout: str = "Linear",
):
    """Draw bubble grid with markers. Supports 3-col or 2-col layout."""
    y = y_start

    # Instruction
    c.setFont("Helvetica", 9)
    c.setFillColor(DARK)
    c.drawString(MARGIN_LEFT, y, "Fill the circles completely using a black pen.")
    y -= 6

    # Separator line
    c.setStrokeColor(LIGHT_GREY)
    c.setLineWidth(0.5)
    c.line(MARGIN_LEFT, y, PAGE_W - MARGIN_RIGHT, y)
    y -= 10

    # Determine column count based on layout
    is_double = grid_layout == "Double Column"
    col_count = 2 if is_double else 3
    rows_per_col = -(-num_questions // col_count)  # ceil division

    col_width = CONTENT_W / col_count
    bubble_r = 4.5
    bubble_gap = 18
    row_height = 22
    m = MARKER_SIZE

    # Top markers — flush with edges, just above the first bubble row
    _draw_section_markers(c, y, is_origin=False)

    # Start grid below top markers
    grid_top = y - m - 4

    for col in range(col_count):
        col_x = MARGIN_LEFT + col * col_width
        for row in range(rows_per_col):
            q_num = col * rows_per_col + row + 1
            if q_num > num_questions:
                break

            row_y = grid_top - row * row_height

            # Question number
            c.setFont("Helvetica", 9)
            c.setFillColor(DARK)
            c.drawString(col_x + 4, row_y - 3, str(q_num))

            # Bubbles
            for ci in range(num_choices):
                bx = col_x + 30 + ci * bubble_gap
                by = row_y
                c.setStrokeColor(DARK)
                c.setLineWidth(0.6)
                c.setFillColor(colors.white)
                c.circle(bx, by, bubble_r, fill=0, stroke=1)

    # Bottom of grid
    grid_bottom = grid_top - rows_per_col * row_height - 4

    # Bottom markers — flush with edges, just below last bubble row
    _draw_section_markers(c, grid_bottom, is_origin=False)

    return grid_bottom - m - 4


def _draw_how_to_fill(c: canvas.Canvas, y_pos: float):
    """Draw the HOW TO FILL instruction box at bottom-left."""
    x = MARGIN_LEFT
    y = y_pos
    box_w = 300
    box_h = 60

    # Header bar
    c.setFillColor(colors.HexColor("#4a4a4a"))
    c.rect(x, y, box_w, 16, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 7)
    c.drawString(x + 6, y + 4, "HOW TO FILL")
    y -= 1

    # Content area
    c.setStrokeColor(LIGHT_GREY)
    c.setFillColor(colors.white)
    c.rect(x, y - box_h, box_w, box_h, fill=1, stroke=1)

    # Wrong examples
    inner_y = y - 18
    c.setFont("Helvetica", 7)
    c.setFillColor(GREY)
    c.drawCentredString(x + 40, inner_y + 22, "WRONG")
    c.drawCentredString(x + 120, inner_y + 22, "CORRECT")

    # Partial circle (wrong)
    c.setStrokeColor(GREY)
    c.setLineWidth(0.6)
    c.circle(x + 25, inner_y + 6, 6, fill=0, stroke=1)
    c.setFillColor(colors.HexColor("#BBBBBB"))
    c.circle(x + 25, inner_y + 6, 3, fill=1, stroke=0)

    # Cross overflow (wrong)
    c.setStrokeColor(GREY)
    c.circle(x + 55, inner_y + 6, 6, fill=0, stroke=1)
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(GREY)
    c.drawCentredString(x + 55, inner_y + 2, "\u00d7")

    # Arrow
    c.setFillColor(GREY)
    c.drawCentredString(x + 80, inner_y + 3, "\u2192")

    # Correct (filled)
    c.setFillColor(colors.HexColor("#333333"))
    c.circle(x + 120, inner_y + 6, 6, fill=1, stroke=0)

    # Labels
    c.setFont("Helvetica", 5.5)
    c.setFillColor(GREY)
    c.drawCentredString(x + 25, inner_y - 8, "Partial")
    c.drawCentredString(x + 55, inner_y - 8, "Cross Overflow")
    c.drawCentredString(x + 120, inner_y - 8, "Filled")

    # Rules
    rules = [
        "Use black pen only",
        "Fill the circle completely",
        "Do not cross the boundary",
        "One answer per question",
    ]
    rule_x = x + 155
    rule_y = inner_y + 14
    c.setFont("Helvetica", 7)
    c.setFillColor(DARK)
    for rule in rules:
        c.drawString(rule_x, rule_y, f"\u2022 {rule}")
        rule_y -= 10


def _draw_qr_code(c: canvas.Canvas, y_pos: float, data: dict):
    """Draw QR code at bottom-right."""
    qr = qrcode.QRCode(version=1, box_size=2, border=1)
    qr.add_data(json.dumps(data, default=str))
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    img_buf = io.BytesIO()
    img.save(img_buf, format="PNG")
    img_buf.seek(0)

    from reportlab.lib.utils import ImageReader

    qr_size = 22 * mm
    qr_x = PAGE_W - MARGIN_RIGHT - qr_size
    qr_y = y_pos - qr_size + 16
    c.drawImage(
        ImageReader(img_buf), qr_x, qr_y, qr_size, qr_size
    )


# ── Public API ──────────────────────────────────────────


def generate_grid_sheet(req: PDFRequest) -> bytes:
    """Generate the OMR answer grid sheet matching the exam template design."""
    buf = io.BytesIO()
    form = req.form
    questions = req.questions
    num_q = len(questions)
    num_choices = max((len(q.choices) for q in questions), default=6)
    c_pdf = canvas.Canvas(buf, pagesize=A4)

    # Title section
    y = PAGE_H - MARGIN_TOP

    # Exam title
    title = form.title or "Exam"
    c_pdf.setFont("Helvetica-Bold", 16)
    c_pdf.setFillColor(DARK)
    c_pdf.drawCentredString(PAGE_W / 2, y, title)
    y -= 18

    # Duration
    if form.duration:
        c_pdf.setFont("Helvetica", 11)
        c_pdf.setFillColor(GREY)
        c_pdf.drawCentredString(PAGE_W / 2, y, f"Duration: {form.duration}")
    y -= 20

    # Separator line
    c_pdf.setStrokeColor(LIGHT_GREY)
    c_pdf.setLineWidth(0.5)
    c_pdf.line(MARGIN_LEFT, y, PAGE_W - MARGIN_RIGHT, y)
    y -= 14

    # Student info
    y = _draw_student_info(c_pdf, y)
    y -= 8

    # Separator line
    c_pdf.setStrokeColor(LIGHT_GREY)
    c_pdf.line(MARGIN_LEFT, y, PAGE_W - MARGIN_RIGHT, y)
    y -= 14

    # Bubble grid
    y = _draw_bubble_grid(c_pdf, y, num_q, num_choices, req.grid_layout)

    # Bottom section
    bottom_y = MARGIN_BOTTOM + 20
    _draw_how_to_fill(c_pdf, bottom_y + 60)

    # QR code with exam metadata
    qr_data = {
        "title": form.title,
        "module": form.module,
        "questions": num_q,
        "choices": num_choices,
    }
    _draw_qr_code(c_pdf, bottom_y + 60, qr_data)

    c_pdf.save()
    return buf.getvalue()


def generate_question_sheet(req: PDFRequest) -> bytes:
    """Generate question sheet with questions and choices listed."""
    buf = io.BytesIO()
    form = req.form
    questions = req.questions
    qpp = int(form.questions_per_page) if form.questions_per_page else 20
    total_pages = max(1, -(-len(questions) // qpp))

    c_pdf = canvas.Canvas(buf, pagesize=A4)

    for pi in range(total_pages):
        page_qs = questions[pi * qpp : (pi + 1) * qpp]
        y = PAGE_H - MARGIN_TOP

        # Title
        c_pdf.setFont("Helvetica-Bold", 14)
        c_pdf.setFillColor(DARK)
        title = form.title or "Exam"
        c_pdf.drawCentredString(PAGE_W / 2, y, title)
        y -= 16

        if form.duration:
            c_pdf.setFont("Helvetica", 10)
            c_pdf.setFillColor(GREY)
            c_pdf.drawCentredString(
                PAGE_W / 2, y, f"Duration: {form.duration}"
            )
        y -= 14

        # Info line
        c_pdf.setFont("Helvetica", 8)
        c_pdf.setFillColor(GREY)
        info_parts = []
        if form.module:
            info_parts.append(f"Module: {form.module}")
        if form.university:
            info_parts.append(f"University: {form.university}")
        info_parts.append(f"Page {pi + 1}/{total_pages}")
        c_pdf.drawString(MARGIN_LEFT, y, "  |  ".join(info_parts))
        y -= 10

        c_pdf.setStrokeColor(LIGHT_GREY)
        c_pdf.line(MARGIN_LEFT, y, PAGE_W - MARGIN_RIGHT, y)
        y -= 14

        # Instructions
        if form.instructions and pi == 0:
            c_pdf.setFont("Helvetica-Oblique", 8)
            c_pdf.setFillColor(GREY)
            c_pdf.drawString(MARGIN_LEFT, y, form.instructions)
            y -= 16

        # Questions
        for qi, q in enumerate(page_qs):
            gi = pi * qpp + qi
            q_text = q.text or f"Question {gi + 1}"

            if y < MARGIN_BOTTOM + 40:
                break

            c_pdf.setFont("Helvetica-Bold", 10)
            c_pdf.setFillColor(DARK)
            c_pdf.drawString(MARGIN_LEFT, y, f"Q{gi + 1}. {q_text}")
            y -= 16

            c_pdf.setFont("Helvetica", 9)
            col_width = CONTENT_W / 2
            for ci, ch in enumerate(q.choices):
                label = CHOICE_LABELS[ci] if ci < 26 else str(ci + 1)
                text = ch.text or f"Choice {label}"
                col = ci % 2
                if col == 0 and ci > 0:
                    y -= 14
                cx = MARGIN_LEFT + 15 + col * col_width
                c_pdf.drawString(cx, y, f"{label}. {text}")
            y -= 22

        if pi < total_pages - 1:
            c_pdf.showPage()

    c_pdf.save()
    return buf.getvalue()


def generate_correction_sheet(req: PDFRequest) -> bytes:
    """Generate correction/answer key sheet."""
    buf = io.BytesIO()
    form = req.form
    questions = req.questions
    qpp = int(form.questions_per_page) if form.questions_per_page else 20
    total_pages = max(1, -(-len(questions) // qpp))
    max_choices = max((len(q.choices) for q in questions), default=4)

    c_pdf = canvas.Canvas(buf, pagesize=A4)

    for pi in range(total_pages):
        page_qs = questions[pi * qpp : (pi + 1) * qpp]
        start_idx = pi * qpp
        y = PAGE_H - MARGIN_TOP

        # Title
        c_pdf.setFont("Helvetica-Bold", 14)
        c_pdf.setFillColor(DARK)
        c_pdf.drawCentredString(
            PAGE_W / 2, y, f"{form.title or 'Exam'} - Correction Key"
        )
        y -= 16

        c_pdf.setFont("Helvetica", 9)
        c_pdf.setFillColor(GREY)
        c_pdf.drawString(
            MARGIN_LEFT, y,
            f"For teacher use only.  |  Page {pi + 1}/{total_pages}"
        )
        y -= 14

        c_pdf.setStrokeColor(LIGHT_GREY)
        c_pdf.line(MARGIN_LEFT, y, PAGE_W - MARGIN_RIGHT, y)
        y -= 20

        # Table header
        col_labels = CHOICE_LABELS[:max_choices]
        q_col_w = 40
        choice_col_w = 35
        answer_col_w = 50
        table_x = MARGIN_LEFT
        header_h = 22
        row_h = 20

        # Header background
        table_w = q_col_w + max_choices * choice_col_w + answer_col_w
        c_pdf.setFillColor(colors.HexColor("#0B96D9"))
        c_pdf.rect(table_x, y - header_h, table_w, header_h, fill=1)

        c_pdf.setFont("Helvetica-Bold", 9)
        c_pdf.setFillColor(colors.white)
        c_pdf.drawString(table_x + 8, y - 15, "Q")
        for ci, label in enumerate(col_labels):
            cx = table_x + q_col_w + ci * choice_col_w + choice_col_w / 2
            c_pdf.drawCentredString(cx, y - 15, label)
        c_pdf.drawCentredString(
            table_x + q_col_w + max_choices * choice_col_w + answer_col_w / 2,
            y - 15, "Answer"
        )
        y -= header_h

        # Rows
        for qi, q in enumerate(page_qs):
            gi = start_idx + qi
            row_y = y - row_h

            # Alternating bg
            if qi % 2 == 1:
                c_pdf.setFillColor(colors.HexColor("#f4faff"))
                c_pdf.rect(table_x, row_y, table_w, row_h, fill=1, stroke=0)

            # Grid lines
            c_pdf.setStrokeColor(colors.HexColor("#ceedf8"))
            c_pdf.setLineWidth(0.3)
            c_pdf.line(table_x, row_y, table_x + table_w, row_y)

            # Q number
            c_pdf.setFont("Helvetica-Bold", 9)
            c_pdf.setFillColor(DARK)
            c_pdf.drawString(table_x + 8, row_y + 6, f"Q{gi + 1}")

            # Choice indicators
            for ci in range(max_choices):
                cx = table_x + q_col_w + ci * choice_col_w + choice_col_w / 2
                cy = row_y + row_h / 2
                if ci < len(q.choices):
                    if q.correct == ci:
                        c_pdf.setFillColor(colors.HexColor("#0FE2A6"))
                        c_pdf.circle(cx, cy, 7, fill=1, stroke=0)
                        c_pdf.setFillColor(colors.white)
                        c_pdf.setFont("Helvetica-Bold", 8)
                        c_pdf.drawCentredString(cx, cy - 3, "\u2713")
                    else:
                        c_pdf.setStrokeColor(colors.HexColor("#ceedf8"))
                        c_pdf.setLineWidth(0.5)
                        c_pdf.circle(cx, cy, 7, fill=0, stroke=1)

            # Answer label
            has_answer = (
                q.correct is not None and q.correct < len(q.choices)
            )
            answer = CHOICE_LABELS[q.correct] if has_answer else "-"
            c_pdf.setFont("Helvetica-Bold", 9)
            c_pdf.setFillColor(DARK)
            ans_x = (
                table_x + q_col_w
                + max_choices * choice_col_w + answer_col_w / 2
            )
            c_pdf.drawCentredString(ans_x, row_y + 6, answer)

            y -= row_h

        # Border
        c_pdf.setStrokeColor(colors.HexColor("#ceedf8"))
        c_pdf.setLineWidth(0.5)
        total_h = header_h + len(page_qs) * row_h
        c_pdf.rect(
            table_x,
            y,
            table_w,
            total_h,
            fill=0, stroke=1,
        )

        if pi < total_pages - 1:
            c_pdf.showPage()

    c_pdf.save()
    return buf.getvalue()
