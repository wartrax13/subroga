"""5.2 Extração dos dados do caso (CaseFacts) a partir dos documentos lógicos."""
from __future__ import annotations

import io
import re
from datetime import date
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter

from app.doc_types import DocType, Scenario
from app.pipeline import heuristic, llm
from app.schemas import CaseFacts, Evidence, FactField, LogicalDoc

TOOL_NAME = "registrar_fatos"

RELEVANT_TYPES: set[DocType] = {
    DocType.APOLICE,
    DocType.AVISO_SINISTRO,
    DocType.BOLETIM_OCORRENCIA,
    DocType.LAUDO_TECNICO,
    DocType.ORCAMENTO_NF_REPARO,
    DocType.COMPROVANTE_PAGAMENTO,
    DocType.TERMO_QUITACAO_SUBROGACAO,
    DocType.DOCS_TRANSPORTE,
    DocType.DOCS_VEICULO_CONDUTOR,
    DocType.RECLAMACAO_ADMINISTRATIVA,
}

FACT_FIELDS: list[str] = [
    name for name, f in CaseFacts.model_fields.items() if f.annotation is FactField
]
DATE_FIELDS = {"apolice_vigencia_inicio", "apolice_vigencia_fim", "sinistro_data", "data_pagamento"}
MONEY_FIELDS = {"valor_pago", "franquia"}

_FIELD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["value", "source_doc_id", "page"],
    "properties": {
        "value": {"type": ["string", "null"]},
        "source_doc_id": {"type": ["string", "null"]},
        "page": {"type": ["integer", "null"]},
    },
}

EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [*FACT_FIELDS, "evidencias_culpa", "valores_alternativos"],
    "properties": {
        **{name: _FIELD_SCHEMA for name in FACT_FIELDS},
        "evidencias_culpa": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["fato", "doc_id"],
                "properties": {"fato": {"type": "string"}, "doc_id": {"type": ["string", "null"]}},
            },
        },
        "valores_alternativos": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["campo", "value", "source_doc_id", "page"],
                "properties": {
                    "campo": {"type": "string", "enum": FACT_FIELDS},
                    "value": {"type": ["string", "null"]},
                    "source_doc_id": {"type": ["string", "null"]},
                    "page": {"type": ["integer", "null"]},
                },
            },
        },
    },
}


# ---------- normalização ----------

def normalize_money(v: str | None) -> str | None:
    """'R$ 48.320,00' / '48320' / '48,320.00' -> '48320.00'."""
    if v is None:
        return None
    s = re.sub(r"[^\d,.\-]", "", str(v))
    if not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if len(s.split(",")[-1]) <= 2 else s.replace(",", "")
    elif s.count(".") > 1 or (s.count(".") == 1 and len(s.split(".")[-1]) == 3):
        s = s.replace(".", "")
    try:
        return f"{float(s):.2f}"
    except ValueError:
        return None


def normalize_date(v: str | None) -> str | None:
    """'12/03/2024' / '2024-03-12' / '12.03.24' -> '2024-03-12'."""
    if v is None:
        return None
    s = str(v).strip()
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
    else:
        m = re.search(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})", s)
        if not m:
            return s or None
        d, mo, y = int(m[1]), int(m[2]), int(m[3])
        if y < 100:
            y += 2000
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return s


def _normalize_value(field: str, v: Any) -> str | None:
    if v is None:
        return None
    v = str(v).strip()
    if not v:
        return None
    if field in MONEY_FIELDS:
        return normalize_money(v)
    if field in DATE_FIELDS:
        return normalize_date(v)
    return v


# ---------- montagem da requisição ----------

def _doc_pdf(reader: PdfReader, pages: list[int]) -> bytes:
    writer = PdfWriter()
    for p in pages:
        if 0 <= p < len(reader.pages):
            writer.add_page(reader.pages[p])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _page_text(reader: PdfReader, p: int, limit: int = 2500) -> str:
    try:
        text = (reader.pages[p].extract_text() or "").strip()
    except Exception:  # noqa: BLE001
        text = ""
    return text[:limit] if text else "(sem texto embutido: use a imagem da página)"


