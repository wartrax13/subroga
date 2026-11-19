"""Tabelas SQLModel. LogicalDoc e CaseFacts são guardados como JSON."""
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import Field, SQLModel

from app.doc_types import Scenario
from app.schemas import CaseFacts, LogicalDoc

STATUSES = ("RECEBENDO", "PROCESSANDO", "PENDENTE", "REVISAR", "PRONTO")
ROLES = ("admin", "advogado")
TOKEN_TTL = timedelta(days=30)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id() -> str:
    return uuid.uuid4().hex[:12]


def new_upload_token() -> str:
    return secrets.token_urlsafe(24)


class Organization(SQLModel, table=True):
    id: str = Field(default_factory=_id, primary_key=True)
    name: str
    created_at: datetime = Field(default_factory=_now)


class User(SQLModel, table=True):
    id: str = Field(default_factory=_id, primary_key=True)
    org_id: str = Field(foreign_key="organization.id", index=True)
    name: str
    email: str = Field(index=True, unique=True)  # sempre minúsculo
    password_hash: str
    role: str = "advogado"
    created_at: datetime = Field(default_factory=_now)


class Case(SQLModel, table=True):
    id: str = Field(default_factory=_id, primary_key=True)
    org_id: str = Field(foreign_key="organization.id", index=True)
    name: str = ""
    scenario: Scenario
    status: str = "RECEBENDO"
    error: str | None = None
    # pedido de (re)processamento pendente; consumido pelo worker que segura a trava do caso
    run_requested: bool = False
    upload_token: str = Field(default_factory=new_upload_token, index=True, unique=True)
    token_expires_at: datetime = Field(default_factory=lambda: _now() + TOKEN_TTL)
    created_at: datetime = Field(default_factory=_now)


class UploadedFile(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    case_id: str = Field(foreign_key="case.id", index=True)
    filename: str
    path: str
    sha256: str
    processed: bool = False
    duplicate_pages: int = 0
    page_hashes: str = "[]"  # JSON list[str], usado na deduplicação por página
    created_at: datetime = Field(default_factory=_now)

    def hashes(self) -> list[str]:
        return list(json.loads(self.page_hashes or "[]"))


class Document(SQLModel, table=True):
    id: str = Field(primary_key=True)  # = LogicalDoc.id
    case_id: str = Field(foreign_key="case.id", index=True)
    data: str  # LogicalDoc.model_dump_json()

    def to_logical(self) -> LogicalDoc:
        return LogicalDoc.model_validate_json(self.data)


class CaseFactsRow(SQLModel, table=True):
    case_id: str = Field(foreign_key="case.id", primary_key=True)
    data: str  # CaseFacts.model_dump_json()
    edited_fields: str = "[]"  # JSON list[str]: campos editados manualmente (reprocessar não sobrescreve)

    def to_facts(self) -> CaseFacts:
        return CaseFacts.model_validate_json(self.data)

    def edited(self) -> list[str]:
        return list(json.loads(self.edited_fields or "[]"))
