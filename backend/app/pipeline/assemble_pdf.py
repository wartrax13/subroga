"""Montagem do dossiê PDF (PLANO_MVP 6.1): capa, índice, separadoras, carimbo e bookmarks."""
from __future__ import annotations

import io
import math
from datetime import date
from pathlib import Path

from pypdf import PageObject, PdfReader, PdfWriter, Transformation
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.doc_types import DOC_ORDER, LABELS
from app.schemas import CaseFacts, DossieResult, FactField, IndexEntry, LogicalDoc

INDEX_ENTRIES_PER_PAGE = 28
PLACEHOLDER = "[a preencher]"
FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"


# ---------------------------------------------------------------- helpers

def doc_code(n: int) -> str:
    return f"Doc. {n:02d}"


def sort_docs(docs: list[LogicalDoc]) -> list[LogicalDoc]:
    """Ordena pela ordem de DOC_ORDER (estável). Tipos fora da ordem vão por último."""
    order = {t: i for i, t in enumerate(DOC_ORDER)}
    return sorted(docs, key=lambda d: order.get(d.doc_type, len(order)))


def index_page_count(n_docs: int) -> int:
    return max(1, math.ceil(n_docs / INDEX_ENTRIES_PER_PAGE))


def _fv(field: FactField | None) -> str:
    if field is None or not field.value:
        return PLACEHOLDER
    return field.value


def _fmt_date(v: str) -> str:
    """'AAAA-MM-DD' -> 'DD/MM/AAAA' (outros formatos passam intactos)."""
    parts = v.split("-")
    if len(parts) == 3 and len(parts[0]) == 4 and all(p.isdigit() for p in parts):
        return f"{parts[2]}/{parts[1]}/{parts[0]}"
    return v


def _fmt_money(v: str) -> str:
    """'48320.00' -> 'R$ 48.320,00' (outros formatos passam intactos)."""
    try:
        n = float(v)
    except ValueError:
        return v
    return "R$ " + f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fit(c: canvas.Canvas, text: str, font: str, size: float, max_w: float) -> str:
    """Trunca o texto com reticências para caber em max_w."""
    if c.stringWidth(text, font, size) <= max_w:
        return text
    while text and c.stringWidth(text + "…", font, size) > max_w:
        text = text[:-1]
    return text + "…"


def _wrap(c: canvas.Canvas, text: str, font: str, size: float, max_w: float) -> list[str]:
    lines: list[str] = []
    cur = ""
    for word in text.split():
        cand = f"{cur} {word}".strip()
        if c.stringWidth(cand, font, size) <= max_w or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _corner_fl(c: canvas.Canvas, fl: int, width: float, height: float) -> None:
    c.setFont(FONT, 9)
    c.drawRightString(width - 28, height - 24, f"fls. {fl}")


def _single_page(draw) -> PageObject:  # type: ignore[no-untyped-def]
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    draw(c)
    c.showPage()
    c.save()
    buf.seek(0)
    return PdfReader(buf).pages[0]


# ---------------------------------------------------------------- páginas geradas

def _cover_page(facts: CaseFacts | None, case_label: str | None) -> PageObject:
    w, h = A4
    f = facts or CaseFacts()
    autora = _fv(f.seguradora_nome)
    reu = _fv(f.causador_nome)

    def draw(c: canvas.Canvas) -> None:
        _corner_fl(c, 1, w, h)
        c.setFont(FONT_BOLD, 22)
        c.drawCentredString(w / 2, h - 170, "DOSSIÊ DOCUMENTAL")
        c.setFont(FONT, 13)
        c.drawCentredString(w / 2, h - 195, "Ação regressiva de ressarcimento – sub-rogação (art. 786 do CC)")
        if case_label:
            c.setFont(FONT, 11)
            c.drawCentredString(w / 2, h - 218, _fit(c, f"Caso: {case_label}", FONT, 11, w - 120))

        y = h - 290
        c.setFont(FONT_BOLD, 14)
        for line in _wrap(c, autora, FONT_BOLD, 14, w - 120):
            c.drawCentredString(w / 2, y, line)
            y -= 18
        c.setFont(FONT, 12)
        c.drawCentredString(w / 2, y - 4, "×")
        y -= 30
        c.setFont(FONT_BOLD, 14)
        for line in _wrap(c, reu, FONT_BOLD, 14, w - 120):
            c.drawCentredString(w / 2, y, line)
            y -= 18

        y -= 40
        rows = [
            ("Sinistro nº", _fv(f.sinistro_numero)),
            ("Apólice nº", _fv(f.apolice_numero)),
            ("Segurado", _fv(f.segurado_nome)),
            ("Data do sinistro", _fmt_date(_fv(f.sinistro_data))),
            ("Valor indenizado", _fmt_money(_fv(f.valor_pago))),
            ("Gerado em", date.today().strftime("%d/%m/%Y")),
        ]
        for k, v in rows:
            c.setFont(FONT_BOLD, 11)
            c.drawString(110, y, f"{k}:")
            c.setFont(FONT, 11)
            c.drawString(230, y, _fit(c, v, FONT, 11, w - 230 - 80))
            y -= 20

    return _single_page(draw)


