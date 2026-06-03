import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.endpoints import grading, health, pdf
from app.api.v1.router import api_v1_router
from app.core.config import settings
from app.services.name_recognition import load_recognition_assets

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load the scripted char model + labels ONCE at startup (not per request).
    app.state.recognition = None
    app.state.recognition_error = None
    app.state.roster_catalogue = None
    try:
        app.state.recognition = load_recognition_assets(settings.recognition_models_dir)
        if app.state.recognition.provisional:
            logger.warning(
                "Recognition assets loaded with a PROVISIONAL label mapping — "
                "predicted names are not reliable until name_reco_labels.json is "
                "finalised (scripts/build_recognition_assets.py --csv/--dropped)."
            )
        else:
            logger.info("Recognition assets loaded.")
    except Exception as e:
        # Don't take down auth/exams/pdf; /grade returns 503 with this message.
        app.state.recognition_error = str(e)
        logger.error("Recognition assets failed to load: %s", e)
    yield


app = FastAPI(title="Pocket OMR API v2", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)

# /health and /health/db at root
app.include_router(health.router)

# /generate-pdf at root (no auth, no /api/v1 prefix)
app.include_router(pdf.router)

# /roster and /grade at root (no auth, no /api/v1 prefix)
app.include_router(grading.router)

# /api/v1/auth/*, /api/v1/exams/*
app.include_router(api_v1_router)
