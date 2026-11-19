"""Contexto da aplicação, dependências (sessão, usuário atual) e serializadores."""
from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app import auth, db
from app.config import Settings
from app.doc_types import LABELS
from app.models import Case, CaseFactsRow, Organization, UploadedFile, User
from app.pipeline.rules import compute_alerts, missing_docs, prescription_deadline
from app.pipeline.run import Steps, load_docs, process_case
from app.schemas import CaseFacts


@dataclass
class Ctx:
    settings: Settings
    steps_factory: Callable[[], Steps]
    today: Callable[[], date]
    engine: Engine | None = None
    _guard: threading.Lock = field(default_factory=threading.Lock)

    def get_engine(self) -> Engine:
        with self._guard:
            if self.engine is None:
                self.engine = db.make_engine(self.settings.db_url)
            return self.engine

    def session(self) -> Session:
        return Session(self.get_engine())

    @property
    def storage(self) -> Path:
        return self.settings.storage_dir

    def case_dir(self, case: Case) -> Path:
        return self.storage / "orgs" / case.org_id / "cases" / case.id

    def run_pipeline(self, case_id: str) -> None:
        process_case(case_id, self.session, self.storage, steps=self.steps_factory())


def get_ctx(request: Request) -> Ctx:
    return request.app.state.ctx


def get_session(ctx: Ctx = Depends(get_ctx)) -> Iterator[Session]:
    with ctx.session() as s:
        yield s


_bearer = HTTPBearer(auto_error=False)


def _user_from_token(token: str | None, s: Session, ctx: Ctx) -> User:
    if not token:
        raise HTTPException(401, "Autenticação necessária.", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = auth.decode_token(token, ctx.settings.jwt_secret)
    except auth.AuthError as exc:
        raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc
    user = s.get(User, payload["sub"])
    if user is None or user.org_id != payload["org"]:
        raise HTTPException(401, "Token inválido.", headers={"WWW-Authenticate": "Bearer"})
    return user


def current_user(cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
                 s: Session = Depends(get_session), ctx: Ctx = Depends(get_ctx)) -> User:
    return _user_from_token(cred.credentials if cred else None, s, ctx)


def current_user_or_query(request: Request, cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
                          s: Session = Depends(get_session), ctx: Ctx = Depends(get_ctx)) -> User:
    """Para <img src>: aceita também `?access_token=` (navegador não envia header em imagens)."""
    token = cred.credentials if cred else request.query_params.get("access_token")
    return _user_from_token(token, s, ctx)


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Apenas administradores podem gerenciar a equipe.")
    return user


def get_org_case(s: Session, user: User, case_id: str) -> Case:
    case = s.get(Case, case_id)
    if case is None or case.org_id != user.org_id:
        raise HTTPException(404, "Caso não encontrado.")
    return case


# ---------------------------------------------------------------- serialização

def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def user_out(u: User) -> dict[str, Any]:
    return {"id": u.id, "name": u.name, "email": u.email, "role": u.role}


def org_out(o: Organization) -> dict[str, Any]:
    return {"id": o.id, "name": o.name}


def files_of(s: Session, case_id: str) -> list[UploadedFile]:
    return list(s.exec(select(UploadedFile).where(UploadedFile.case_id == case_id)
                       .order_by(UploadedFile.id)).all())


def facts_row(s: Session, case_id: str) -> CaseFactsRow | None:
    return s.get(CaseFactsRow, case_id)


def case_summary(s: Session, case: Case, today: date, _cache: dict[str, Any] | None = None) -> dict[str, Any]:
    docs = load_docs(s, case.id)
    row = facts_row(s, case.id)
    facts: CaseFacts | None = row.to_facts() if row else None
    alerts = compute_alerts(docs, facts, case.scenario, today)
    presc = next((a for a in alerts if a.kind == "PRESCRIPTION"), None)
    prescription: dict[str, Any] | None = None
    if presc is not None:
        deadline = prescription_deadline(facts.data_pagamento.value) if facts else None
        prescription = {
            "kind": presc.kind, "severity": presc.severity, "message": presc.message,
            "deadline": deadline.isoformat() if deadline else None,
            "days_left": (deadline - today).days if deadline else None,
        }
    missing = missing_docs(docs, case.scenario)
    out = {
        "id": case.id, "name": case.name, "scenario": str(case.scenario), "status": case.status,
        "created_at": iso(case.created_at), "upload_token": case.upload_token,
        "upload_url": f"/enviar/{case.upload_token}", "token_expires_at": iso(case.token_expires_at),
        "n_files": len(files_of(s, case.id)), "n_docs": len(docs),
        "prescription": prescription,
        "critical_alerts": sum(a.severity == "critical" for a in alerts),
        "missing_count": len(missing),
    }
    if _cache is not None:
        _cache.update(docs=docs, row=row, facts=facts, alerts=alerts, missing=missing)
    return out


def case_detail(s: Session, case: Case, today: date) -> dict[str, Any]:
    c: dict[str, Any] = {}
    out = case_summary(s, case, today, c)
    documents = []
    for n, d in enumerate(c["docs"], start=1):
        item = d.model_dump(mode="json")
        item["source_file"] = Path(d.source_file).name  # não expõe o caminho do servidor
        item |= {"label": LABELS[d.doc_type], "number": n, "page_count": len(d.pages)}
        documents.append(item)
    out |= {
        "error": case.error,
        "files": [{"id": f.id, "filename": f.filename, "sha256": f.sha256, "processed": f.processed,
                   "duplicate_pages": f.duplicate_pages} for f in files_of(s, case.id)],
        "documents": documents,
        "facts": c["facts"].model_dump(mode="json") if c["facts"] else None,
        "edited_fields": c["row"].edited() if c["row"] else [],
        "missing": [{"doc_type": t.value, "label": LABELS[t]} for t in c["missing"]],
        "alerts": [a.model_dump() for a in c["alerts"]],
    }
    return out
