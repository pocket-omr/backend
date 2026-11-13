# Pocket OMR backend

Service layer for Pocket OMR. Accepts an uploaded sheet, stores it, calls the computer
vision service and returns the grading result to the clients.

> **Status:** scaffold. The application code is not published in this repository yet.

## Configuration

Copy `.env.example` to `.env`:

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | Database connection string |
| `SECRET_KEY` | Signing key for sessions and tokens |
| `MODEL_SERVICE_URL` | Base URL of the CV model service |
| `STORAGE_PATH` | Directory for uploaded sheets and generated crops |

## Expected pipeline

The backend does not do any computer vision itself. For each upload it should:

1. Validate the image and store it under `STORAGE_PATH`.
2. Forward it to `MODEL_SERVICE_URL` for rectification and segmentation.
3. Receive the region crops, bubble decisions and recognised characters.
4. Score the sheet against the answer key and persist the result.
5. Return the result to the mobile and web clients.

## Related repositories

- [mobile](https://github.com/pocket-omr/mobile): Flutter client
- [frontend](https://github.com/pocket-omr/frontend): web client
- `cv-model` (private): the computer vision service

## Project

Pluridisciplinary project, ESI-SBA, 2025/2026. Supervisor: Pr. Oussama Serhane.