def _build_parts(docs: list[LogicalDoc], scenario: Scenario) -> list[dict[str, Any]]:
    """Conteúdo neutro de provedor: marcador de texto + PDF de cada documento."""
    readers: dict[Path, PdfReader] = {}
    parts: list[dict[str, Any]] = []
    for d in docs:
        reader = readers.setdefault(Path(d.source_file), PdfReader(str(d.source_file)))
        hints = "\n".join(
            f"--- página original {p} ---\n{_page_text(reader, p)}" for p in d.pages if 0 <= p < len(reader.pages)
        )
        parts.append(llm.text_part(
            f"[DOC id={d.id} tipo={d.doc_type.value} paginas={','.join(map(str, d.pages))}] {d.title}\n"
            f"Texto embutido do documento (pode estar vazio em digitalizações):\n{hints}"
        ))
        parts.append(llm.pdf_part(_doc_pdf(reader, d.pages), title=d.id))
    parts.append(llm.text_part(
        f"Cenário do sinistro: {scenario.value}. Extraia os fatos do caso a partir dos documentos acima "
        f"chamando a ferramenta {TOOL_NAME} (ou respondendo no formato JSON pedido)."
    ))
    return parts


# ---------- pós-processamento ----------

def _to_field(name: str, raw: Any, docs_by_id: dict[str, LogicalDoc]) -> FactField:
    if not isinstance(raw, dict):
        return FactField()
    value = _normalize_value(name, raw.get("value"))
    if value is None:
        return FactField()
    doc_id = raw.get("source_doc_id")
    doc = docs_by_id.get(doc_id) if isinstance(doc_id, str) else None
    page = raw.get("page")
    if doc is None:
        return FactField(value=value)
    if not isinstance(page, int) or page not in doc.pages:
        # aceita índice relativo ao documento; senão, 1ª página do doc
        page = doc.pages[page] if isinstance(page, int) and 0 <= page < len(doc.pages) else doc.pages[0]
    return FactField(value=value, source_doc_id=doc.id, page=page)


def facts_from_raw(raw: dict[str, Any], docs: list[LogicalDoc], scenario: Scenario | None) -> CaseFacts:
    docs_by_id = {d.id: d for d in docs}
    facts = CaseFacts(scenario=scenario)
    for name in FACT_FIELDS:
        setattr(facts, name, _to_field(name, raw.get(name), docs_by_id))

    facts.evidencias_culpa = [
        Evidence(fato=str(e["fato"]).strip(), doc_id=e.get("doc_id") if e.get("doc_id") in docs_by_id else None)
        for e in raw.get("evidencias_culpa") or []
        if isinstance(e, dict) and str(e.get("fato") or "").strip()
    ]

    grouped: dict[str, list[FactField]] = {}
    for alt in raw.get("valores_alternativos") or []:
        if not isinstance(alt, dict) or alt.get("campo") not in FACT_FIELDS:
            continue
        ff = _to_field(alt["campo"], alt, docs_by_id)
        if ff.value is not None:
            grouped.setdefault(alt["campo"], []).append(ff)

    alternativos: dict[str, list[FactField]] = {}
    for campo, items in grouped.items():
        main: FactField = getattr(facts, campo)
        if main.value is not None and not any(
            i.value == main.value and i.source_doc_id == main.source_doc_id for i in items
        ):
            items.insert(0, main)
        seen: set[tuple[str | None, str | None]] = set()
        uniq = [i for i in items if (k := (i.value, i.source_doc_id)) not in seen and not seen.add(k)]
        if len({i.value for i in uniq}) > 1:
            alternativos[campo] = uniq
    facts.valores_alternativos = alternativos
    return facts


def extract_facts(docs: list[LogicalDoc], scenario: Scenario, client: Any = None) -> CaseFacts:
    """Extrai os CaseFacts dos documentos relevantes no provedor do ambiente (ou do `client`)."""
    relevant = [d for d in docs if d.doc_type in RELEVANT_TYPES]
    if not relevant:
        return CaseFacts(scenario=scenario)
    if client is None and llm.provider_name() == "heuristic":
        return heuristic.extract_facts(relevant, scenario)
    raw = llm.structured_call(
        "extract",
        system=llm.load_prompt("extract"),
        parts=_build_parts(relevant, scenario),
        schema=EXTRACT_SCHEMA,
        name=TOOL_NAME,
        description="Registra os dados estruturados do sinistro, com documento e página de origem de cada campo.",
        client=client,
        max_tokens=16000,
        effort="medium",
    )
    return facts_from_raw(raw, relevant, scenario)
