"""OMR grading endpoints: upload a roster, then grade scanned answer sheets."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from app.recognition.fuzzy_match import MATCH_WEIGHTS, match
from app.services.exam import _extract_images
from app.services.name_recognition import RecognitionError, grade_sheet_image
from app.services.roster import RosterError, parse_roster

router = APIRouter(tags=["grading"])

_MATCH_FIELDS = list(MATCH_WEIGHTS.keys())


def _get_assets(request: Request):
    assets = getattr(request.app.state, "recognition", None)
    if assets is None:
        err = getattr(request.app.state, "recognition_error", "model not loaded")
        raise HTTPException(
            status_code=503,
            detail=f"Recognition model is unavailable: {err}",
        )
    return assets


def _get_catalogue(request: Request):
    catalogue = getattr(request.app.state, "roster_catalogue", None)
    if not catalogue:
        raise HTTPException(
            status_code=400,
            detail="No roster uploaded yet. POST a roster to /roster first.",
        )
    return catalogue


@router.post("/roster")
async def upload_roster(request: Request, file: UploadFile = File(...)):
    """Upload + validate the teacher roster (.xlsx); store the built catalogue."""
    if not (file.filename or "").lower().endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Roster must be an .xlsx file.")

    data = await file.read()
    try:
        catalogue, column_mapping = parse_roster(data)
    except RosterError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Roster is missing required columns.",
                "required": e.required,
                "found": e.found,
            },
        ) from e
    except Exception as e:  # malformed workbook, etc.
        raise HTTPException(status_code=400, detail=f"Could not parse roster: {e}") from e

    request.app.state.roster_catalogue = catalogue
    return {
        "rows": len(catalogue),
        "column_mapping": column_mapping,
        "fields": _MATCH_FIELDS,
    }


@router.post("/grade")
async def grade(request: Request, files: list[UploadFile] = File(...)):
    """Grade scanned answer sheets against the roster.

    Each upload may be a single image or a ``.zip`` archive of images; zips are
    expanded and non-image members are ignored.
    """
    assets = _get_assets(request)
    catalogue = _get_catalogue(request)

    # Flatten uploads (incl. any .zip archives) into (filename, bytes) images.
    raw = [((u.filename or "sheet.png"), await u.read()) for u in files]
    images = _extract_images(raw)
    if not images:
        raise HTTPException(
            status_code=400,
            detail="No images found in the upload (accepted: images or a .zip of images).",
        )

    results = []
    for filename, data in images:
        suffix = Path(filename).suffix or ".png"
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(data)
                tmp_path = tmp.name

            rec = grade_sheet_image(tmp_path, assets)
            predicted = rec["predicted"]
            qr_data = rec["qr_data"]

            hits = match(predicted, catalogue, top_k=1)
            if hits:
                score, matched = hits[0]
                result = {
                    "filename": filename,
                    "predicted": predicted,
                    "matched": matched,
                    "match_id": matched["registration_number"],
                    "score": round(float(score), 4),
                    "qr_data": qr_data,
                    "match_source": "fuzzy",
                }
            else:
                result = {
                    "filename": filename,
                    "predicted": predicted,
                    "matched": None,
                    "match_id": None,
                    "score": None,
                    "qr_data": qr_data,
                    "match_source": "none",
                }

            if assets.provisional:
                result["warning"] = (
                    "Recognition label mapping is PROVISIONAL — predicted fields "
                    "are not reliable until name_reco_labels.json is finalised."
                )
            results.append(result)

        except RecognitionError as e:
            results.append({"filename": filename, "error": str(e)})
        except Exception as e:  # pragma: no cover - defensive
            results.append({"filename": filename, "error": f"grading failed: {e}"})
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    return {"count": len(results), "results": results}
