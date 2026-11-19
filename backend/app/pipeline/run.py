"""Orquestração do pipeline de um caso (roda em BackgroundTasks / thread).

- Só os arquivos ainda não processados são classificados (correções manuais de documentos ficam).
- Páginas idênticas a páginas já vistas no caso não são reclassificadas (dedupe por página).
- Campos de fatos editados manualmente não são sobrescritos pela reextração.
- Uma trava por caso impede dois processamentos simultâneos; pedidos que chegam durante
  um processamento ficam marcados em `Case.run_requested` e são atendidos pelo worker ativo.
"""
import json
import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from app.models import Case, CaseFactsRow, Document, UploadedFile
from app.pipeline.normalize import page_hashes, write_page_subset
from app.pipeline.rules import missing_docs, sort_docs
from app.schemas import CaseFacts, LogicalDoc

log = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]


@dataclass
class Steps:
    classify_pdf: Callable[..., Any]   # (path, client=None) -> list[PageClass]
    segment: Callable[..., Any]        # (pages, source_file) -> list[LogicalDoc]
    extract_facts: Callable[..., Any]  # (docs, scenario, client=None) -> CaseFacts
    build_dossie: Callable[..., Any]   # (docs, facts, out_path) -> DossieResult
    draft_petition: Callable[..., Any] # (facts, index, scenario, out_path, narrative=None) -> Path
    # (facts, index, scenario, client=None) -> str; None = importa draft_petition.write_narrative na hora
    write_narrative: Callable[..., Any] | None = None


def default_steps() -> Steps:
    """Importa os módulos reais só quando necessário."""
    from app.pipeline.assemble_pdf import build_dossie
    from app.pipeline.classify import classify_pdf, segment
    from app.pipeline.draft_petition import draft_petition
    from app.pipeline.extract import extract_facts

    return Steps(classify_pdf, segment, extract_facts, build_dossie, draft_petition)


def load_docs(session: Session, case_id: str) -> list[LogicalDoc]:
    rows = session.exec(select(Document).where(Document.case_id == case_id)).all()
    return sort_docs([r.to_logical() for r in rows])


def computed_status(session: Session, case: Case) -> str:
    """Status derivado do estado atual (sem considerar aprovação)."""
    files = session.exec(select(UploadedFile).where(UploadedFile.case_id == case.id)).all()
    if not files:
        return "RECEBENDO"
    return "PENDENTE" if missing_docs(load_docs(session, case.id), case.scenario) else "REVISAR"


def merge_edited(new: CaseFacts, old: CaseFacts | None, edited: list[str]) -> CaseFacts:
    """Mantém em `new` os campos editados manualmente em `old`."""
    if old is None or not edited:
        return new
    update: dict[str, Any] = {f: getattr(old, f) for f in edited if f in CaseFacts.model_fields}
    alternatives = {k: v for k, v in new.valores_alternativos.items() if k not in edited}
    return new.model_copy(update=update | {"valores_alternativos": alternatives})


# ---------------------------------------------------------------- trava por caso

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def case_lock(case_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(case_id, threading.Lock())


def is_processing(case_id: str) -> bool:
    return case_lock(case_id).locked()


def _set_flag(sf: SessionFactory, case_id: str, value: bool) -> bool:
    """Grava o flag e devolve o valor anterior."""
    with sf() as s:
        case = s.get(Case, case_id)
        if case is None:
            return False
        before = case.run_requested
        case.run_requested = value
        s.add(case)
        s.commit()
        return before


def _peek_flag(sf: SessionFactory, case_id: str) -> bool:
    with sf() as s:
        case = s.get(Case, case_id)
        return bool(case and case.run_requested)


def process_case(case_id: str, session_factory: SessionFactory, storage_dir: Path,
                 steps: Steps | None = None, client: Any = None) -> None:
    """Pede o processamento do caso e o executa se ninguém mais estiver processando."""
    _set_flag(session_factory, case_id, True)
    lock = case_lock(case_id)
    while True:
        if not lock.acquire(blocking=False):
            return  # o worker ativo verá o flag
        try:
            while _set_flag(session_factory, case_id, False):
                _process_once(case_id, session_factory, steps, client)
        finally:
            lock.release()
        if not _peek_flag(session_factory, case_id):
            return


# ---------------------------------------------------------------- uma rodada

def _safe_page_hashes(path: Path) -> list[str]:
    try:
        return page_hashes(path)
    except Exception:  # noqa: BLE001 - PDF ilegível pelo pypdf: classifica inteiro, sem dedupe
        log.warning("não foi possível calcular hashes de página de %s", path)
        return []


def _classify_file(f: UploadedFile, seen: set[str], steps: Steps, client: Any) -> list[LogicalDoc]:
    path = Path(f.path)
    hashes = _safe_page_hashes(path)
    keep: list[int] = []
    for i, h in enumerate(hashes):
        if h not in seen:
            seen.add(h)
            keep.append(i)
    f.duplicate_pages = len(hashes) - len(keep)
    f.page_hashes = json.dumps(hashes)
    if hashes and not keep:
        return []  # arquivo só com páginas já vistas
    if hashes and len(keep) < len(hashes):
        subset = write_page_subset(path, keep, path.with_name(f"{path.stem}__unicas.pdf"))
        try:
            pages = steps.classify_pdf(subset, client=client)
        finally:
            subset.unlink(missing_ok=True)
        pages = [p.model_copy(update={"page": keep[p.page]}) for p in pages if 0 <= p.page < len(keep)]
    else:
        pages = steps.classify_pdf(path, client=client)
    return list(steps.segment(pages, path))


def _process_once(case_id: str, sf: SessionFactory, steps: Steps | None, client: Any) -> None:
    with sf() as s:
        case = s.get(Case, case_id)
        if case is None:
            return
        case.status = "PROCESSANDO"
        case.error = None
        s.add(case)
        s.commit()
    try:
        steps = steps or default_steps()
        with sf() as s:
            case = s.get(Case, case_id)
            if case is None:
                return
            files = s.exec(select(UploadedFile).where(UploadedFile.case_id == case_id)
                           .order_by(UploadedFile.id)).all()
            seen: set[str] = {h for f in files if f.processed for h in f.hashes()}
            for f in files:
                if f.processed:
                    continue
                for doc in _classify_file(f, seen, steps, client):
                    doc = doc.model_copy(update={"id": uuid.uuid4().hex[:12]})  # id único no caso
                    s.add(Document(id=doc.id, case_id=case_id, data=doc.model_dump_json()))
                f.processed = True
                s.add(f)
                s.commit()

            docs = load_docs(s, case_id)
            if docs:
                row = s.get(CaseFactsRow, case_id)
                old = row.to_facts() if row else None
                new = steps.extract_facts(docs, case.scenario, client=client)
                facts = merge_edited(new, old, row.edited() if row else [])
                row = row or CaseFactsRow(case_id=case_id, data="")
                row.data = facts.model_dump_json()
                s.add(row)
                s.commit()
            case.status = computed_status(s, case)
            s.add(case)
            s.commit()
    except Exception as exc:  # noqa: BLE001 - não derrubar o worker; o painel mostra o erro
        log.exception("falha ao processar caso %s", case_id)
        with sf() as s:
            case = s.get(Case, case_id)
            if case is not None:
                case.status = "REVISAR"
                case.error = f"Falha no processamento ({type(exc).__name__}): {exc}"
                s.add(case)
                s.commit()
