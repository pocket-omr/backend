# Backend for Pocket-OMR

## Stack

- Docker + Docker Compose
- FastAPI with Python 3.12
- PostgreSQL 16 (alpine)
- MinIO (S3-compatible object storage)

## Services and ports

- API: host port from `API_PORT` -> container 80
- PostgreSQL: `5432` (exposed in override compose file)
- MinIO API: `9000`
- MinIO Console: `9001`

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

Use this flow if you are a frontend or mobile teammate and need a local backend to integrate against.

1. Create local env file:

```bash
cp .env.example .env
```

2. Start the backend stack (API + Postgres + MinIO):

```bash
docker compose up --build -d
```

3. Check that API is running:

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status":"ok"}
```

4. Base URL to use in clients:

- Frontend running on same machine: `http://localhost:8000`
- Physical phone on same Wi-Fi: `http://<your-computer-lan-ip>:8000`
- Android emulator: `http://10.0.2.2:8000`
- iOS simulator: `http://localhost:8000`

5. Optional check in browser:

- API root: `http://localhost:8000/`
- MinIO console: `http://localhost:9001`

6. View logs when debugging integration:

```bash
docker compose logs -f api
```

7. Stop services:

```bash
docker compose down
```

## Dev Container

This repo includes VS Code Dev Container configuration.

1. Open folder in VS Code
2. Press Ctrl + Shift + P
3. Run “Dev Containers: Reopen in Container”
4. Work inside the api service container

## Testing
For this project we're using `pytest` as a testing framework.
Use these commands to run tests:
```bash
docker compose run --rm api pytest
```
For verbose output:
```bash
docker compose run --rm api pytest -v
```
If the containers are already running, you can also execute tests in the running api service like this:
```bash
docker compose exec api pytest
```

Consider making a `/tests` directory to organize your test suite.

## Database Migrations (Alembic)

This project uses Alembic for schema migrations.

Initialize Alembic (run once):
1. `docker compose run --rm api alembic init alembic`
2. This creates:
   - `alembic.ini`
   - `alembic/env.py`
   - `alembic/versions/`

Create a migration:
1. `docker compose run --rm api alembic revision -m "describe change"`

Apply migrations:
1. `docker compose run --rm api alembic upgrade head`

Rollback one migration:
1. `docker compose run --rm api alembic downgrade -1`

Useful checks:
1. `docker compose run --rm api alembic current`
2. `docker compose run --rm api alembic history`

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