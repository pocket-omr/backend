from fastapi import FastAPI


app = FastAPI(title="Pocket OMR Backend")


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "Pocket OMR backend is running"}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
