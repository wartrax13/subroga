"""5.1 Classificação de páginas e segmentação em documentos lógicos."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from pypdf import PdfReader, PdfWriter

from app.doc_types import LABELS, DocType
from app.pipeline import heuristic, llm
from app.schemas import LogicalDoc, PageClass

BATCH_SIZE = 20
REVIEW_THRESHOLD = 0.7
TOOL_NAME = "registrar_classificacao"

CLASSIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["pages"],
    "properties": {
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["page", "doc_type", "is_first_page_of_new_doc", "confidence", "short_title"],
                "properties": {
                    "page": {"type": "integer", "description": "índice 0-based no PDF recebido"},
                    "doc_type": {"type": "string", "enum": [d.value for d in DocType]},
                    "is_first_page_of_new_doc": {"type": "boolean"},
                    "confidence": {"type": "number", "description": "0 a 1"},
                    "short_title": {"type": "string"},
                },
            },
        }
    },
}


def _batch_bytes(reader: PdfReader, start: int, end: int) -> bytes:
    writer = PdfWriter()
    for i in range(start, end):
        writer.add_page(reader.pages[i])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _parse_batch(raw: dict[str, Any], start: int, count: int) -> dict[int, PageClass]:
    """Converte a resposta do lote em {página_original: PageClass}, ignorando itens inválidos."""
    out: dict[int, PageClass] = {}
    for item in raw.get("pages") or []:
        if not isinstance(item, dict):
            continue
        try:
            local = int(item.get("page", -1))
        except (TypeError, ValueError):
            continue
        if not 0 <= local < count or (start + local) in out:
            continue
        try:
            conf = max(0.0, min(1.0, float(item.get("confidence", 0))))
            out[start + local] = PageClass.model_validate(
                {**item, "page": start + local, "confidence": conf}
            )
        except (ValidationError, TypeError, ValueError):
            continue
    return out


def _page_hints(reader: PdfReader, start: int, end: int, limit: int = 1200) -> str:
    """Texto embutido de cada página do lote (pistas para alinhar os índices)."""
    out: list[str] = []
    for i in range(start, end):
        try:
            text = (reader.pages[i].extract_text() or "").strip()
        except Exception:  # noqa: BLE001
            text = ""
        text = text[:limit] if text else "(sem texto embutido: use a imagem da página)"
        out.append(f"--- página {i - start} ---\n{text}")
    return "\n".join(out)


def classify_pdf(path: Path, client: Any = None) -> list[PageClass]:
    """Classifica todas as páginas de `path` (índices 0-based do arquivo original).

    O provedor vem do ambiente (`llm.provider_name()`), salvo se `client` for injetado.
    """
    path = Path(path)
    if client is None and llm.provider_name() == "heuristic":
        return heuristic.classify_pdf(path)
    reader = PdfReader(str(path))
    total = len(reader.pages)
    system = llm.load_prompt("classify")
    result: dict[int, PageClass] = {}

    for start in range(0, total, BATCH_SIZE):
        end = min(start + BATCH_SIZE, total)
        count = end - start
        parts = [
            llm.pdf_part(_batch_bytes(reader, start, end), title=f"{path.stem}_p{start}-{end - 1}"),
            llm.text_part(
                f"Este PDF tem {count} página(s) (índices 0 a {count - 1}). "
                f"Classifique todas, uma entrada por página, na ordem, chamando a ferramenta {TOOL_NAME} "
                f"(ou respondendo no formato JSON pedido).\n\n"
                f"Texto embutido extraído de cada página (pode estar vazio em digitalizações):\n"
                f"{_page_hints(reader, start, end)}"
            ),
        ]
        raw = llm.structured_call(
            "classify",
            system=system,
            parts=parts,
            schema=CLASSIFY_SCHEMA,
            name=TOOL_NAME,
            description="Registra a classificação de cada página do PDF recebido.",
            client=client,
            max_tokens=8000,
            effort="low",
        )
        result.update(_parse_batch(raw, start, count))

    pages: list[PageClass] = []
    for i in range(total):
        pages.append(
            result.get(i)
            or PageClass(
                page=i,
                doc_type=DocType.OUTROS,
                is_first_page_of_new_doc=False,
                confidence=0.0,
                short_title="",
            )
        )
    return pages


def _doc_id(source_file: Path, pages: list[int]) -> str:
    key = f"{Path(source_file).name}:{','.join(map(str, pages))}"
    return "d_" + hashlib.sha1(key.encode()).hexdigest()[:12]


def segment(pages: list[PageClass], source_file: Path) -> list[LogicalDoc]:
    """Agrupa páginas consecutivas do mesmo tipo; quebra em nova 1ª página ou mudança de tipo."""
    groups: list[list[PageClass]] = []
    for pc in sorted(pages, key=lambda p: p.page):
        if not groups or pc.is_first_page_of_new_doc or pc.doc_type != groups[-1][-1].doc_type:
            groups.append([pc])
        else:
            groups[-1].append(pc)

    docs: list[LogicalDoc] = []
    for g in groups:
        nums = [p.page for p in g]
        conf = min(p.confidence for p in g)
        title = g[0].short_title.strip() or LABELS[g[0].doc_type]
        docs.append(
            LogicalDoc(
                id=_doc_id(source_file, nums),
                doc_type=g[0].doc_type,
                title=title,
                source_file=Path(source_file),
                pages=nums,
                confidence=conf,
                needs_review=conf < REVIEW_THRESHOLD,
            )
        )
    return docs
