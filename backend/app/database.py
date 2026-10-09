from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings, resolve_database_path

database_url = resolve_database_path(get_settings().database_url)
engine = create_engine(database_url, connect_args={"check_same_thread": False} if database_url.startswith("sqlite") else {})


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
