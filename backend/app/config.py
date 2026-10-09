from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    ors_api_key: str = ""
    ors_base_url: str = "https://api.heigit.org/openrouteservice"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gemma3:4b"
    ollama_text_read_timeout_seconds: float = 180
    ollama_image_read_timeout_seconds: float = 600
    database_url: str = "sqlite:///../data/accesspath.db"
    frontend_origin: str = "http://localhost:3000"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def resolve_database_path(database_url: str) -> str:
    if not database_url.startswith("sqlite:///"):
        return database_url
    path = database_url.removeprefix("sqlite:///")
    if path == ":memory:":
        return database_url
    # Relative SQLite URLs are anchored at backend/, so ../data stays inside the repo.
    resolved = (Path(__file__).resolve().parents[1] / path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{resolved.as_posix()}"
