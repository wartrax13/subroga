"""Fixtures compartilhadas dos testes de API: pipeline falso (sem rede) e app com auth."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app import auth, db
from app.config import Settings
from app.doc_types import DocType, Scenario
from app.main import create_app
from app.pipeline.run import Steps
from app.schemas import CaseFacts, DossieResult, FactField, IndexEntry, LogicalDoc, PageClass

auth.BCRYPT_ROUNDS = 4  # testes rápidos

PDF = b"%PDF-1.4\n%fake\n"
SECRET = "segredo-de-teste-com-mais-de-32-bytes"
TODAY = date(2026, 11, 7)

# nome do arquivo -> tipos de documento que o classificador "fake" devolve (um por página)
PLAN: dict[str, list[DocType]] = {
    "misto": [DocType.APOLICE, DocType.AVISO_SINISTRO, DocType.PROCURACAO],
    "pagto": [DocType.COMPROVANTE_PAGAMENTO],
    "termo": [DocType.TERMO_QUITACAO_SUBROGACAO],
    "bo": [DocType.BOLETIM_OCORRENCIA],
    "nf": [DocType.ORCAMENTO_NF_REPARO],
}

CLASSIFY_CALLS: list[tuple[str, int]] = []


def _pdf_page_count(path: Path) -> int | None:
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(path)).pages)
    except Exception:  # noqa: BLE001
        return None


def fake_classify(path: Path, client=None) -> list[PageClass]:
    key = next(k for k in PLAN if k in path.name)
    types = PLAN[key]
    n = _pdf_page_count(path)
    if n is not None:  # PDF real: um tipo por página (repete o último)
        types = [types[min(i, len(types) - 1)] for i in range(n)]
    CLASSIFY_CALLS.append((path.name, len(types)))
    return [PageClass(page=i, doc_type=t, is_first_page_of_new_doc=True,
                      confidence=0.9 if t != DocType.APOLICE else 0.5) for i, t in enumerate(types)]


def fake_segment(pages: list[PageClass], source_file: Path) -> list[LogicalDoc]:
    return [LogicalDoc(id=f"d{p.page}", doc_type=p.doc_type, title=p.doc_type.value, source_file=source_file,
                       pages=[p.page], confidence=p.confidence, needs_review=p.confidence < 0.7) for p in pages]


def fake_extract(docs: list[LogicalDoc], scenario: Scenario, client=None) -> CaseFacts:
    return CaseFacts(scenario=scenario, segurado_nome=FactField(value="Maria Silva"),
                     valor_pago=FactField(value="48320.00"), data_pagamento=FactField(value="2024-01-20"),
                     valores_alternativos={"valor_pago": [FactField(value="R$ 48.230,00")]})


def fake_dossie(docs: list[LogicalDoc], facts: CaseFacts | None, out_path: Path) -> DossieResult:
    out_path.write_bytes(PDF + b"".join(d.doc_type.value.encode() + b"\n" for d in docs))
    index = [IndexEntry(number=i + 1, doc_id=d.id, label=d.doc_type.value, title=d.title, start_fl=i + 3, end_fl=i + 3)
             for i, d in enumerate(docs)]
    return DossieResult(path=out_path, index=index, total_pages=len(docs) + 2)


def fake_petition(facts: CaseFacts, index: list[IndexEntry], scenario: Scenario, out_path: Path,
                  narrative: str | None = None) -> Path:
    out_path.write_text(f"{facts.segurado_nome.value} Doc. {index[0].number:02d} {scenario} {narrative or '-'}")
    return out_path


def fake_steps() -> Steps:
    return Steps(fake_classify, fake_segment, fake_extract, fake_dossie, fake_petition)


def make_pdf(texts: list[str]) -> bytes:
    """PDF real (reportlab) com uma página por texto."""
    import io

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    for t in texts:
        c.setFont("Helvetica", 14)
        c.drawString(72, 700, t)
        for k in range(40):  # volume para o teste de divisão por tamanho
            c.drawString(72, 680 - k * 14, f"{t} linha {k} " + "x" * 60)
        c.showPage()
    c.save()
    return buf.getvalue()


@dataclass
class Env:
    client: TestClient
    engine: object
    tmp: Path
    settings: Settings

    def signup(self, email: str = "ana@escritorio.com", org: str = "Escritório A", password: str = "segredo123"):
        r = self.client.post("/api/auth/signup", json={"org_name": org, "name": "Ana", "email": email,
                                                       "password": password})
        assert r.status_code == 201, r.text
        return r.json()

    def headers(self, email: str = "ana@escritorio.com", org: str = "Escritório A") -> dict[str, str]:
        return {"Authorization": f"Bearer {self.signup(email, org)['access_token']}"}


def make_env(tmp_path: Path, steps_factory=fake_steps, frontend_dir: Path | None = None,
             max_upload_mb: float = 50) -> Env:
    settings = Settings(storage_dir=tmp_path / "storage", db_url=f"sqlite:///{tmp_path / 'test.db'}",
                        jwt_secret=SECRET, max_upload_mb=max_upload_mb,
                        frontend_dir=frontend_dir or tmp_path / "sem-frontend")
    engine = db.make_engine(settings.db_url)
    app = create_app(engine=engine, steps_factory=steps_factory, today=lambda: TODAY, settings=settings,
                     resume_on_startup=False)
    return Env(TestClient(app), engine, tmp_path, settings)


def new_case(env: Env, h: dict[str, str], scenario: str = "AUTO", name: str = "Sinistro X") -> tuple[str, str]:
    r = env.client.post("/api/cases", json={"scenario": scenario, "name": name}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"], r.json()["upload_token"]


def upload(env: Env, token: str, *names: str, content: bytes = PDF, real: bool = False):
    files = []
    for n in names:
        data = make_pdf([f"{n} p{i}" for i in range(len(PLAN[n.split('.')[0]]))]) if real else content + n.encode()
        files.append(("files", (n, data, "application/pdf")))
    return env.client.post(f"/api/public/upload/{token}", files=files)
