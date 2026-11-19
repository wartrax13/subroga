from pathlib import Path

import pytest
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4, landscape, letter
from reportlab.pdfgen import canvas

from app.doc_types import DOC_ORDER, LABELS, DocType
from app.pipeline.assemble_pdf import INDEX_ENTRIES_PER_PAGE, build_dossie, split_by_size
from app.schemas import CaseFacts, FactField, LogicalDoc


def _make_pdf(path: Path, n: int, prefix: str, size=A4, rotate: int = 0) -> Path:  # type: ignore[no-untyped-def]
    c = canvas.Canvas(str(path), pagesize=size, invariant=1)
    for i in range(n):
        c.setPageSize(size)
        c.setPageRotation(rotate)
        c.setFont("Helvetica", 12)
        c.drawString(72, 300, f"{prefix} página {i + 1}")
        c.showPage()
    c.save()
    return path


def _doc(id_: str, t: DocType, src: Path, pages: list[int], title: str | None = None) -> LogicalDoc:
    return LogicalDoc(id=id_, doc_type=t, title=title or f"Título {id_}", source_file=src,
                      pages=pages, confidence=0.95)


@pytest.fixture
def case(tmp_path: Path) -> tuple[list[LogicalDoc], CaseFacts, Path]:
    misto = _make_pdf(tmp_path / "doc final (2).pdf", 4, "MISTO")
    rot = _make_pdf(tmp_path / "laudo.pdf", 1, "LAUDO", rotate=90)
    carta = _make_pdf(tmp_path / "fotos.pdf", 2, "FOTOS", size=landscape(letter))
    proc = _make_pdf(tmp_path / "proc.pdf", 1, "PROC")
    docs = [
        _doc("d-outros", DocType.OUTROS, misto, [3]),
        _doc("d-comp", DocType.COMPROVANTE_PAGAMENTO, misto, [2], "TED R$ 48.320,00"),
        _doc("d-fotos", DocType.FOTOS, carta, [0, 1]),
        _doc("d-apol", DocType.APOLICE, misto, [0, 1], "Apólice nº 123 – condições"),
        _doc("d-laudo", DocType.LAUDO_TECNICO, rot, [0]),
        _doc("d-proc", DocType.PROCURACAO, proc, [0], "Procuração ad judicia"),
    ]
    facts = CaseFacts(seguradora_nome=FactField(value="Aurora Companhia de Seguros S.A."),
                      causador_nome=FactField(value="João Conceição"),
                      sinistro_numero=FactField(value="AUT-2023-0045871"),
                      apolice_numero=FactField(value="01.31.0098765"))
    return docs, facts, tmp_path / "out" / "dossie.pdf"


def test_order_pages_and_index(case) -> None:  # type: ignore[no-untyped-def]
    docs, facts, out = case
    res = build_dossie(docs, facts, out, case_label="Caso teste")
    reader = PdfReader(str(out))

    types = [next(d.doc_type for d in docs if d.id == e.doc_id) for e in res.index]
    assert types == sorted(types, key=DOC_ORDER.index)
    assert [e.doc_id for e in res.index] == ["d-proc", "d-apol", "d-laudo", "d-fotos", "d-comp", "d-outros"]

    expected_total = 1 + 1 + sum(1 + len(d.pages) for d in docs)
    assert res.total_pages == expected_total == len(reader.pages)

    fl = 3  # capa (1) + índice (2)
    for e, n in zip(res.index, [1, 2, 1, 2, 1, 1]):
        assert e.start_fl == fl and e.end_fl == fl + n
        assert e.label == LABELS[types[e.number - 1]]
        fl = e.end_fl + 1


