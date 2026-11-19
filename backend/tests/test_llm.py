"""Camada de provedores: seleção por ambiente, OpenAI (fake), Anthropic (fake), retry e erros."""
import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from app.doc_types import DocType, Scenario
from app.pipeline import classify as cl
from app.pipeline import draft_petition as dp
from app.pipeline import extract as ex
from app.pipeline import llm
from app.schemas import CaseFacts, LogicalDoc


def make_pdf(path: Path, n: int) -> Path:
    c = canvas.Canvas(str(path))
    for i in range(n):
        c.drawString(72, 720, f"pagina {i}")
        c.showPage()
    c.save()
    return path


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("SUBROGA_LLM_PROVIDER", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
              "SUBROGA_OPENAI_CLASSIFY_MODEL", "SUBROGA_OPENAI_EXTRACT_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)


# ------------------------------------------------------------------ seleção

def test_provider_auto_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    assert llm.provider_name() == "heuristic" and llm.is_ready()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert llm.provider_name() == "anthropic"
    monkeypatch.setenv("OPENAI_API_KEY", "y")
    assert llm.provider_name() == "openai" and llm.is_ready()


def test_provider_explicit_and_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUBROGA_LLM_PROVIDER", "openai")
    assert llm.provider_name() == "openai"
    assert not llm.is_ready()  # sem chave
    with pytest.raises(llm.LLMError, match="OPENAI_API_KEY"):
        llm.get_client()
    monkeypatch.setenv("SUBROGA_LLM_PROVIDER", "Heuristic")
    assert llm.provider_name() == "heuristic"
    monkeypatch.setenv("SUBROGA_LLM_PROVIDER", "gemini")
    assert not llm.is_ready()
    with pytest.raises(llm.LLMError):
        llm.provider_name()


def test_models_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    assert llm.model_for("openai", "classify") == "gpt-4.1-mini"
    assert llm.model_for("openai", "extract") == "gpt-4.1"
    assert llm.model_for("openai", "narrative") == "gpt-4.1"
    assert llm.model_for("anthropic", "classify") == "claude-haiku-5-5"
    assert llm.model_for("anthropic", "extract") == "claude-sonnet-5-5"
    monkeypatch.setenv("SUBROGA_OPENAI_EXTRACT_MODEL", "gpt-4o")
    assert llm.model_for("openai", "extract") == "gpt-4o"


# ------------------------------------------------------------------ fakes OpenAI

class FakeResponses:
    """Imita client.responses.create; `handler(kwargs)` devolve o dict de saída (ou levanta)."""

    def __init__(self, handler: Any):
        self.handler = handler
        self.calls: list[dict[str, Any]] = []
        self.responses = self

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        out = self.handler(kwargs)
        text = out if isinstance(out, str) else json.dumps(out)
        return SimpleNamespace(status="completed", output_text=text,
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=100))


class FakeChat:
    """Imita client.chat.completions.create (sem Responses API)."""

    def __init__(self, out: dict[str, Any]):
        self.out = out
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        msg = SimpleNamespace(content=json.dumps(self.out), refusal=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")],
                               usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))


def _pdf_pages_in(kwargs: dict[str, Any]) -> int:
    content = kwargs["input"][0]["content"]
    f = next(c for c in content if c["type"] == "input_file")
    assert f["file_data"].startswith("data:application/pdf;base64,")
    return len(PdfReader(io.BytesIO(base64.b64decode(f["file_data"].split(",", 1)[1]))).pages)


def _assert_strict(schema: dict[str, Any]) -> None:
    """Regras do strict mode da OpenAI: additionalProperties false e todos os campos required."""
    if schema.get("type") == "object" or "properties" in schema:
        assert schema.get("additionalProperties") is False
        assert set(schema["required"]) == set(schema["properties"])
        for sub in schema["properties"].values():
            _assert_strict(sub)
    if "items" in schema:
        _assert_strict(schema["items"])


def test_schemas_are_openai_strict() -> None:
    _assert_strict(cl.CLASSIFY_SCHEMA)
    _assert_strict(ex.EXTRACT_SCHEMA)


