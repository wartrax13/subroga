"""Engine SQLite/SQLModel. Nada é criado no import: chame `make_engine` (o app faz no startup)."""
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app import models  # noqa: F401  (registra as tabelas)
from app.config import BACKEND_DIR

STORAGE_DIR = BACKEND_DIR / "storage"

SessionFactory = Callable[[], Session]


def make_engine(url: str) -> Engine:
    """Cria a engine e as tabelas que faltarem."""
    if url.startswith("sqlite:///") and ":memory:" not in url:
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
    SQLModel.metadata.create_all(engine)
    return engine


def session_factory(engine: Engine) -> SessionFactory:
    return lambda: Session(engine)
