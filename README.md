# Pocket OMR Backend v2

New backend for the Pocket OMR exam management system. Serves both the React web frontend and the Flutter mobile app.

## Tech Stack

- **Framework**: FastAPI (Python 3.12)
- **Database**: PostgreSQL 16
- **ORM**: SQLAlchemy 2.0 (async) + asyncpg
- **Migrations**: Alembic
- **Auth**: JWT (python-jose) + bcrypt (passlib)
- **PDF**: ReportLab
- **Container**: Docker + Docker Compose

## Setup

### 1. Environment

```bash
cp .env.example .env
# Edit .env if needed (defaults work for local dev)
```

### 2. Start Database

```bash
docker compose up -d pgsql
```

This starts PostgreSQL on port **5434** with database `omr_db_v2`.

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

This starts both PostgreSQL and the API server.

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
- `POST /refresh` - Refresh tokens
- `POST /logout` - Logout (Bearer token)

### Exams (`/api/v1/exams`)
- `POST /` - Create exam (Bearer token)
- `GET /` - List user's exams (Bearer token)
- `GET /{id}` - Get exam (Bearer token)
- `PUT /{id}` - Update exam (Bearer token)
- `DELETE /{id}` - Delete exam (Bearer token)

### PDF
- `POST /generate-pdf` - Generate PDF sheet (no auth)
