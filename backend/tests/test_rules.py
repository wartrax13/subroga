from datetime import date
from decimal import Decimal
from pathlib import Path

from app.checklists import REQUIRED
from app.doc_types import DocType, Scenario
from app.pipeline.rules import (
    add_years, compute_alerts, conflict_alerts, low_confidence_alerts, missing_docs,
    normalize_money, prescription_alert, sort_docs,
)
from app.schemas import CaseFacts, FactField, LogicalDoc


def doc(t: DocType, id: str = "", conf: float = 0.95, review: bool = False) -> LogicalDoc:
    return LogicalDoc(id=id or t.value, doc_type=t, title=t.value.lower(), source_file=Path("x.pdf"),
                      pages=[0], confidence=conf, needs_review=review)


def test_checklist_reports_missing_in_order():
    docs = [doc(DocType.APOLICE), doc(DocType.PROCURACAO), doc(DocType.BOLETIM_OCORRENCIA)]
    assert missing_docs(docs, Scenario.AUTO) == [
        DocType.AVISO_SINISTRO, DocType.COMPROVANTE_PAGAMENTO,
        DocType.TERMO_QUITACAO_SUBROGACAO, DocType.ORCAMENTO_NF_REPARO,
    ]


def test_checklist_complete():
    for scenario, required in REQUIRED.items():
        assert missing_docs([doc(t) for t in required], scenario) == []
    assert DocType.DOCS_TRANSPORTE in REQUIRED[Scenario.TRANSPORTE]
    assert DocType.RECLAMACAO_ADMINISTRATIVA not in REQUIRED[Scenario.ELETRICO]


def test_prescription_levels():
    today = date(2026, 11, 7)
    assert prescription_alert(None, today) is None
    expired = prescription_alert("2023-11-06", today)
    assert expired.severity == "critical" and "VENCIDO" in expired.message
    assert prescription_alert("2023-11-07", today).severity == "critical"  # vence hoje: 0 dias
    assert prescription_alert("2024-01-20", today).severity == "critical"  # 74 dias
    assert prescription_alert("2024-03-01", today).severity == "warn"
    assert prescription_alert("2025-01-01", today).severity == "info"
    assert "faltam 74 dias" in prescription_alert("2024-01-20", today).message


def test_prescription_leap_day():
    assert add_years(date(2024, 2, 29), 3) == date(2027, 3, 1)
    assert add_years(date(2024, 2, 28), 3) == date(2027, 2, 28)
    assert "01/03/2027" in prescription_alert("2024-02-29", date(2026, 1, 1)).message


def test_prescription_invalid_date():
    assert prescription_alert("31/12/2024", date(2026, 1, 1)).severity == "warn"


def test_normalize_money():
    for raw in ["48320.00", "48.320,00", "R$ 48.320,00", "48,320.00", "48320", "48.320"]:
        assert normalize_money(raw) == Decimal("48320.00"), raw
    assert normalize_money("1.234.567,89") == Decimal("1234567.89")
    assert normalize_money("") is None


def test_conflict_detects_different_values():
    facts = CaseFacts(
        valor_pago=FactField(value="48320.00", source_doc_id="a"),
        valores_alternativos={"valor_pago": [FactField(value="R$ 48.230,00", source_doc_id="b")]},
    )
    alerts = conflict_alerts(facts)
    assert len(alerts) == 1 and alerts[0].kind == "CONFLICT" and alerts[0].severity == "warn"


def test_conflict_ignores_equivalent_formats():
    facts = CaseFacts(
        valor_pago=FactField(value="48320.00"),
        valores_alternativos={
            "valor_pago": [FactField(value="R$ 48.320,00"), FactField(value=None)],
            "segurado_nome": [FactField(value="Maria  Silva"), FactField(value="maria silva")],
        },
    )
    assert conflict_alerts(facts) == []


def test_low_confidence():
    docs = [doc(DocType.APOLICE), doc(DocType.OUTROS, "o", conf=0.4), doc(DocType.FOTOS, "f", review=True)]
    assert len(low_confidence_alerts(docs)) == 2


def test_sort_docs_stable_by_enum_order():
    docs = [doc(DocType.TERMO_QUITACAO_SUBROGACAO), doc(DocType.FOTOS, "f1"), doc(DocType.PROCURACAO),
            doc(DocType.FOTOS, "f2"), doc(DocType.OUTROS)]
    assert [d.id for d in sort_docs(docs)] == ["PROCURACAO", "f1", "f2", "TERMO_QUITACAO_SUBROGACAO", "OUTROS"]


def test_compute_alerts():
    docs = [doc(t) for t in REQUIRED[Scenario.ELETRICO] if t != DocType.TERMO_QUITACAO_SUBROGACAO]
    facts = CaseFacts(data_pagamento=FactField(value="2024-01-20"))
    alerts = compute_alerts(docs, facts, Scenario.ELETRICO, date(2026, 11, 7))
    kinds = [(a.kind, a.severity) for a in alerts]
    assert ("MISSING_DOC", "critical") in kinds  # termo de sub-rogação
    assert ("MISSING_DOC", "info") in kinds      # reclamação administrativa (recomendado)
    assert ("PRESCRIPTION", "critical") in kinds
    no_date = compute_alerts(docs, CaseFacts(), Scenario.ELETRICO, date(2026, 11, 7))
    assert any(a.kind == "PRESCRIPTION" and a.severity == "warn" for a in no_date)
