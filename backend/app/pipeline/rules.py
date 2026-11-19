"""Regras determinísticas (sem IA): checklist, prescrição, conflitos, confiança."""
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from app.checklists import ESSENTIAL, RECOMMENDED, REQUIRED
from app.doc_types import LABELS, DocType, Scenario
from app.pipeline.assemble_pdf import sort_docs  # noqa: F401  (única implementação; reexportada aqui)
from app.schemas import Alert, CaseFacts, LogicalDoc

PRESCRIPTION_YEARS = 3
LOW_CONFIDENCE = 0.7
MONEY_FIELDS = {"valor_pago", "franquia"}


def _present(docs: list[LogicalDoc]) -> set[DocType]:
    return {d.doc_type for d in docs}


def missing_docs(docs: list[LogicalDoc], scenario: Scenario) -> list[DocType]:
    present = _present(docs)
    return [t for t in REQUIRED[scenario] if t not in present]


def missing_recommended(docs: list[LogicalDoc], scenario: Scenario) -> list[DocType]:
    present = _present(docs)
    return [t for t in RECOMMENDED[scenario] if t not in present]


def add_years(d: date, years: int) -> date:
    """Lei 810/1949, art. 3º: sem o dia correspondente (29/02), o prazo finda no dia seguinte."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return date(d.year + years, 3, 1)


def _fmt(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def prescription_deadline(data_pagamento: str | None) -> date | None:
    """Fim do prazo prescricional (3 anos do pagamento) ou None se a data não for ISO válida."""
    if not data_pagamento:
        return None
    try:
        paid = date.fromisoformat(data_pagamento.strip()[:10])
    except ValueError:
        return None
    return add_years(paid, PRESCRIPTION_YEARS)


def prescription_alert(data_pagamento: str | None, today: date) -> Alert | None:
    if not data_pagamento:
        return None
    deadline = prescription_deadline(data_pagamento)
    if deadline is None:
        return Alert(kind="PRESCRIPTION", severity="warn",
                     message=f"Data de pagamento inválida ({data_pagamento!r}); prescrição não calculada.")
    days = (deadline - today).days
    base = f"Prescrição (art. 206, §3º, V, CC) em {_fmt(deadline)}"
    if days < 0:
        return Alert(kind="PRESCRIPTION", severity="critical",
                     message=f"{base}: PRAZO VENCIDO há {-days} dias.")
    if days < 90:
        severity = "critical"
    elif days < 365:
        severity = "warn"
    else:
        severity = "info"
    return Alert(kind="PRESCRIPTION", severity=severity, message=f"{base}: faltam {days} dias.")


def normalize_money(value: str) -> Decimal | None:
    """Aceita "48320.00", "48.320,00", "R$ 48.320,00", "48,320.00"."""
    s = re.sub(r"[^\d,.\-]", "", value or "")
    if not s:
        return None
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        thou = "." if dec == "," else ","
        s = s.replace(thou, "").replace(dec, ".")
    elif "," in s:
        s = s.replace(".", "").replace(",", ".") if s.count(",") == 1 else s.replace(",", "")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        return Decimal(s).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _norm(field: str, value: str) -> object:
    if field in MONEY_FIELDS:
        money = normalize_money(value)
        if money is not None:
            return money
    return " ".join(value.split()).casefold()


def conflict_alerts(facts: CaseFacts) -> list[Alert]:
    alerts: list[Alert] = []
    for field, alternatives in facts.valores_alternativos.items():
        main = getattr(facts, field, None)
        candidates = ([main] if main is not None and hasattr(main, "value") else []) + list(alternatives)
        seen: dict[object, str] = {}
        for ff in candidates:
            if ff.value:
                seen.setdefault(_norm(field, ff.value), ff.value)
        if len(seen) > 1:
            shown = " x ".join(f'"{v}"' for v in seen.values())
            alerts.append(Alert(kind="CONFLICT", severity="warn", field=field,
                                message=f"Valores divergentes para {field}: {shown}. Conferir nos documentos."))
    return alerts


def low_confidence_alerts(docs: list[LogicalDoc]) -> list[Alert]:
    return [
        Alert(kind="LOW_CONFIDENCE", severity="warn",
              message=f"Classificação a revisar: \"{d.title}\" ({LABELS[d.doc_type]}, confiança {d.confidence:.0%}).")
        for d in docs
        if d.needs_review or d.confidence < LOW_CONFIDENCE
    ]


def compute_alerts(docs: list[LogicalDoc], facts: CaseFacts | None, scenario: Scenario, today: date) -> list[Alert]:
    alerts: list[Alert] = []
    for t in missing_docs(docs, scenario):
        alerts.append(Alert(kind="MISSING_DOC", severity="critical" if t in ESSENTIAL else "warn",
                            message=f"Documento obrigatório ausente: {LABELS[t]}."))
    for t in missing_recommended(docs, scenario):
        alerts.append(Alert(kind="MISSING_DOC", severity="info",
                            message=f"Documento recomendado ausente: {LABELS[t]}."))
    if facts is not None:
        presc = prescription_alert(facts.data_pagamento.value, today)
        if presc is None:
            presc = Alert(kind="PRESCRIPTION", severity="warn",
                          message="Data de pagamento da indenização não identificada; prescrição não calculada.")
        alerts.append(presc)
        alerts.extend(conflict_alerts(facts))
    alerts.extend(low_confidence_alerts(docs))
    return alerts

