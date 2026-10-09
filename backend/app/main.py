from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import get_settings
from app.database import create_db_and_tables


@asynccontextmanager
async def lifespan(_: FastAPI):
    create_db_and_tables()
    yield


settings = get_settings()
app = FastAPI(title="AccessPath Go API", version="0.1.0", lifespan=lifespan)
origins = {settings.frontend_origin, "http://localhost:3000", "http://127.0.0.1:3000"}
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(origins),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.include_router(router)


@app.get("/health")
def health():
    ollama_configured = bool(settings.ollama_base_url and settings.ollama_model)
    return {"status": "ok", "ollama_configured": ollama_configured}
