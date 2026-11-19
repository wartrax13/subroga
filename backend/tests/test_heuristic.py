"""Provedor heurístico (sem rede) contra as fixtures sintéticas."""
import json
from pathlib import Path

import pytest

from app.doc_types import DocType, Scenario
from app.pipeline import classify as cl
from app.pipeline import extract as ex
from app.pipeline import heuristic as h

FIX = Path(__file__).parent / "fixtures"
SCENARIOS = ["auto", "eletrico", "transporte"]


@pytest.fixture(autouse=True)
def heuristic_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUBROGA_LLM_PROVIDER", "heuristic")


def _expected(s: str) -> dict:
    return json.loads((FIX / s / "expected.json").read_text(encoding="utf-8"))


def _docs(s: str) -> list:
    docs = []
    for name, spec in _expected(s)["files"].items():
        if spec["duplicate_of"]:
            continue
        path = FIX / s / name
        docs += cl.segment(cl.classify_pdf(path), path)
    return docs


@pytest.mark.parametrize("s", SCENARIOS)
def test_page_accuracy_and_breaks(s: str) -> None:
    exp = _expected(s)
    total = types_ok = breaks_ok = 0
    for name, spec in exp["files"].items():
        pages = cl.classify_pdf(FIX / s / name)
        for got, want in zip(pages, spec["pages"], strict=True):
            total += 1
            types_ok += got.doc_type.value == want["doc_type"]
            breaks_ok += got.is_first_page_of_new_doc == want["is_first_page_of_new_doc"]
    assert types_ok / total >= 0.8
    assert breaks_ok / total >= 0.8


@pytest.mark.parametrize("s", SCENARIOS)
def test_logical_docs_and_facts(s: str) -> None:
    exp = _expected(s)
    docs = _docs(s)
    got = sorted((Path(d.source_file).name, d.doc_type.value, tuple(d.pages)) for d in docs)
    want = sorted((d["file"], d["doc_type"], tuple(d["pages"])) for d in exp["logical_docs"])
    assert len(set(got) & set(want)) / len(want) >= 0.8

    facts = ex.extract_facts(docs, Scenario(exp["scenario"]))
    fields = {k: v for k, v in exp["facts"].items() if k != "scenario"}
    correct = sum(getattr(facts, k).value == v for k, v in fields.items())
    assert correct / len(fields) >= 0.8
    # rastreabilidade: todo campo preenchido aponta para um documento e página existentes
    by_id = {d.id: d for d in docs}
    for k in fields:
        ff = getattr(facts, k)
        if ff.value is not None:
            assert ff.source_doc_id in by_id and ff.page in by_id[ff.source_doc_id].pages
    assert facts.evidencias_culpa


@pytest.mark.parametrize("s", SCENARIOS)
def test_conflict_detected(s: str) -> None:
    exp = _expected(s)
    facts = ex.extract_facts(_docs(s), Scenario(exp["scenario"]))
    for conflict in exp["conflicts"]:
        alts = facts.valores_alternativos.get(conflict["field"])
        assert alts, f"conflito em {conflict['field']} não detectado"
        assert set(conflict["values"]) <= {a.value for a in alts}
    assert set(facts.valores_alternativos) <= {c["field"] for c in exp["conflicts"]}


def test_keyword_rules() -> None:
    assert h.classify_text("TERMO DE QUITAÇÃO E SUB-ROGAÇÃO DE DIREITOS\nRecebi...")[0] == DocType.TERMO_QUITACAO_SUBROGACAO
    assert h.classify_text("AVISO DE SINISTRO\nApólice: 123")[0] == DocType.AVISO_SINISTRO
    assert h.classify_text("REGISTRO FOTOGRÁFICO – VISTORIA")[0] == DocType.FOTOS
    assert h.classify_text("")[0] is None
    pages = h.classify_pages(["BOLETIM DE OCORRÊNCIA Nº 1", "BOLETIM DE OCORRÊNCIA Nº 1 – CONTINUAÇÃO", "xyz"])
    assert [p.is_first_page_of_new_doc for p in pages] == [True, False, True]
    assert pages[2].doc_type == DocType.OUTROS and pages[2].confidence == pytest.approx(0.3)
