from pathlib import Path
from types import SimpleNamespace
from typing import Any

from reportlab.pdfgen import canvas

from app.doc_types import DocType, Scenario
from app.pipeline import extract as ex
from app.schemas import LogicalDoc


def make_pdf(path: Path, n: int) -> Path:
    c = canvas.Canvas(str(path))
    for i in range(n):
        c.drawString(72, 720, f"pagina {i}")
        c.showPage()
    c.save()
    return path


class FakeClient:
    def __init__(self, tool_input: dict[str, Any]):
        self.tool_input = tool_input
        self.calls: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            stop_reason="tool_use",
            content=[
                SimpleNamespace(type="thinking", thinking=""),
                SimpleNamespace(type="tool_use", name=kwargs["tools"][0]["name"], input=self.tool_input),
            ],
        )


def f(value: Any = None, doc: str | None = None, page: int | None = None) -> dict[str, Any]:
    return {"value": value, "source_doc_id": doc, "page": page}


def _docs(pdf: Path) -> list[LogicalDoc]:
    mk = lambda i, t, pages: LogicalDoc(id=i, doc_type=t, title=t.value, source_file=pdf, pages=pages, confidence=0.9)
    return [
        mk("d_proc", DocType.PROCURACAO, [0]),
        mk("d_apol", DocType.APOLICE, [1, 2]),
        mk("d_comp", DocType.COMPROVANTE_PAGAMENTO, [3]),
        mk("d_termo", DocType.TERMO_QUITACAO_SUBROGACAO, [4]),
        mk("d_fotos", DocType.FOTOS, [5]),
    ]


def test_extract_facts(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "caso.pdf", 6)
    docs = _docs(pdf)
    raw = {name: f() for name in ex.FACT_FIELDS}
    raw.update(
        segurado_nome=f("João da Silva", "d_apol", 1),
        apolice_numero=f("123456", "d_apol", 1),
        apolice_vigencia_inicio=f("01/02/2024", "d_apol", 2),
        valor_pago=f("R$ 48.320,00", "d_comp", 3),
        data_pagamento=f("15/03/2024", "d_comp", 0),  # página relativa ao doc
        causador_nome=f("Fulano", "d_inexistente", 9),
    )
    raw["evidencias_culpa"] = [{"fato": "Réu avançou o sinal", "doc_id": "d_apol"}]
    raw["valores_alternativos"] = [
        {"campo": "valor_pago", "value": "48.320,00", "source_doc_id": "d_comp", "page": 3},
        {"campo": "valor_pago", "value": "45000", "source_doc_id": "d_termo", "page": 4},
    ]
    client = FakeClient(raw)
    facts = ex.extract_facts(docs, Scenario.AUTO, client=client)

    # só documentos relevantes enviados
    texts = " ".join(b["text"] for b in client.calls[0]["messages"][0]["content"] if b["type"] == "text")
    assert "d_apol" in texts and "d_comp" in texts and "d_termo" in texts
    assert "d_proc" not in texts and "d_fotos" not in texts
    n_docs = sum(1 for b in client.calls[0]["messages"][0]["content"] if b["type"] == "document")
    assert n_docs == 3

    assert facts.scenario == Scenario.AUTO
    assert facts.segurado_nome.value == "João da Silva"
    assert facts.segurado_nome.source_doc_id == "d_apol" and facts.segurado_nome.page == 1
    assert facts.valor_pago.value == "48320.00"
    assert facts.apolice_vigencia_inicio.value == "2024-02-01"
    assert facts.data_pagamento.value == "2024-03-15" and facts.data_pagamento.page == 3
    assert facts.causador_nome.value == "Fulano" and facts.causador_nome.source_doc_id is None
    assert facts.franquia.value is None
    assert facts.evidencias_culpa[0].doc_id == "d_apol"

    alts = facts.valores_alternativos["valor_pago"]
    assert {a.value for a in alts} == {"48320.00", "45000.00"}
    assert {a.source_doc_id for a in alts} == {"d_comp", "d_termo"}


def test_no_conflict_means_no_alternativos(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "caso.pdf", 6)
    raw = {name: f() for name in ex.FACT_FIELDS}
    raw["valor_pago"] = f("100,00", "d_comp", 3)
    raw["evidencias_culpa"] = []
    raw["valores_alternativos"] = [{"campo": "valor_pago", "value": "100.00", "source_doc_id": "d_termo", "page": 4}]
    facts = ex.extract_facts(_docs(pdf), Scenario.AUTO, client=FakeClient(raw))
    assert facts.valores_alternativos == {}


def test_normalizers() -> None:
    assert ex.normalize_money("R$ 1.234.567,89") == "1234567.89"
    assert ex.normalize_money("48320") == "48320.00"
    assert ex.normalize_money("48,320.50") == "48320.50"
    assert ex.normalize_money("12.500") == "12500.00"
    assert ex.normalize_date("05/01/23") == "2023-01-05"
    assert ex.normalize_date("2024-12-31") == "2024-12-31"