def test_texts_bookmarks_and_stamps(case) -> None:  # type: ignore[no-untyped-def]
    docs, facts, out = case
    res = build_dossie(docs, facts, out)
    reader = PdfReader(str(out))

    cover = reader.pages[0].extract_text()
    assert "Aurora Companhia de Seguros S.A." in cover and "João Conceição" in cover
    assert "DOSSIÊ" in cover and "AUT-2023-0045871" in cover

    index_text = reader.pages[1].extract_text()
    assert "ÍNDICE" in index_text
    assert "Doc. 01 – Procuração: Procuração ad judicia" in index_text
    for e in res.index:
        assert f"fls. {e.start_fl}–{e.end_fl}" in index_text
    assert "Orçamentos" not in index_text and "Apólice nº 123" in index_text

    outline = reader.outline
    assert len(outline) == len(res.index)
    for item, e in zip(outline, res.index):
        assert item.title.startswith(f"Doc. {e.number:02d}")
        assert reader.get_destination_page_number(item) == e.start_fl - 1

    for e in res.index:
        sep = reader.pages[e.start_fl - 1].extract_text()
        assert f"Doc. {e.number:02d}" in sep and e.label in sep
        n = e.end_fl - e.start_fl
        for k in range(1, n + 1):
            txt = reader.pages[e.start_fl - 1 + k].extract_text()
            assert f"Doc. {e.number:02d} – fl. {k}/{n}" in txt
            assert f"fls. {e.start_fl + k}" in txt
    assert "Comprovante de pagamento da indenização" in reader.pages[res.index[4].start_fl - 1].extract_text()


def test_rotation_and_page_sizes_preserved(case) -> None:  # type: ignore[no-untyped-def]
    docs, facts, out = case
    res = build_dossie(docs, facts, out)
    reader = PdfReader(str(out))
    laudo = next(e for e in res.index if e.doc_id == "d-laudo")
    p = reader.pages[laudo.start_fl]  # 1ª página do laudo (rotacionada 90º na origem)
    def visual(pg) -> tuple[int, int]:  # type: ignore[no-untyped-def]
        w, h = round(float(pg.mediabox.width)), round(float(pg.mediabox.height))
        return (w, h) if pg.rotation % 180 == 0 else (h, w)

    src = PdfReader(str(next(d.source_file for d in docs if d.id == "d-laudo"))).pages[0]
    assert src.rotation == 90
    assert visual(p) == visual(src)  # mesma orientação visual da origem
    fotos = next(e for e in res.index if e.doc_id == "d-fotos")
    pf = reader.pages[fotos.start_fl]
    assert round(float(pf.mediabox.width)) == round(landscape(letter)[0])


def test_no_facts_placeholder_and_multi_page_index(tmp_path: Path) -> None:
    src = _make_pdf(tmp_path / "x.pdf", 1, "X")
    n = INDEX_ENTRIES_PER_PAGE + 5
    docs = [_doc(f"d{i}", DocType.OUTROS, src, [0]) for i in range(n)]
    out = tmp_path / "d.pdf"
    res = build_dossie(docs, None, out)
    reader = PdfReader(str(out))
    assert "[a preencher]" in reader.pages[0].extract_text()
    assert res.total_pages == 1 + 2 + 2 * n == len(reader.pages)
    assert res.index[0].start_fl == 4
    assert "ÍNDICE (continuação)" in reader.pages[2].extract_text()
    assert f"Doc. {n:02d}" in reader.pages[2].extract_text()
    assert reader.pages[res.index[-1].start_fl - 1].extract_text().count(f"Doc. {n:02d}") >= 1


def test_split_by_size(case) -> None:  # type: ignore[no-untyped-def]
    docs, facts, out = case
    build_dossie(docs, facts, out)
    assert split_by_size(out, max_mb=100) == [out]
    parts = split_by_size(out, max_mb=0.000001)  # força dividir: cada bloco vira uma parte
    total = sum(len(PdfReader(str(p)).pages) for p in parts)
    assert total == len(PdfReader(str(out)).pages)
    assert len(parts) == 1 + len(docs)  # capa+índice, depois um por documento
