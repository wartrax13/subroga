"""Rotas /api/cases/* (autenticadas, escopo da organização)."""
from __future__ import annotations

import json
import shutil
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app.doc_types import DocType, Scenario
from app.models import TOKEN_TTL, Case, CaseFactsRow, Document, UploadedFile, User, _now, new_upload_token
from app.pipeline import normalize
from app.pipeline.rules import missing_docs
from app.pipeline.run import Steps, computed_status, is_processing, load_docs
from app.routers.deps import (
    Ctx, case_detail, case_summary, current_user, current_user_or_query, get_ctx, get_org_case, get_session,
)
from app.schemas import CaseFacts, DossieResult, FactField

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
NOT_EDITABLE = {"scenario", "valores_alternativos"}

router = APIRouter(prefix="/api/cases")


class CaseCreate(BaseModel):
    scenario: Scenario
    name: str = ""


class DocumentPatch(BaseModel):
    doc_type: DocType | None = None
    title: str | None = None


class ApproveIn(BaseModel):
    force: bool = False


def _today(ctx: Ctx) -> date:
    return ctx.today()


@router.post("", status_code=201)
def create_case(body: CaseCreate, user: User = Depends(current_user), s: Session = Depends(get_session),
                ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    case = Case(org_id=user.org_id, scenario=body.scenario, name=body.name.strip())
    s.add(case)
    s.commit()
    s.refresh(case)
    return case_summary(s, case, _today(ctx))


@router.get("")
def list_cases(user: User = Depends(current_user), s: Session = Depends(get_session),
               ctx: Ctx = Depends(get_ctx)) -> list[dict[str, Any]]:
    cases = s.exec(select(Case).where(Case.org_id == user.org_id).order_by(Case.created_at.desc())).all()
    out = [case_summary(s, c, _today(ctx)) for c in cases]
    # prescrição mais próxima primeiro; sem data por último (sort estável mantém mais recentes antes)
    out.sort(key=lambda c: (c["prescription"] is None or c["prescription"]["deadline"] is None,
                            (c["prescription"] or {}).get("deadline") or ""))
    return out


@router.get("/{case_id}")
def get_case(case_id: str, user: User = Depends(current_user), s: Session = Depends(get_session),
             ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    return case_detail(s, get_org_case(s, user, case_id), _today(ctx))


@router.delete("/{case_id}", status_code=204)
def delete_case(case_id: str, user: User = Depends(current_user), s: Session = Depends(get_session),
                ctx: Ctx = Depends(get_ctx)) -> Response:
    case = get_org_case(s, user, case_id)
    if is_processing(case.id):
        raise HTTPException(409, "O caso está sendo processado. Tente novamente em instantes.")
    for model in (Document, UploadedFile):
        for row in s.exec(select(model).where(model.case_id == case.id)).all():
            s.delete(row)
    facts = s.get(CaseFactsRow, case.id)
    if facts is not None:
        s.delete(facts)
    s.delete(case)
    s.commit()
    shutil.rmtree(ctx.case_dir(case), ignore_errors=True)
    return Response(status_code=204)


@router.post("/{case_id}/token")
def new_token(case_id: str, user: User = Depends(current_user), s: Session = Depends(get_session),
              ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    case = get_org_case(s, user, case_id)
    case.upload_token = new_upload_token()
    case.token_expires_at = _now() + TOKEN_TTL
    s.add(case)
    s.commit()
    s.refresh(case)
    return case_summary(s, case, _today(ctx))


def _doc_row(s: Session, case: Case, doc_id: str) -> Document:
    row = s.get(Document, doc_id)
    if row is None or row.case_id != case.id:
        raise HTTPException(404, "Documento não encontrado.")
    return row


def _refresh_status(s: Session, case: Case) -> None:
    if case.status in ("PENDENTE", "REVISAR"):
        s.flush()
        case.status = computed_status(s, case)
        s.add(case)


@router.patch("/{case_id}/documents/{doc_id}")
def patch_document(case_id: str, doc_id: str, body: DocumentPatch, user: User = Depends(current_user),
                   s: Session = Depends(get_session), ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    case = get_org_case(s, user, case_id)
    row = _doc_row(s, case, doc_id)
    update: dict[str, Any] = {"needs_review": False}
    if body.doc_type is not None:
        update["doc_type"] = body.doc_type
    if body.title is not None:
        if not body.title.strip():
            raise HTTPException(422, "O título não pode ficar vazio.")
        update["title"] = body.title.strip()
    row.data = row.to_logical().model_copy(update=update).model_dump_json()
    s.add(row)
    _refresh_status(s, case)
    s.commit()
    return case_detail(s, case, _today(ctx))


@router.get("/{case_id}/documents/{doc_id}/pages/{n}.png")
def page_png(case_id: str, doc_id: str, n: int, scale: float = Query(0.4, gt=0, le=3),
             user: User = Depends(current_user_or_query), s: Session = Depends(get_session)) -> Response:
    case = get_org_case(s, user, case_id)
    doc = _doc_row(s, case, doc_id).to_logical()
    if not 0 <= n < len(doc.pages):
        raise HTTPException(404, "Página não encontrada.")
    try:
        png = normalize.render_page_png(doc.source_file, doc.pages[n], scale=scale)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, "Não foi possível gerar a miniatura desta página.") from exc
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@router.patch("/{case_id}/facts")
def patch_facts(case_id: str, body: dict[str, Any] = Body(...), user: User = Depends(current_user),
                s: Session = Depends(get_session), ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    case = get_org_case(s, user, case_id)
    row = s.get(CaseFactsRow, case.id)
    current = (row.to_facts() if row else CaseFacts(scenario=case.scenario)).model_dump(mode="json")
    edited = set(row.edited()) if row else set()
    for key, value in body.items():
        if key not in CaseFacts.model_fields or key == "scenario":
            raise HTTPException(422, f"Campo desconhecido: {key}")
        if CaseFacts.model_fields[key].annotation is FactField:
            if isinstance(value, dict):
                patch = {k: v for k, v in value.items() if k in FactField.model_fields}
            elif value is None or isinstance(value, (str, int, float)):
                patch = {"value": None if value is None else str(value)}
            else:
                raise HTTPException(422, f"Valor inválido para {key}.")
            current[key] = current[key] | patch
        else:
            current[key] = value
        if key not in NOT_EDITABLE:
            edited.add(key)
            current["valores_alternativos"].pop(key, None)  # conflito conferido
    try:
        facts = CaseFacts.model_validate(current)
    except ValueError as exc:
        raise HTTPException(422, f"Dados inválidos: {exc}") from exc
    row = row or CaseFactsRow(case_id=case.id, data="")
    row.data = facts.model_dump_json()
    row.edited_fields = json.dumps(sorted(edited))
    s.add(row)
    s.commit()
    return case_detail(s, case, _today(ctx))


@router.post("/{case_id}/approve")
def approve(case_id: str, body: ApproveIn | None = None, user: User = Depends(current_user),
            s: Session = Depends(get_session), ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    case = get_org_case(s, user, case_id)
    if case.status in ("RECEBENDO", "PROCESSANDO") or is_processing(case.id):
        raise HTTPException(409, "O caso ainda não terminou de ser processado.")
    missing = missing_docs(load_docs(s, case.id), case.scenario)
    if missing and not (body and body.force):
        raise HTTPException(409, f"Faltam {len(missing)} documento(s) obrigatório(s). "
                                 "Envie-os ou aprove com force=true.")
    case.status = "PRONTO"
    s.add(case)
    s.commit()
    return case_detail(s, case, _today(ctx))


@router.post("/{case_id}/reopen")
def reopen(case_id: str, user: User = Depends(current_user), s: Session = Depends(get_session),
           ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    case = get_org_case(s, user, case_id)
    if case.status == "PROCESSANDO" or is_processing(case.id):
        raise HTTPException(409, "O caso está sendo processado.")
    case.status = computed_status(s, case)
    s.add(case)
    s.commit()
    return case_detail(s, case, _today(ctx))


# ---------------------------------------------------------------- entregáveis

def _build_dossie(s: Session, case: Case, steps: Steps, ctx: Ctx) -> DossieResult:
    docs = load_docs(s, case.id)
    if not docs:
        raise HTTPException(409, "O caso ainda não tem documentos processados.")
    row = s.get(CaseFactsRow, case.id)
    out = ctx.case_dir(case) / "output" / f"dossie_{case.id}.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    for old in out.parent.glob(f"dossie_{case.id}_parte*.pdf"):
        old.unlink(missing_ok=True)
    return steps.build_dossie(docs, row.to_facts() if row else None, out)


@router.post("/{case_id}/dossie")
def gen_dossie(case_id: str, max_mb: float | None = Query(None, gt=0), user: User = Depends(current_user),
               s: Session = Depends(get_session), ctx: Ctx = Depends(get_ctx)) -> Response:
    from app.pipeline.assemble_pdf import split_by_size

    case = get_org_case(s, user, case_id)
    result = _build_dossie(s, case, ctx.steps_factory(), ctx)
    path = Path(result.path)
    if max_mb is not None:
        parts = split_by_size(path, max_mb)
        if len(parts) > 1:
            zpath = path.with_suffix(".zip")
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
                for p in parts:
                    z.write(p, arcname=p.name)
            return FileResponse(zpath, media_type="application/zip", filename=zpath.name)
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.post("/{case_id}/peticao")
def gen_peticao(case_id: str, narrative: bool = False, user: User = Depends(current_user),
                s: Session = Depends(get_session), ctx: Ctx = Depends(get_ctx)) -> Response:
    case = get_org_case(s, user, case_id)
    steps = ctx.steps_factory()
    result = _build_dossie(s, case, steps, ctx)  # o índice numerado do dossiê alimenta as remissões "Doc. NN"
    row = s.get(CaseFactsRow, case.id)
    facts = row.to_facts() if row else CaseFacts(scenario=case.scenario)
    text: str | None = None
    headers: dict[str, str] = {}
    if narrative:
        try:
            write = steps.write_narrative
            if write is None:
                from app.pipeline.draft_petition import write_narrative as write
                from app.pipeline.llm import provider_name

                if provider_name() == "heuristic":  # sem LLM: texto padrão (contrato)
                    raise RuntimeError("sem LLM")
            text = write(facts, result.index, case.scenario) or None
        except Exception:  # noqa: BLE001 - sem LLM ou falha: texto padrão
            text = None
        headers["X-Narrative"] = "llm" if text else "fallback"
    out = ctx.case_dir(case) / "output" / f"peticao_{case.id}.docx"
    path = Path(steps.draft_petition(facts, result.index, case.scenario, out, narrative=text))
    return FileResponse(path, media_type=DOCX, filename=path.name, headers=headers)
