import io
import zipfile

import pytest

from app.services import exam as exam_service
from app.services import storage
from app.services.grading import GradingResult
from tests.conftest import register_user


SAMPLE_EXAM = {
    "form": {
        "title": "Final Exam 2026",
        "module": "Mathematics",
        "university": "University of Tlemcen",
        "department": "L2 Computer Science",
        "date": "2026-06-15",
        "duration": "1h30",
        "numQuestions": "2",
        "choices": "4",
        "questionsPerPage": "20",
        "instructions": "Use a black pen only.",
    },
    "questions": [
        {
            "text": "What is 2+2?",
            "choices": [{"text": "3"}, {"text": "4"}, {"text": "5"}, {"text": "6"}],
            "correct": 1,
        },
        {
            "text": "What is 3*3?",
            "choices": [{"text": "6"}, {"text": "9"}, {"text": "12"}, {"text": "15"}],
            "correct": 1,
        },
    ],
    "checkboxType": "Fill",
    "gridLayout": "Linear",
    "students": ["Alice Smith", "Bob Jones"],
}


async def _auth_header(client):
    data = await register_user(client)
    return {"Authorization": f"Bearer {data['access_token']}"}


@pytest.mark.asyncio
async def test_create_exam(client):
    headers = await _auth_header(client)
    resp = await client.post("/api/v1/exams", json=SAMPLE_EXAM, headers=headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["form"]["title"] == "Final Exam 2026"
    assert len(data["questions"]) == 2
    assert data["questions"][0]["correct"] == 1
    assert len(data["questions"][0]["choices"]) == 4
    assert data["checkboxType"] == "Fill"
    assert data["gridLayout"] == "Linear"
    assert data["students"] == ["Alice Smith", "Bob Jones"]


@pytest.mark.asyncio
async def test_list_exams(client):
    headers = await _auth_header(client)
    await client.post("/api/v1/exams", json=SAMPLE_EXAM, headers=headers)
    resp = await client.get("/api/v1/exams", headers=headers)
    assert resp.status_code == 200
    exams = resp.json()
    assert len(exams) >= 1
    assert exams[0]["form"]["title"] == "Final Exam 2026"


@pytest.mark.asyncio
async def test_get_exam(client):
    headers = await _auth_header(client)
    create_resp = await client.post("/api/v1/exams", json=SAMPLE_EXAM, headers=headers)
    exam_id = create_resp.json()["id"]
    resp = await client.get(f"/api/v1/exams/{exam_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == exam_id


@pytest.mark.asyncio
async def test_update_exam(client):
    headers = await _auth_header(client)
    create_resp = await client.post("/api/v1/exams", json=SAMPLE_EXAM, headers=headers)
    exam_id = create_resp.json()["id"]

    updated = SAMPLE_EXAM.copy()
    updated["form"] = {**SAMPLE_EXAM["form"], "title": "Updated Exam"}
    updated["students"] = ["Charlie Brown"]

    resp = await client.put(f"/api/v1/exams/{exam_id}", json=updated, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["form"]["title"] == "Updated Exam"
    assert data["students"] == ["Charlie Brown"]


@pytest.mark.asyncio
async def test_delete_exam(client):
    headers = await _auth_header(client)
    create_resp = await client.post("/api/v1/exams", json=SAMPLE_EXAM, headers=headers)
    exam_id = create_resp.json()["id"]

    resp = await client.delete(f"/api/v1/exams/{exam_id}", headers=headers)
    assert resp.status_code == 204

    resp2 = await client.get(f"/api/v1/exams/{exam_id}", headers=headers)
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_exam_requires_auth(client):
    resp = await client.get("/api/v1/exams")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_exam_isolation_between_users(client):
    # User 1 creates exam
    h1 = await _auth_header(client)
    await client.post("/api/v1/exams", json=SAMPLE_EXAM, headers=h1)

    # User 2 should see no exams
    h2 = await _auth_header(client)
    resp = await client.get("/api/v1/exams", headers=h2)
    assert resp.status_code == 200
    assert len(resp.json()) == 0


# --- Mobile app endpoints ---


@pytest.mark.asyncio
async def test_recent_exams(client):
    headers = await _auth_header(client)
    await client.post("/api/v1/exams", json=SAMPLE_EXAM, headers=headers)
    resp = await client.get("/api/v1/exams/recent", headers=headers)
    assert resp.status_code == 200
    exams = resp.json()
    assert len(exams) >= 1
    assert exams[0]["title"] == "Final Exam 2026"
    assert "totalStudents" in exams[0]
    assert "correctedCount" in exams[0]
    assert "avgConfidence" in exams[0]
    assert "students" in exams[0]


@pytest.mark.asyncio
async def test_to_correct_exams(client):
    headers = await _auth_header(client)
    await client.post("/api/v1/exams", json=SAMPLE_EXAM, headers=headers)
    resp = await client.get("/api/v1/exams/to-correct", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


@pytest.mark.asyncio
async def test_to_correct_search(client):
    headers = await _auth_header(client)
    await client.post("/api/v1/exams", json=SAMPLE_EXAM, headers=headers)
    resp = await client.get("/api/v1/exams/to-correct?search=Final", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1

    resp2 = await client.get("/api/v1/exams/to-correct?search=nonexistent", headers=headers)
    assert resp2.status_code == 200
    assert len(resp2.json()) == 0


@pytest.mark.asyncio
async def test_history_exams(client):
    headers = await _auth_header(client)
    await client.post("/api/v1/exams", json=SAMPLE_EXAM, headers=headers)
    resp = await client.get("/api/v1/exams/history", headers=headers)
    assert resp.status_code == 200
    exams = resp.json()
    assert len(exams) >= 1
    assert "avgScore" in exams[0]
    assert "avgConfidence" in exams[0]
    assert "pendingPages" in exams[0]
    assert "totalPages" in exams[0]


@pytest.mark.asyncio
async def test_get_exam_mobile(client):
    headers = await _auth_header(client)
    create_resp = await client.post("/api/v1/exams", json=SAMPLE_EXAM, headers=headers)
    exam_id = create_resp.json()["id"]
    resp = await client.get(f"/api/v1/exams/{exam_id}/mobile", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == exam_id
    assert data["title"] == "Final Exam 2026"
    assert "totalStudents" in data


# --- Upload / grading pipeline ---


@pytest.fixture
def fake_storage(monkeypatch):
    """Replace MinIO with an in-memory object store so tests don't need a live bucket."""
    store: dict[str, bytes] = {}

    async def _put(key, data, content_type="application/octet-stream"):
        store[key] = data
        return key

    async def _get(key):
        return store[key]

    monkeypatch.setattr(storage, "put_object", _put)
    monkeypatch.setattr(storage, "get_object", _get)
    return store


async def _create_exam(client, headers) -> str:
    resp = await client.post("/api/v1/exams", json=SAMPLE_EXAM, headers=headers)
    return resp.json()["id"]


def _zip_of(images: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in images.items():
            zf.writestr(name, content)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_upload_images_pending(client, fake_storage):
    headers = await _auth_header(client)
    exam_id = await _create_exam(client, headers)

    files = [
        ("files", ("sheet1.jpg", b"fakejpeg1", "image/jpeg")),
        ("files", ("sheet2.jpg", b"fakejpeg2", "image/jpeg")),
    ]
    resp = await client.post(
        f"/api/v1/exams/{exam_id}/upload-images", files=files, headers=headers
    )
    assert resp.status_code == 200
    data = resp.json()
    # Two sheets stored, none graded yet (model not wired in)
    assert data["totalStudents"] == 2
    assert data["correctedCount"] == 0
    assert len(data["students"]) == 2
    assert all(s["score"] == 0 for s in data["students"])
    assert len(fake_storage) == 2

    hist = (await client.get("/api/v1/exams/history", headers=headers)).json()
    row = next(e for e in hist if e["id"] == exam_id)
    assert row["totalPages"] == 2
    assert row["pendingPages"] == 2


@pytest.mark.asyncio
async def test_upload_zip(client, fake_storage):
    headers = await _auth_header(client)
    exam_id = await _create_exam(client, headers)

    archive = _zip_of({"a.png": b"img-a", "b.png": b"img-b", "notes.txt": b"ignore me"})
    files = [("files", ("scans.zip", archive, "application/zip"))]
    resp = await client.post(
        f"/api/v1/exams/{exam_id}/upload-images", files=files, headers=headers
    )
    assert resp.status_code == 200
    # Only the two images count; the .txt member is ignored
    assert resp.json()["totalStudents"] == 2
    assert len(fake_storage) == 2


@pytest.mark.asyncio
async def test_upload_and_grade(client, fake_storage, monkeypatch):
    headers = await _auth_header(client)
    exam_id = await _create_exam(client, headers)

    # Answer key in SAMPLE_EXAM is [1, 1]; this sheet gets both right.
    def fake_grade(image_bytes, exam):
        return GradingResult(answers=[1, 1], confidence=95.0, first_name="Ada", last_name="Lovelace")

    monkeypatch.setattr(exam_service, "grade_sheet", fake_grade)

    files = [("files", ("s.jpg", b"img", "image/jpeg"))]
    resp = await client.post(
        f"/api/v1/exams/{exam_id}/upload-images", files=files, headers=headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["totalStudents"] == 1
    assert data["correctedCount"] == 1
    assert data["avgConfidence"] == 95.0
    student = data["students"][0]
    assert student["score"] == 2
    assert student["maxScore"] == 2
    assert student["firstName"] == "Ada"


@pytest.mark.asyncio
async def test_regrade_pending(client, fake_storage, monkeypatch):
    headers = await _auth_header(client)
    exam_id = await _create_exam(client, headers)

    # First upload while the model is unavailable -> pending
    files = [("files", ("s.jpg", b"img", "image/jpeg"))]
    resp = await client.post(
        f"/api/v1/exams/{exam_id}/upload-images", files=files, headers=headers
    )
    assert resp.json()["correctedCount"] == 0

    # Model lands; re-grade the pending sheet
    monkeypatch.setattr(
        exam_service, "grade_sheet", lambda b, e: GradingResult(answers=[1, 0], confidence=80.0)
    )
    resp = await client.post(f"/api/v1/exams/{exam_id}/regrade", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["correctedCount"] == 1
    assert data["students"][0]["score"] == 1  # one of two correct
    assert data["students"][0]["maxScore"] == 2