def _index_pages(entries: list[IndexEntry], n_pages: int) -> list[PageObject]:
    w, h = A4
    left, right = 60, w - 60
    pages: list[PageObject] = []
    for p in range(n_pages):
        chunk = entries[p * INDEX_ENTRIES_PER_PAGE:(p + 1) * INDEX_ENTRIES_PER_PAGE]
        fl = 2 + p

        def draw(c: canvas.Canvas, chunk: list[IndexEntry] = chunk, fl: int = fl, p: int = p) -> None:
            _corner_fl(c, fl, w, h)
            c.setFont(FONT_BOLD, 16)
            title = "ÍNDICE" if p == 0 else "ÍNDICE (continuação)"
            c.drawCentredString(w / 2, h - 80, title)
            y = h - 120
            size = 10
            for e in chunk:
                fls = f"fls. {e.start_fl}–{e.end_fl}"
                c.setFont(FONT, size)
                fls_w = c.stringWidth(fls, FONT, size)
                text = _fit(c, f"{doc_code(e.number)} – {e.label}: {e.title}", FONT, size,
                            right - left - fls_w - 30)
                text_w = c.stringWidth(text, FONT, size)
                c.drawString(left, y, text)
                c.drawRightString(right, y, fls)
                dot_w = c.stringWidth(".", FONT, size)
                n_dots = int((right - fls_w - 6 - (left + text_w + 6)) / dot_w)
                if n_dots > 0:
                    c.drawString(left + text_w + 4, y, "." * n_dots)
                y -= 22

        pages.append(_single_page(draw))
    return pages


def _separator_page(entry: IndexEntry, n_pages: int) -> PageObject:
    w, h = A4

    def draw(c: canvas.Canvas) -> None:
        _corner_fl(c, entry.start_fl, w, h)
        c.setFont(FONT_BOLD, 40)
        c.drawCentredString(w / 2, h / 2 + 60, doc_code(entry.number))
        c.setFont(FONT_BOLD, 18)
        y = h / 2 + 10
        for line in _wrap(c, entry.label, FONT_BOLD, 18, w - 120):
            c.drawCentredString(w / 2, y, line)
            y -= 24
        c.setFont(FONT, 13)
        for line in _wrap(c, entry.title, FONT, 13, w - 140):
            c.drawCentredString(w / 2, y - 6, line)
            y -= 18
        c.setFont(FONT, 10)
        plural = "s" if n_pages != 1 else ""
        first = entry.start_fl + 1
        fls = f"fls. {first}" if first == entry.end_fl else f"fls. {first}–{entry.end_fl}"
        c.drawCentredString(w / 2, y - 30, f"{n_pages} página{plural} – {fls}")

    return _single_page(draw)


def _stamp_page(page: PageObject, number: int, x: int, y: int, fl: int) -> PageObject:
    """Aplica carimbo no rodapé e nº de folha no canto, respeitando rotação e mediabox."""
    if page.rotation:
        page.transfer_rotation_to_content()
    box = page.mediabox
    x0, y0 = float(box.left), float(box.bottom)
    w, h = float(box.width), float(box.height)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(w, h))
    label = f"{doc_code(number)} – fl. {x}/{y}"
    size = 9
    tw = c.stringWidth(label, FONT_BOLD, size)
    c.setFillColorRGB(1, 1, 1)
    c.rect(w / 2 - tw / 2 - 6, 10, tw + 12, 16, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0)
    c.setFont(FONT_BOLD, size)
    c.drawCentredString(w / 2, 15, label)
    fl_txt = f"fls. {fl}"
    fw = c.stringWidth(fl_txt, FONT, size)
    c.setFillColorRGB(1, 1, 1)
    c.rect(w - 28 - fw - 4, h - 28, fw + 8, 14, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0)
    c.setFont(FONT, size)
    c.drawRightString(w - 28, h - 24, fl_txt)
    c.showPage()
    c.save()
    buf.seek(0)
    overlay = PdfReader(buf).pages[0]
    page.merge_transformed_page(overlay, Transformation().translate(x0, y0))
    return page


