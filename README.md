# Pocket OMR Backend v2

New backend for the Pocket OMR exam management system. Serves both the React web frontend and the Flutter mobile app.

## Tech Stack

- **Framework**: FastAPI (Python 3.12)
- **Database**: PostgreSQL 16
- **ORM**: SQLAlchemy 2.0 (async) + asyncpg
- **Migrations**: Alembic
- **Auth**: JWT (python-jose) + bcrypt (passlib)
- **PDF**: ReportLab
- **Object storage**: MinIO (scanned answer sheets)
- **Container**: Docker + Docker Compose

## Setup

### 1. Environment

```bash
cp .env.example .env
# Edit .env if needed (defaults work for local dev)
```

### 2. Start Database & Object Storage

```bash
docker compose up -d pgsql minio
```

This starts PostgreSQL on port **5434** (database `omr_db_v2`) and MinIO on **9000**
(S3 API) / **9001** (web console). The `omr-sheets` bucket is created automatically on the
first sheet upload.

### 3. Python Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 4. Run Migrations

```bash
PYTHONPATH=. alembic upgrade head
```

### 5. Run Server

```bash
fastapi dev app/main.py --port 8000
```

Or with uvicorn directly:

```bash
uvicorn app.main:app --reload --port 8000
```

### Full Stack (Docker)

```bash
docker compose up -d
```

This starts PostgreSQL, MinIO, and the API server.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `API_PORT` | `8000` | Server port |
| `POSTGRES_USER` | `postgres` | DB user |
| `POSTGRES_PASSWORD` | `postgres` | DB password |
| `POSTGRES_DB` | `omr_db_v2` | DB name |
| `POSTGRES_HOST` | `localhost` | DB host |
| `POSTGRES_PORT` | `5434` | DB port |
| `JWT_SECRET_KEY` | *required* | JWT signing secret |
| `SMTP_EMAIL` | `""` | Gmail address for password-reset emails (optional) |
| `SMTP_PASSWORD` | `""` | Gmail app password (optional) |
| `MINIO_ENDPOINT` | `localhost:9000` | MinIO host:port |
| `MINIO_ACCESS_KEY` | `minioadmin` | MinIO access key (root user) |
| `MINIO_SECRET_KEY` | `minioadmin` | MinIO secret key (root password) |
| `MINIO_BUCKET` | `omr-sheets` | Bucket for scanned sheets |
| `MINIO_SECURE` | `false` | Use HTTPS for MinIO |

## Migrations

```bash
# Create a new migration
PYTHONPATH=. alembic revision -m "describe change"

# Apply all migrations
PYTHONPATH=. alembic upgrade head

# Rollback one step
PYTHONPATH=. alembic downgrade -1

# Check current version
PYTHONPATH=. alembic current
```

## Tests

Requires a running PostgreSQL instance.

```bash
# Run all tests
PYTHONPATH=. pytest -v

# Run specific test file
PYTHONPATH=. pytest tests/test_auth.py -v

# Run a single test
PYTHONPATH=. pytest tests/test_auth.py::test_login_success -v
```

## Linting

```bash
ruff check app/ tests/
ruff check --fix app/ tests/
ruff format app/ tests/
```

## API Endpoints

### Health
- `GET /health` - Health check
- `GET /health/db` - Database health check

### Auth (`/api/v1/auth`)
- `POST /register` - Register new teacher
- `POST /login` - Login
- `GET /me` - Get current user (Bearer token)
- `PUT /me` - Update profile: first/last name, email (Bearer token)
- `POST /refresh` - Refresh tokens
- `POST /logout` - Logout (Bearer token)
- `POST /send-reset-code` - Email a password-reset code
- `POST /verify-reset-code` - Verify a reset code
- `POST /reset-password` - Reset password with a valid code

### Exams (`/api/v1/exams`) — all Bearer token
- `POST /` - Create exam
- `GET /` - List user's exams
- `GET /{id}` - Get exam
- `PUT /{id}` - Update exam
- `DELETE /{id}` - Delete exam

#### Mobile-facing
- `GET /recent` - Recently updated exams
- `GET /to-correct` - Exams still being corrected (`?search=`)
- `GET /history` - All exams with avg score/confidence (`?search=`)
- `GET /{id}/mobile` - Mobile-shaped exam with student results
- `POST /{id}/upload-images` - Upload scanned sheets (images or a `.zip` of images);
  each image is stored in MinIO and recorded as a graded or pending `StudentSubmission`
- `POST /{id}/regrade` - Re-run grading on all pending submissions

### Students (`/api/v1/students`)
- `POST /parse-file` - Parse an uploaded `.xlsx`/`.xls`/`.csv` roster → student names

### PDF
- `POST /generate-pdf` - Generate PDF sheet: `question_sheet` | `grid_sheet` | `correction_sheet` (no auth)

## OMR Grading

Scanned-sheet grading is wired end-to-end (upload → MinIO storage → `StudentSubmission`
lifecycle → aggregate stats), with the recognition model itself left as a single seam:

- Implement `grade_sheet(image_bytes, exam) -> GradingResult` in
  `app/services/grading.py`. The answer key is `exam.questions[*].correct_answer`.
- Until it's implemented, `grade_sheet` raises `GradingNotAvailable`, so uploaded sheets are
  stored and recorded as **pending** submissions.
- After implementing it, new uploads grade automatically; grade already-stored pending sheets
  with `POST /api/v1/exams/{id}/regrade`.
