"""Rotas públicas do cliente (/api/public/upload/{token}), sem login."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlmodel import Session, select

from app.doc_types import LABELS
from app.models import Case, Organization, UploadedFile, _now
from app.pipeline.rules import missing_docs
from app.pipeline.run import load_docs
from app.routers.deps import Ctx, aware, files_of, get_ctx, get_session

router = APIRouter(prefix="/api/public")


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", Path(name or "arquivo.pdf").name)[:120] or "arquivo.pdf"


def case_by_token(s: Session, token: str) -> Case:
    case = s.exec(select(Case).where(Case.upload_token == token)).first()
    if case is None:
        raise HTTPException(404, "Link de envio inválido.")
    if aware(case.token_expires_at) < _now():
        raise HTTPException(410, "Este link de envio expirou. Peça um novo link ao escritório.")
    return case


@router.get("/upload/{token}")
def upload_status(token: str, s: Session = Depends(get_session)) -> dict[str, Any]:
    case = case_by_token(s, token)
    org = s.get(Organization, case.org_id)
    processed = case.status not in ("RECEBENDO", "PROCESSANDO")
    missing = missing_docs(load_docs(s, case.id), case.scenario) if processed else []
    return {
        "org_name": org.name if org else "",
        "case_name": case.name,
        "scenario": str(case.scenario),
        "status": case.status,
        "files": [{"filename": f.filename, "processed": f.processed} for f in files_of(s, case.id)],
        "missing": [LABELS[t] for t in missing],
    }


@router.post("/upload/{token}")
async def upload(token: str, background: BackgroundTasks, files: list[UploadFile] = File(...),
                 s: Session = Depends(get_session), ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    case = case_by_token(s, token)
    limit = int(ctx.settings.max_upload_mb * 1024 * 1024)

    # lê e valida tudo antes de gravar qualquer arquivo
    received: list[tuple[str, bytes]] = []
    for up in files:
        name = _safe_name(up.filename or "")
        content = await up.read(limit + 1)
        if len(content) > limit:
            raise HTTPException(413, f"O arquivo {name} passa do limite de "
                                     f"{ctx.settings.max_upload_mb:g} MB.")
        received.append((name, content))

    known = {f.sha256 for f in files_of(s, case.id)}
    updir = ctx.case_dir(case) / "uploads"
    accepted: list[str] = []
    duplicates: list[str] = []
    rejected: list[str] = []
    for name, content in received:
        if not content.startswith(b"%PDF"):
            rejected.append(name)
            continue
        sha = hashlib.sha256(content).hexdigest()
        if sha in known:
            duplicates.append(name)
            continue
        known.add(sha)
        updir.mkdir(parents=True, exist_ok=True)
        path = updir / f"{sha[:12]}_{name}"
        path.write_bytes(content)
        s.add(UploadedFile(case_id=case.id, filename=name, path=str(path), sha256=sha))
        accepted.append(name)
    if not accepted and not duplicates:
        detail = ", ".join(rejected) if rejected else "nenhum arquivo"
        raise HTTPException(415, f"Apenas arquivos PDF são aceitos ({detail}).")
    if accepted:
        case.status = "PROCESSANDO"
        s.add(case)
    s.commit()
    if accepted:
        background.add_task(ctx.run_pipeline, case.id)
    return {"accepted": accepted, "duplicates": duplicates, "rejected": rejected, "status": case.status}
