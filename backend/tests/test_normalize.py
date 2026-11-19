from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.pipeline.normalize import dedupe_files, file_sha256, page_count, page_hashes, render_page_png


def _make_pdf(path: Path, texts: list[str], title: str = "x") -> Path:
    c = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    c.setTitle(title)
    for t in texts:
        c.setFont("Helvetica", 12)
        c.drawString(72, 700, t)
        c.showPage()
    c.save()
    return path


@pytest.fixture
def pdfs(tmp_path: Path) -> dict[str, Path]:
    a = _make_pdf(tmp_path / "a.pdf", ["Apólice nº 1", "Condições gerais"], title="a")
    b = _make_pdf(tmp_path / "b.pdf", ["Boletim de ocorrência", "Apólice nº 1"], title="b")
    a_copy = tmp_path / "scan001 (1).pdf"
    a_copy.write_bytes(a.read_bytes())
    return {"a": a, "b": b, "a_copy": a_copy}


def test_file_sha256_and_page_count(pdfs: dict[str, Path]) -> None:
    assert file_sha256(pdfs["a"]) == file_sha256(pdfs["a_copy"])
    assert file_sha256(pdfs["a"]) != file_sha256(pdfs["b"])
    assert len(file_sha256(pdfs["a"])) == 64
    assert page_count(pdfs["a"]) == 2


def test_page_hashes_detect_same_page_across_files(pdfs: dict[str, Path]) -> None:
    ha, hb = page_hashes(pdfs["a"]), page_hashes(pdfs["b"])
    assert len(ha) == 2 and len(hb) == 2
    assert ha[0] == hb[1]  # "Apólice nº 1" aparece nos dois arquivos (metadados diferentes)
    assert ha[0] != ha[1]


def test_dedupe_files(pdfs: dict[str, Path]) -> None:
    unique, dups = dedupe_files([pdfs["a"], pdfs["b"], pdfs["a_copy"]])
    assert unique == [pdfs["a"], pdfs["b"]]
    assert dups == [pdfs["a_copy"]]


def test_render_page_png(pdfs: dict[str, Path]) -> None:
    png = render_page_png(pdfs["a"], 1, scale=0.5)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.skipif(not (FIXTURES / "auto").exists(), reason="rode scripts/make_fixtures.py")
@pytest.mark.parametrize("scenario", ["auto", "eletrico", "transporte"])
def test_fixture_duplicates_detected(scenario: str) -> None:
    import json

    folder = FIXTURES / scenario
    expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
    files = sorted(folder.glob("*.pdf"), key=lambda p: (p.name in expected["duplicates"], p.name))
    _, dups = dedupe_files(files)
    assert {p.name for p in dups} == set(expected["duplicates"])
    for name, spec in expected["files"].items():
        assert page_count(folder / name) == len(spec["pages"])
