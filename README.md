# Backend for Pocket-OMR

## Stack

- Docker + Docker Compose
- FastAPI with Python 3.12
- PostgreSQL 16 (alpine)
- MinIO (S3-compatible object storage)

## Project structure

```text
app/
   api/v1/endpoints/   # route modules
   core/               # settings and shared core config
   db/                 # DB engine/session primitives
   models/             # SQLAlchemy models
   schemas/            # Pydantic schemas
   services/           # business logic layer
   main.py             # FastAPI app entrypoint
tests/
   test_health.py      # smoke tests
```

## App File Tree Explained

This backend follows a layered structure so each concern stays in one place.

- `app/main.py`: FastAPI application entrypoint. Creates the app instance and mounts versioned routers.
- `app/api/`: HTTP layer only.
- `app/api/v1/router.py`: Aggregates v1 endpoint routers under `/api/v1`.
- `app/api/v1/endpoints/`: Route handlers grouped by domain (for example `health.py`, `auth.py`).
- `app/core/`: Application-wide settings and security helpers.
- `app/db/`: Database session and engine setup (`get_db` dependency lives here).
- `app/models/`: SQLAlchemy ORM models and enums (database shape).
- `app/schemas/`: Pydantic request/response models (API contracts).
- `app/services/`: Business logic used by endpoints; keeps route handlers thin.
- `tests/`: API and unit tests.

Typical request flow:

1. Endpoint in `app/api/v1/endpoints/*` receives request.
2. Endpoint validates payload via `app/schemas/*`.
3. Endpoint calls business logic in `app/services/*`.
4. Service reads/writes `app/models/*` using session from `app/db/session.py`.
5. Endpoint returns schema response from `app/schemas/*`.

Where to add new code:

- New endpoint: `app/api/v1/endpoints/`
- New service/business logic: `app/services/`
- New ORM model: `app/models/`
- New request/response schema: `app/schemas/`

## Services and ports

- Dev API (venv): `http://localhost:8000`
- PostgreSQL: `5432` (exposed in override compose file)
- MinIO API: `9000`
- MinIO Console: `9001`
- Production-style API container (optional local check): host port from `API_PORT` -> container `80`

## Required environment variables

Create a `.env` file in the project root with at least:

```ini
API_PORT=8000
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=pocket_omr
MINIO_ROOT_USER=minio
MINIO_ROOT_PASSWORD=minio123
```

## Quick start

In development, API is run from local venv. Docker Compose is used only for dependencies (`pgsql` and `minio`).

1. Create local env file:

```bash
cp .env.example .env
```

2. Start dependencies:

```bash
docker compose up -d
```

3. Create and activate venv:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

4. Install dependencies:

```bash
pip install -r requirements.txt
```

5. Run API locally:

```bash
fastapi dev app/main.py --port 8000
```

6. Check that API is running:

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status":"ok"}
```

7. Base URL to use in clients:

- Frontend running on same machine: `http://localhost:8000`
- Physical phone on same Wi-Fi: `http://<your-computer-lan-ip>:8000`
- Android emulator: `http://10.0.2.2:8000`
- iOS simulator: `http://localhost:8000`

8. Optional check in browser:

- API root: `http://localhost:8000/`
- MinIO console: `http://localhost:9001`

9. View dependency logs when debugging integration:

```bash
docker compose logs -f pgsql minio
```

10. Stop services:

```bash
docker compose down
```

## Production-style container check (optional)

Use this only when you want to validate the API container path locally.

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d
```

Stop production-style stack:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml down
```

## Linting and Formatting

This project uses Ruff for linting and code formatting.

Check lint issues:
```bash
ruff check app/
```

Fix auto-fixable issues:
```bash
ruff check --fix app/
```

Format code:
```bash
ruff format app/
```

Combined check and format:
```bash
ruff check --fix app/ && ruff format app/
```

### Pre-commit hook setup (optional)

Run linting automatically before each commit:

```bash
pip install pre-commit
pre-commit install
```

From now on, `ruff check --fix` and `ruff format` will run on staged files before each commit. To bypass (not recommended):
```bash
git commit --no-verify
```

## Testing

For this project we're using `pytest` as a testing framework.
Use these commands to run tests:
```bash
pytest
```
For verbose output:
```bash
pytest -v
```

Run only smoke tests:
```bash
pytest tests/test_health.py -v
```

## Database Migrations (Alembic)

This project uses Alembic for schema migrations.

Initialize Alembic (run once):
1. `alembic init alembic`
2. This creates:
   - `alembic.ini`
   - `alembic/env.py`
   - `alembic/versions/`

Create a migration:
1. `alembic revision -m "describe change"`

Apply migrations:
1. `alembic upgrade head`

Rollback one migration:
1. `alembic downgrade -1`

Useful checks:
1. `alembic current`
2. `alembic history`

## Git Workflow

This repo uses:

- `main` as protected production branch
- `dev` as integration branch for ongoing development
- `feat/<feature-name>` for feature work (branched from `dev`)

Open a Pull Request from `dev` -> `main` when you want to release the current state.

### Daily feature workflow

1. Sync `dev` locally:

```bash
git checkout dev
git pull origin dev
```

2. Create a feature branch from `dev`:

```bash
git checkout -b feat/<feature-name>
```

3. Commit and push your feature:

```bash
git add .
git commit -m "feat: <short description>"
git push -u origin feat/<feature-name>
```

4. Open Pull Request from `feat/<feature-name>` -> `dev`.

5. After merge, update local `dev` and optionally delete the feature branch:

```bash
git checkout dev
git pull origin dev
git branch -d feat/<feature-name>
git push origin --delete feat/<feature-name>
```

### Releasing to production

When `dev` is stable:

1. Open Pull Request from `dev` -> `main`
2. Pass checks and required approvals
3. Merge to release

### Branch protection

- `main`: require PR, approvals, and status checks; block direct push
- `dev`: require PR and approvals once team starts active feature development

## Notes

- Alembic usage is documented above; initialize it when you are ready to start versioning schema changes.
- Pin dependency versions in requirements.txt for reproducible builds.
- In development, do not run the API container; run API from local venv only.