from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.schemas.pdf import PDFRequest
from app.services.pdf import (
    generate_correction_sheet,
    generate_grid_sheet,
    generate_question_sheet,
)

router = APIRouter(tags=["pdf"])

GENERATORS = {
    "question_sheet": generate_question_sheet,
    "grid_sheet": generate_grid_sheet,
    "correction_sheet": generate_correction_sheet,
}


@router.post("/generate-pdf")
async def generate_pdf(payload: PDFRequest):
    generator = GENERATORS.get(payload.type)
    if not generator:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid type '{payload.type}'. Must be one of: {list(GENERATORS.keys())}",
        )

    pdf_bytes = generator(payload)
    filename = f"{payload.form.title or 'exam'}_{payload.type}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
