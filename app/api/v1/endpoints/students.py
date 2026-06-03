import csv
import io

from fastapi import APIRouter, File, HTTPException, UploadFile

router = APIRouter(prefix="/students", tags=["students"])

ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv"}

# Header keywords (lowercased, substring match) that identify each column.
# Order matters: first_name is matched before last_name so "prenom" doesn't
# get swallowed by the French "nom" used for last names.
_FIRST_KW = ("first", "prenom", "prénom", "given")
_LAST_KW = ("last", "surname", "family")
_GROUP_KW = ("group", "groupe", "section", "class", "classe")
_REG_KW = ("regist", "matricule", "number", "num", "registration")

# Field order used for positional fallback when the file has no header row.
_FIELDS = ("first_name", "last_name", "group", "registration_number")


def _get_extension(filename: str) -> str:
    return ("." + filename.rsplit(".", 1)[-1]).lower() if "." in filename else ""


def _has_header(header: list[str]) -> bool:
    joined = " ".join(header)
    keywords = ("first", "last", "name", "nom", *_GROUP_KW, *_REG_KW)
    return any(kw in joined for kw in keywords)


def _detect_columns(header: list[str]) -> dict[str, int]:
    """Map each known field to a column index, by header keyword."""
    cols: dict[str, int] = {}
    for i, h in enumerate(header):
        if "first_name" not in cols and any(kw in h for kw in _FIRST_KW):
            cols["first_name"] = i
        elif "last_name" not in cols and (any(kw in h for kw in _LAST_KW) or h == "nom"):
            cols["last_name"] = i
        elif "group" not in cols and any(kw in h for kw in _GROUP_KW):
            cols["group"] = i
        elif "registration_number" not in cols and any(kw in h for kw in _REG_KW):
            cols["registration_number"] = i
    # A lone generic "name" column (no first/last detected) → first_name.
    if "first_name" not in cols:
        for i, h in enumerate(header):
            if "name" in h and i not in cols.values():
                cols["first_name"] = i
                break
    return cols


def _positional_columns(ncols: int) -> dict[str, int]:
    return {field: i for i, field in enumerate(_FIELDS) if i < ncols}


def _extract_student(cells: list, cols: dict[str, int]) -> dict[str, str] | None:
    def val(field: str) -> str:
        i = cols.get(field)
        if i is None or i >= len(cells):
            return ""
        return str(cells[i] or "").strip()

    entry = {
        "first_name": val("first_name"),
        "last_name": val("last_name"),
        "group": val("group"),
        "registration_number": val("registration_number"),
    }
    return entry if any(entry.values()) else None


def _columns_for(header: list[str], ncols: int) -> tuple[dict[str, int], bool]:
    """Return (column map, header_found)."""
    if _has_header(header):
        return _detect_columns(header), True
    return _positional_columns(ncols), False


def _parse_csv(content: bytes) -> list[dict[str, str]]:
    text = content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []

    header = [h.strip().lower() for h in rows[0]]
    cols, header_found = _columns_for(header, len(rows[0]))
    start = 1 if header_found else 0

    students = []
    for row in rows[start:]:
        if not any(cell.strip() for cell in row):
            continue
        entry = _extract_student(row, cols)
        if entry:
            students.append(entry)
    return students


def _parse_xlsx(content: bytes) -> list[dict[str, str]]:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    if ws is None:
        return []

    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        return []

    header = [str(h or "").strip().lower() for h in rows[0]]
    cols, header_found = _columns_for(header, len(rows[0]))
    start = 1 if header_found else 0

    students = []
    for row in rows[start:]:
        cells = [str(c or "") for c in row]
        if not any(c.strip() for c in cells):
            continue
        entry = _extract_student(cells, cols)
        if entry:
            students.append(entry)
    return students


def _parse_xls(content: bytes) -> list[dict[str, str]]:
    import xlrd

    wb = xlrd.open_workbook(file_contents=content)
    ws = wb.sheet_by_index(0)
    if ws.nrows == 0:
        return []

    header = [str(ws.cell_value(0, c)).strip().lower() for c in range(ws.ncols)]
    cols, header_found = _columns_for(header, ws.ncols)
    start = 1 if header_found else 0

    students = []
    for r in range(start, ws.nrows):
        cells = [str(ws.cell_value(r, c)) for c in range(ws.ncols)]
        if not any(c.strip() for c in cells):
            continue
        entry = _extract_student(cells, cols)
        if entry:
            students.append(entry)
    return students


@router.post("/parse-file")
async def parse_student_file(file: UploadFile = File(...)):
    """Parse an uploaded Excel/CSV roster → student records.

    Each record carries first_name, last_name, group and registration_number;
    columns are detected from header keywords, falling back to positional order
    (first, last, group, registration) when the file has no header row.
    """
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
        raise HTTPException(status_code=400, detail=f"Failed to parse file: {e}") from e

    return {"students": students, "count": len(students)}