# ---------------------------------------------------------------- API pública

def plan_index(docs: list[LogicalDoc]) -> tuple[list[LogicalDoc], list[IndexEntry], int]:
    """Calcula ordem, entradas do índice e nº de folhas do índice, sem montar o PDF."""
    ordered = sort_docs(docs)
    n_idx = index_page_count(len(ordered))
    fl = 1 + n_idx  # capa + índice
    entries: list[IndexEntry] = []
    for i, d in enumerate(ordered, start=1):
        start = fl + 1
        end = start + len(d.pages)
        entries.append(IndexEntry(number=i, doc_id=d.id, label=LABELS[d.doc_type],
                                  title=d.title, start_fl=start, end_fl=end))
        fl = end
    return ordered, entries, n_idx


def build_dossie(docs: list[LogicalDoc], facts: CaseFacts | None, out_path: Path,
                 case_label: str | None = None) -> DossieResult:
    out_path = Path(out_path)
    ordered, entries, n_idx = plan_index(docs)

    writer = PdfWriter()
    writer.add_page(_cover_page(facts, case_label))
    for p in _index_pages(entries, n_idx):
        writer.add_page(p)

    readers: dict[Path, PdfReader] = {}
    for doc, entry in zip(ordered, entries):
        sep_index = len(writer.pages)
        writer.add_page(_separator_page(entry, len(doc.pages)))
        src = Path(doc.source_file)
        reader = readers.get(src)
        if reader is None:
            reader = readers[src] = PdfReader(str(src))
        total = len(doc.pages)
        for k, pi in enumerate(doc.pages, start=1):
            added = writer.add_page(reader.pages[pi])
            _stamp_page(added, entry.number, k, total, entry.start_fl + k)
        writer.add_outline_item(f"{doc_code(entry.number)} – {entry.label}: {entry.title}", sep_index)

    total_pages = len(writer.pages)
    writer.page_mode = "/UseOutlines"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        writer.write(f)
    return DossieResult(path=out_path, index=entries, total_pages=total_pages)


def split_by_size(path: Path, max_mb: float = 10.0) -> list[Path]:
    """Divide o dossiê em partes de até max_mb sem quebrar documentos.

    Usa os bookmarks de primeiro nível como fronteiras (capa+índice ficam na parte 1).
    Um documento maior que o limite sozinho vira uma parte própria (acima do limite).
    """
    path = Path(path)
    limit = max_mb * 1024 * 1024
    if path.stat().st_size <= limit:
        return [path]

    reader = PdfReader(str(path))
    n = len(reader.pages)
    starts = sorted({reader.get_destination_page_number(o) for o in reader.outline
                     if not isinstance(o, list)})
    bounds = [0] + [s for s in starts if s > 0] + [n]
    blocks = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1) if bounds[i] < bounds[i + 1]]

    def size_of(page_range: list[int]) -> int:
        w = PdfWriter()
        for i in page_range:
            w.add_page(reader.pages[i])
        b = io.BytesIO()
        w.write(b)
        return b.tell()

    groups: list[list[int]] = []
    cur: list[int] = []
    for a, b in blocks:
        cand = cur + list(range(a, b))
        if cur and size_of(cand) > limit:
            groups.append(cur)
            cur = list(range(a, b))
        else:
            cur = cand
    if cur:
        groups.append(cur)

    parts: list[Path] = []
    for i, g in enumerate(groups, start=1):
        w = PdfWriter()
        for pi in g:
            w.add_page(reader.pages[pi])
        out = path.with_name(f"{path.stem}_parte{i:02d}.pdf")
        with open(out, "wb") as f:
            w.write(f)
        parts.append(out)
    return parts
