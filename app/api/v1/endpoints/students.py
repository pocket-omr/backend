import csv
import io

from fastapi import APIRouter, File, HTTPException, UploadFile

router = APIRouter(prefix="/students", tags=["students"])

ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv"}


def _get_extension(filename: str) -> str:
    return ("." + filename.rsplit(".", 1)[-1]).lower() if "." in filename else ""


def _has_header(header: list[str]) -> bool:
    joined = " ".join(header)
    return "first" in joined or "last" in joined or "name" in joined


def _detect_columns(header: list[str], ncols: int) -> tuple[int, int | None]:
    first_col = None
    last_col = None
    for i, h in enumerate(header):
        if "first" in h:
            first_col = i
        elif "last" in h:
            last_col = i
    if first_col is None and last_col is None:
        first_col = 0
        last_col = 1 if ncols > 1 else None
    if first_col is None:
        first_col = 0
    if last_col is None and ncols > 1:
        last_col = 1
    return first_col, last_col


def _extract_name(cells: list, first_col: int, last_col: int | None) -> dict[str, str] | None:
    first = str(cells[first_col] or "").strip() if first_col < len(cells) else ""
    last = ""
    if last_col is not None and last_col < len(cells):
        last = str(cells[last_col] or "").strip()
    if first or last:
        return {"first_name": first, "last_name": last}
    return None


def _parse_csv(content: bytes) -> list[dict[str, str]]:
    text = content.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return []

    header = [h.strip().lower() for h in rows[0]]
    header_found = _has_header(header)
    start = 1 if header_found else 0
    if header_found:
        first_col, last_col = _detect_columns(header, len(header))
    else:
        first_col, last_col = 0, (1 if len(rows[0]) > 1 else None)

    students = []
    for row in rows[start:]:
        if not any(cell.strip() for cell in row):
            continue
        entry = _extract_name(row, first_col, last_col)
        if entry:
            students.append(entry)
    return students


def _parse_xlsx(content: bytes) -> list[dict[str, str]]:
    import openpyxl

    wb = openpyxl.load_workbook(
        io.BytesIO(content), read_only=True, data_only=True
    )
    ws = wb.active
    if ws is None:
        return []

    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        return []

    header = [str(h or "").strip().lower() for h in rows[0]]
    header_found = _has_header(header)
    start = 1 if header_found else 0
    ncols = len(rows[0])
    first_col, last_col = (
        _detect_columns(header, ncols) if header_found else (0, 1 if ncols > 1 else None)
    )

    students = []
    for row in rows[start:]:
        cells = [str(c or "") for c in row]
        if not any(c.strip() for c in cells):
            continue
        entry = _extract_name(cells, first_col, last_col)
        if entry:
            students.append(entry)
    return students


def _parse_xls(content: bytes) -> list[dict[str, str]]:
    import xlrd

    wb = xlrd.open_workbook(file_contents=content)
    ws = wb.sheet_by_index(0)
    if ws.nrows == 0:
        return []

    header = [
        str(ws.cell_value(0, c)).strip().lower() for c in range(ws.ncols)
    ]
    header_found = _has_header(header)
    start = 1 if header_found else 0
    first_col, last_col = (
        _detect_columns(header, ws.ncols)
        if header_found
        else (0, 1 if ws.ncols > 1 else None)
    )

    students = []
    for r in range(start, ws.nrows):
        cells = [str(ws.cell_value(r, c)) for c in range(ws.ncols)]
        if not any(c.strip() for c in cells):
            continue
        entry = _extract_name(cells, first_col, last_col)
        if entry:
            students.append(entry)
    return students


@router.post("/parse-file")
async def parse_student_file(file: UploadFile = File(...)):
    """Parse an uploaded Excel/CSV file and return student names."""
    filename = file.filename or ""
    ext = _get_extension(filename)

    if ext not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {allowed}",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="File is empty")

    try:
        if ext == ".csv":
            students = _parse_csv(content)
        elif ext == ".xlsx":
            students = _parse_xlsx(content)
        elif ext == ".xls":
            students = _parse_xls(content)
        else:
            raise HTTPException(status_code=400, detail="Unsupported file type")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Failed to parse file: {e}"
        ) from e

    return {"students": students, "count": len(students)}