def test_openai_classify_via_responses(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "a.pdf", 25)

    def handler(kw: dict[str, Any]) -> dict[str, Any]:
        n = _pdf_pages_in(kw)
        return {"pages": [{"page": i, "doc_type": "APOLICE", "is_first_page_of_new_doc": i == 0,
                           "confidence": 0.9, "short_title": "Apólice"} for i in range(n)]}

    client = FakeResponses(handler)
    pages = cl.classify_pdf(pdf, client=client)
    assert len(client.calls) == 2  # lotes de 20 + 5
    assert [p.page for p in pages] == list(range(25))
    assert pages[20].is_first_page_of_new_doc and pages[20].doc_type == DocType.APOLICE
    kw = client.calls[0]
    fmt = kw["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True and fmt["name"] == cl.TOOL_NAME
    assert kw["model"] == "gpt-4.1-mini" and kw["instructions"].startswith("<!-- versão: classify")
    hints = next(c["text"] for c in kw["input"][0]["content"] if c["type"] == "input_text")
    assert "--- página 0 ---" in hints and "pagina 0" in hints
    assert llm.USAGE[-1]["provider"] == "openai" and llm.USAGE[-1]["cost_usd"] > 0


def test_openai_extract_and_narrative(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "c.pdf", 2)
    docs = [
        LogicalDoc(id="d_comp", doc_type=DocType.COMPROVANTE_PAGAMENTO, title="TED", source_file=pdf,
                   pages=[0], confidence=0.9),
        LogicalDoc(id="d_termo", doc_type=DocType.TERMO_QUITACAO_SUBROGACAO, title="Termo", source_file=pdf,
                   pages=[1], confidence=0.9),
    ]
    raw = {n: {"value": None, "source_doc_id": None, "page": None} for n in ex.FACT_FIELDS}
    raw["valor_pago"] = {"value": "R$ 1.000,00", "source_doc_id": "d_comp", "page": 0}
    raw["evidencias_culpa"] = []
    raw["valores_alternativos"] = [
        {"campo": "valor_pago", "value": "1000.00", "source_doc_id": "d_comp", "page": 0},
        {"campo": "valor_pago", "value": "1100.00", "source_doc_id": "d_termo", "page": 1},
    ]
    client = FakeResponses(lambda kw: raw)
    facts = ex.extract_facts(docs, Scenario.AUTO, client=client)
    assert facts.valor_pago.value == "1000.00"
    assert {a.value for a in facts.valores_alternativos["valor_pago"]} == {"1000.00", "1100.00"}
    kw = client.calls[0]
    assert kw["model"] == "gpt-4.1"
    assert sum(1 for c in kw["input"][0]["content"] if c["type"] == "input_file") == 2

    text_client = FakeResponses(lambda kw: "Parágrafo um (Doc. 01).\n\nParágrafo dois.")
    out = dp.write_narrative(facts, [], Scenario.AUTO, client=text_client)
    assert out.startswith("Parágrafo um") and "text" not in text_client.calls[0]


def test_openai_chat_completions_fallback(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "b.pdf", 1)
    out = {"pages": [{"page": 0, "doc_type": "FOTOS", "is_first_page_of_new_doc": True,
                      "confidence": 0.8, "short_title": "Fotos"}]}
    client = FakeChat(out)
    pages = cl.classify_pdf(pdf, client=client)
    assert pages[0].doc_type == DocType.FOTOS
    kw = client.calls[0]
    assert kw["response_format"]["json_schema"]["strict"] is True
    part = next(c for c in kw["messages"][1]["content"] if c["type"] == "file")
    assert part["file"]["file_data"].startswith("data:application/pdf;base64,")


def test_env_selects_openai_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pdf = make_pdf(tmp_path / "e.pdf", 1)
    fake = FakeResponses(lambda kw: {"pages": [{"page": 0, "doc_type": "PROCURACAO",
                                                "is_first_page_of_new_doc": True, "confidence": 1,
                                                "short_title": ""}]})
    monkeypatch.setenv("OPENAI_API_KEY", "teste")
    seen: list[str] = []
    monkeypatch.setattr(llm, "get_client", lambda p=None: seen.append(p) or fake)
    assert cl.classify_pdf(pdf)[0].doc_type == DocType.PROCURACAO
    assert seen == ["openai"]


# ------------------------------------------------------------------ retry / erros

class StatusError(Exception):
    def __init__(self, status: int, msg: str = "erro"):
        super().__init__(msg)
        self.status_code = status


def test_retry_on_transient_then_success(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "r.pdf", 1)
    state = {"n": 0}

    def handler(kw: dict[str, Any]) -> Any:
        state["n"] += 1
        if state["n"] == 1:
            raise StatusError(429)
        if state["n"] == 2:
            return "{não é json"
        return {"pages": [{"page": 0, "doc_type": "APOLICE", "is_first_page_of_new_doc": True,
                           "confidence": 0.9, "short_title": ""}]}

    client = FakeResponses(handler)
    assert cl.classify_pdf(pdf, client=client)[0].doc_type == DocType.APOLICE
    assert state["n"] == 3


def test_definitive_error_raises_without_leaking_key(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "x.pdf", 1)
    calls = {"n": 0}

    def handler(kw: dict[str, Any]) -> Any:
        calls["n"] += 1
        raise StatusError(401, "Incorrect API key provided: sk-proj-abcdef123456")

    with pytest.raises(llm.LLMError) as ei:
        cl.classify_pdf(pdf, client=FakeResponses(handler))
    assert calls["n"] == 1  # sem retry em erro definitivo
    assert "sk-proj" not in str(ei.value) and "definitiva" in str(ei.value)


def test_transient_exhausted(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "y.pdf", 1)
    client = FakeResponses(lambda kw: (_ for _ in ()).throw(StatusError(503)))
    with pytest.raises(llm.LLMError, match="3 tentativas"):
        cl.classify_pdf(pdf, client=client)
    assert len(client.calls) == 3


def test_anthropic_retry_when_no_tool_call(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "z.pdf", 1)

    class Fake:
        def __init__(self) -> None:
            self.messages = self
            self.n = 0

        def create(self, **kw: Any) -> SimpleNamespace:
            self.n += 1
            if self.n == 1:
                return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="oi")])
            block = SimpleNamespace(type="tool_use", name=kw["tools"][0]["name"], input={"pages": [
                {"page": 0, "doc_type": "FOTOS", "is_first_page_of_new_doc": True, "confidence": 0.9,
                 "short_title": ""}]})
            return SimpleNamespace(stop_reason="tool_use", content=[block])

    fake = Fake()
    assert cl.classify_pdf(pdf, client=fake)[0].doc_type == DocType.FOTOS and fake.n == 2


def test_heuristic_provider_needs_no_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUBROGA_LLM_PROVIDER", "heuristic")
    monkeypatch.setattr(llm, "get_client", lambda p=None: pytest.fail("não deveria criar cliente"))
    pdf = make_pdf(tmp_path / "h.pdf", 2)
    pages = cl.classify_pdf(pdf)
    assert [p.doc_type for p in pages] == [DocType.OUTROS, DocType.OUTROS]
    assert all(p.confidence < cl.REVIEW_THRESHOLD for p in pages)
    text = dp.write_narrative(CaseFacts(scenario=Scenario.AUTO), [], Scenario.AUTO)
    assert "[[REVISAR: segurado_nome]]" in text
