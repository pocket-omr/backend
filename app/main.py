from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.endpoints import health, pdf
from app.api.v1.router import api_v1_router

app = FastAPI(title="Pocket OMR API v2", version="2.0.0")

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

# /api/v1/auth/*, /api/v1/exams/*
app.include_router(api_v1_router)
