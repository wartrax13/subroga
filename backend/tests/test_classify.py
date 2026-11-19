import base64
import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from pypdf import PdfReader
from reportlab.pdfgen import canvas

from app.doc_types import DocType
from app.pipeline import classify as cl
from app.schemas import PageClass


def make_pdf(path: Path, n: int) -> Path:
    c = canvas.Canvas(str(path))
    for i in range(n):
        c.drawString(72, 720, f"pagina {i}")
        c.showPage()
    c.save()
    return path


class FakeClient:
    """Imita client.messages.create devolvendo um tool_use montado por `handler(n_paginas_do_lote)`."""

    def __init__(self, handler: Callable[[int, int], list[dict[str, Any]]]):
        self.handler = handler
        self.calls: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        doc = next(b for b in kwargs["messages"][0]["content"] if b["type"] == "document")
        n = len(PdfReader(io.BytesIO(base64.b64decode(doc["source"]["data"]))).pages)
        pages = self.handler(len(self.calls) - 1, n)
        block = SimpleNamespace(type="tool_use", name=kwargs["tools"][0]["name"], input={"pages": pages})
        return SimpleNamespace(stop_reason="tool_use", content=[block])


def page(i: int, t: DocType, first: bool = False, conf: float = 0.95, title: str = "") -> dict[str, Any]:
    return {"page": i, "doc_type": t.value, "is_first_page_of_new_doc": first, "confidence": conf, "short_title": title}


def test_batches_offset(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "grande.pdf", 45)
    # tipo varia por lote para provar o offset: lote 0 -> APOLICE, 1 -> LAUDO, 2 -> FOTOS
    tipos = [DocType.APOLICE, DocType.LAUDO_TECNICO, DocType.FOTOS]
    client = FakeClient(lambda b, n: [page(i, tipos[b], first=(i == 0)) for i in range(n)])
    pages = cl.classify_pdf(pdf, client=client)

    assert len(client.calls) == 3
    assert [p.page for p in pages] == list(range(45))
    assert pages[19].doc_type == DocType.APOLICE
    assert pages[20].doc_type == DocType.LAUDO_TECNICO and pages[20].is_first_page_of_new_doc
    assert pages[40].doc_type == DocType.FOTOS
    assert pages[44].doc_type == DocType.FOTOS
    # schema da tool usa o enum de DocType
    schema = client.calls[0]["tools"][0]["input_schema"]
    assert schema["properties"]["pages"]["items"]["properties"]["doc_type"]["enum"] == [d.value for d in DocType]


def test_missing_page_becomes_outros(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "x.pdf", 3)
    client = FakeClient(lambda b, n: [page(0, DocType.APOLICE, True), page(2, DocType.APOLICE)])
    pages = cl.classify_pdf(pdf, client=client)
    assert pages[1].doc_type == DocType.OUTROS
    assert pages[1].confidence == 0


def test_segment_mixed_pdf_into_three_docs(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "misturado.pdf", 6)
    client = FakeClient(
        lambda b, n: [
            page(0, DocType.BOLETIM_OCORRENCIA, True, title="B.O. 123/2024"),
            page(1, DocType.BOLETIM_OCORRENCIA),
            page(2, DocType.ORCAMENTO_NF_REPARO, True, title="Orçamento Oficina A"),
            page(3, DocType.ORCAMENTO_NF_REPARO, False, conf=0.5),
            page(4, DocType.ORCAMENTO_NF_REPARO, True),  # mesmo tipo, mas documento novo
            page(5, DocType.ORCAMENTO_NF_REPARO),
        ]
    )
    docs = cl.segment(cl.classify_pdf(pdf, client=client), pdf)
    assert [d.pages for d in docs] == [[0, 1], [2, 3], [4, 5]]
    assert [d.doc_type for d in docs] == [
        DocType.BOLETIM_OCORRENCIA, DocType.ORCAMENTO_NF_REPARO, DocType.ORCAMENTO_NF_REPARO,
    ]
    assert docs[0].title == "B.O. 123/2024"
    assert docs[2].title == "Orçamentos e notas fiscais"  # fallback para LABELS
    assert docs[1].confidence == 0.5 and docs[1].needs_review
    assert not docs[0].needs_review
    assert len({d.id for d in docs}) == 3
    # id estável
    assert cl.segment(cl.classify_pdf(pdf, client=client), pdf)[0].id == docs[0].id


def test_segment_breaks_on_type_change() -> None:
    pcs = [
        PageClass(page=0, doc_type=DocType.APOLICE, is_first_page_of_new_doc=True, confidence=0.9),
        PageClass(page=1, doc_type=DocType.OUTROS, is_first_page_of_new_doc=False, confidence=0.0),
    ]
    docs = cl.segment(pcs, Path("a.pdf"))
    assert len(docs) == 2 and docs[1].needs_review
