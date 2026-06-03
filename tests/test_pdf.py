import pytest


SAMPLE_PDF_REQUEST = {
    "type": "question_sheet",
    "form": {
        "title": "Test Exam",
        "module": "Math",
        "university": "Test Uni",
        "department": "CS",
        "date": "2026-06-15",
        "duration": "1h",
        "numQuestions": "2",
        "choices": "4",
        "questionsPerPage": "20",
        "instructions": "",
    },
    "questions": [
        {
            "text": "Q1?",
            "choices": [{"text": "A"}, {"text": "B"}, {"text": "C"}, {"text": "D"}],
            "correct": [0],
        },
        {
            "text": "Q2?",
            "choices": [{"text": "A"}, {"text": "B"}, {"text": "C"}, {"text": "D"}],
            "correct": [1],
        },
    ],
    "checkboxType": "Fill",
    "gridLayout": "Linear",
}


@pytest.mark.asyncio
async def test_generate_question_sheet(client):
    resp = await client.post("/generate-pdf", json=SAMPLE_PDF_REQUEST)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert len(resp.content) > 100  # PDF has content


@pytest.mark.asyncio
async def test_generate_grid_sheet(client):
    payload = {**SAMPLE_PDF_REQUEST, "type": "grid_sheet"}
    resp = await client.post("/generate-pdf", json=payload)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"


@pytest.mark.asyncio
async def test_generate_correction_sheet(client):
    payload = {**SAMPLE_PDF_REQUEST, "type": "correction_sheet"}
    resp = await client.post("/generate-pdf", json=payload)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"


@pytest.mark.asyncio
async def test_invalid_pdf_type(client):
    payload = {**SAMPLE_PDF_REQUEST, "type": "invalid_type"}
    resp = await client.post("/generate-pdf", json=payload)
    assert resp.status_code == 400
