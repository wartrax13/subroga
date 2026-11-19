"""Configuração por variáveis de ambiente (com `backend/.env` opcional)."""
from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent.parent
_EPHEMERAL_SECRET: str | None = None


def load_dotenv(path: Path | None = None) -> None:
    """Parser simples KEY=VALUE. Não sobrescreve variáveis já definidas; ignora comentários."""
    path = path or BACKEND_DIR / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def _jwt_secret() -> str:
    global _EPHEMERAL_SECRET
    secret = os.environ.get("SUBROGA_JWT_SECRET", "").strip()
    if secret:
        return secret
    if _EPHEMERAL_SECRET is None:
        _EPHEMERAL_SECRET = secrets.token_urlsafe(48)
        log.warning("SUBROGA_JWT_SECRET não definido: usando segredo aleatório em memória "
                    "(os logins expiram quando o servidor reiniciar).")
    return _EPHEMERAL_SECRET


@dataclass
class Settings:
    storage_dir: Path = field(default_factory=lambda: BACKEND_DIR / "storage")
    db_url: str = ""
    jwt_secret: str = field(default_factory=lambda: secrets.token_urlsafe(48))
    cors_origins: list[str] = field(default_factory=list)
    max_upload_mb: float = 50.0
    frontend_dir: Path = field(default_factory=lambda: BACKEND_DIR.parent / "frontend")

    def __post_init__(self) -> None:
        self.storage_dir = Path(self.storage_dir)
        self.frontend_dir = Path(self.frontend_dir)
        if not self.db_url:
            self.db_url = f"sqlite:///{self.storage_dir / 'app.db'}"


def get_settings() -> Settings:
    """Lê o ambiente no momento da chamada (depois de `load_dotenv`)."""
    storage = Path(os.environ.get("SUBROGA_STORAGE_DIR") or BACKEND_DIR / "storage")
    origins = [o.strip() for o in os.environ.get("SUBROGA_CORS_ORIGINS", "").split(",") if o.strip()]
    return Settings(
        storage_dir=storage,
        db_url=os.environ.get("SUBROGA_DB_URL", ""),
        jwt_secret=_jwt_secret(),
        cors_origins=origins,
        max_upload_mb=float(os.environ.get("SUBROGA_MAX_UPLOAD_MB") or 50),
        frontend_dir=Path(os.environ.get("SUBROGA_FRONTEND_DIR") or BACKEND_DIR.parent / "frontend"),
    )
