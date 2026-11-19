"""Camada de provedores de IA do pipeline (OpenAI, Anthropic ou heurístico offline).

Seleção do provedor (lida do ambiente NA HORA DA CHAMADA, porque `backend/.env`
pode ser carregado depois do import deste módulo):

- `SUBROGA_LLM_PROVIDER` = openai | anthropic | heuristic;
- sem valor: openai se houver `OPENAI_API_KEY`, senão anthropic se houver
  `ANTHROPIC_API_KEY`, senão heuristic.

Saída estruturada:
- OpenAI: Responses API com `input_file` (PDF em base64) e `text.format` json_schema
  strict; se o cliente não tiver `responses`, usa Chat Completions com part `file`.
- Anthropic: tool use com uma única ferramenta strict (Haiku/Sonnet 5.5 não aceitam
  `tool_choice` forçado; usamos `auto` + instrução + nova tentativa).

Conteúdo neutro de provedor ("parts"): lista de dicts
  {"type": "text", "text": str} | {"type": "pdf", "data": bytes, "title": str | None}
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PROVIDERS = ("openai", "anthropic", "heuristic")
TASKS = ("classify", "extract", "narrative")

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# Preço aproximado (US$ por 1M tokens: entrada, saída) para estimar custo na avaliação.
PRICES: dict[str, tuple[float, float]] = {
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
}

# Registro de uso (tokens) de todas as chamadas do processo; a avaliação lê e zera.
USAGE: list[dict[str, Any]] = []


class LLMError(RuntimeError):
    pass


class RetryableLLMError(LLMError):
    """Resposta malformada (sem tool call, JSON vazio/inválido): vale tentar de novo."""


# ---------------------------------------------------------------- provedor/modelos

def provider_name() -> str:
    """Provedor efetivo segundo o ambiente atual."""
    explicit = (os.environ.get("SUBROGA_LLM_PROVIDER") or "").strip().lower()
    if explicit:
        if explicit not in PROVIDERS:
            raise LLMError(f"SUBROGA_LLM_PROVIDER inválido: {explicit!r} (use {', '.join(PROVIDERS)})")
        return explicit
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "heuristic"


def is_ready() -> bool:
    """True se o provedor escolhido tem o que precisa para rodar (chave, quando aplicável)."""
    try:
        p = provider_name()
    except LLMError:
        return False
    if p == "openai":
        return bool(os.environ.get("OPENAI_API_KEY"))
    if p == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    return True


def model_for(provider: str, task: str) -> str:
    """Modelo por provedor/tarefa (narrativa usa o modelo de extração)."""
    kind = "CLASSIFY" if task == "classify" else "EXTRACT"
    if provider == "openai":
        default = "gpt-4.1-mini" if kind == "CLASSIFY" else "gpt-4.1"
        return os.environ.get(f"SUBROGA_OPENAI_{kind}_MODEL") or default
    if provider == "anthropic":
        default = "claude-haiku-5-5" if kind == "CLASSIFY" else "claude-sonnet-5-5"
        return (
            os.environ.get(f"SUBROGA_ANTHROPIC_{kind}_MODEL")
            or os.environ.get(f"SUBROGA_{kind}_MODEL")
            or default
        )
    return "heuristic"


def provider_of_client(client: Any) -> str:
    """Descobre o provedor de um cliente injetado (útil em testes) por duck typing."""
    if hasattr(client, "responses") or (hasattr(client, "chat") and not hasattr(client, "messages")):
        return "openai"
    return "anthropic"


def resolve(client: Any = None) -> tuple[str, Any]:
    """(provedor, cliente). Cliente injetado define o provedor; heuristic devolve cliente None."""
    if client is not None:
        return provider_of_client(client), client
    p = provider_name()
    if p == "heuristic":
        return p, None
    return p, get_client(p)


def get_client(provider: str | None = None) -> Any:
    p = provider or provider_name()
    if p == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            raise LLMError("OPENAI_API_KEY ausente: defina a chave ou use SUBROGA_LLM_PROVIDER=heuristic")
        import openai

        return openai.OpenAI(max_retries=2, timeout=180)
    if p == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise LLMError("ANTHROPIC_API_KEY ausente: defina a chave ou use SUBROGA_LLM_PROVIDER=heuristic")
        import anthropic

        return anthropic.Anthropic(max_retries=3)
    raise LLMError("provedor heuristic não usa cliente de API")


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------- utilidades

def _attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


_KEY_RE = re.compile(r"(sk-|sk_|key-)[A-Za-z0-9_\-*]{4,}")


def _safe(err: Exception) -> str:
    """Mensagem do erro sem nada que pareça chave de API."""
    return _KEY_RE.sub("[chave omitida]", f"{type(err).__name__}: {err}")[:500]


def _is_transient(err: Exception) -> bool:
    status = _attr(err, "status_code")
    if isinstance(status, int):
        return status == 429 or status >= 500
    name = type(err).__name__
    return name in {
        "RateLimitError", "APIConnectionError", "APITimeoutError", "InternalServerError",
        "ServiceUnavailableError", "OverloadedError", "TimeoutError", "ConnectionError",
    }


def _record_usage(provider: str, model: str, task: str, resp: Any, elapsed: float) -> None:
    usage = _attr(resp, "usage")
    inp = _attr(usage, "input_tokens") or _attr(usage, "prompt_tokens") or 0
    out = _attr(usage, "output_tokens") or _attr(usage, "completion_tokens") or 0
    try:
        inp, out = int(inp), int(out)
    except (TypeError, ValueError):
        inp, out = 0, 0
    price = PRICES.get(model)
    cost = (inp * price[0] + out * price[1]) / 1e6 if price else None
    USAGE.append({"provider": provider, "model": model, "task": task, "input_tokens": inp,
                  "output_tokens": out, "cost_usd": cost, "seconds": round(elapsed, 2)})


def pdf_document_block(pdf_bytes: bytes, title: str | None = None) -> dict[str, Any]:
    """Bloco `document` da Anthropic."""
    block: dict[str, Any] = {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.standard_b64encode(pdf_bytes).decode("ascii"),
        },
    }
    if title:
        block["title"] = title
    return block


def text_part(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def pdf_part(data: bytes, title: str | None = None) -> dict[str, Any]:
    return {"type": "pdf", "data": data, "title": title}


def _anthropic_content(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in parts:
        if p["type"] == "pdf":
            out.append(pdf_document_block(p["data"], p.get("title")))
        else:
            out.append({"type": "text", "text": p["text"]})
    return out


def _data_url(data: bytes) -> str:
    return "data:application/pdf;base64," + base64.standard_b64encode(data).decode("ascii")


def _openai_responses_content(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, p in enumerate(parts):
        if p["type"] == "pdf":
            out.append({"type": "input_file", "filename": f"{p.get('title') or f'doc{i}'}.pdf",
                        "file_data": _data_url(p["data"])})
        else:
            out.append({"type": "input_text", "text": p["text"]})
    return out


def _openai_chat_content(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, p in enumerate(parts):
        if p["type"] == "pdf":
            out.append({"type": "file", "file": {"filename": f"{p.get('title') or f'doc{i}'}.pdf",
                                                 "file_data": _data_url(p["data"])}})
        else:
            out.append({"type": "text", "text": p["text"]})
    return out


def _parse_json(text: str | None) -> dict[str, Any]:
    if not text:
        raise RetryableLLMError("resposta vazia do modelo")
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    data = json.loads(s)
    if not isinstance(data, dict):
        raise RetryableLLMError("resposta JSON não é um objeto")
    return data


def _with_retry(fn: Any, what: str, attempts: int) -> Any:
    """Executa `fn()` com backoff em erros transitórios; erro definitivo vira LLMError."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except RetryableLLMError as e:  # resposta malformada: tenta de novo
            last = e
        except LLMError:
            raise
        except json.JSONDecodeError as e:
            last = RetryableLLMError(f"JSON inválido na resposta ({what}): {e}")
        except Exception as e:  # noqa: BLE001 - classificamos abaixo
            if not _is_transient(e):
                raise LLMError(f"falha definitiva na chamada ao modelo ({what}): {_safe(e)}") from None
            last = e
        if attempt < attempts - 1:
            time.sleep(min(2 ** attempt, 10))
    raise LLMError(f"falha após {attempts} tentativas ({what}): {_safe(last) if last else '?'}")


# ---------------------------------------------------------------- Anthropic

def call_tool(
    client: Any,
    *,
    model: str,
    system: str,
    content: list[dict[str, Any]],
    tool_name: str,
    tool_description: str,
    input_schema: dict[str, Any],
    max_tokens: int = 16000,
    effort: str | None = None,
    attempts: int = 3,
    task: str = "extract",
) -> dict[str, Any]:
    """Anthropic: exige uma chamada à ferramenta `tool_name` e devolve seu input."""
    tool = {"name": tool_name, "description": tool_description, "input_schema": input_schema, "strict": True}
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "tools": [tool],
        "tool_choice": {"type": "auto"},
        "messages": [{"role": "user", "content": content}],
    }
    if effort:
        kwargs["output_config"] = {"effort": effort}

    def once() -> dict[str, Any]:
        t0 = time.monotonic()
        resp = client.messages.create(**kwargs)
        _record_usage("anthropic", model, task, resp, time.monotonic() - t0)
        stop = _attr(resp, "stop_reason")
        if stop == "refusal":
            raise LLMError(f"modelo recusou a requisição ({tool_name})")
        for block in _attr(resp, "content") or []:
            if _attr(block, "type") == "tool_use" and _attr(block, "name") == tool_name:
                data = _attr(block, "input")
                if isinstance(data, dict):
                    return data
        raise RetryableLLMError(f"resposta sem chamada à ferramenta {tool_name} (stop_reason={stop})")

    return _with_retry(once, tool_name, attempts)


def call_text(
    client: Any,
    *,
    model: str,
    system: str,
    content: list[dict[str, Any]],
    max_tokens: int = 16000,
    effort: str | None = None,
    attempts: int = 3,
) -> str:
    """Anthropic: resposta em texto livre."""
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": content}],
    }
    if effort:
        kwargs["output_config"] = {"effort": effort}

    def once() -> str:
        t0 = time.monotonic()
        resp = client.messages.create(**kwargs)
        _record_usage("anthropic", model, "narrative", resp, time.monotonic() - t0)
        if _attr(resp, "stop_reason") == "refusal":
            raise LLMError("modelo recusou a requisição (texto)")
        parts = [_attr(b, "text") or "" for b in _attr(resp, "content") or [] if _attr(b, "type") == "text"]
        return "".join(parts).strip()

    return _with_retry(once, "texto", attempts)


# ---------------------------------------------------------------- OpenAI

def _openai_output_text(resp: Any) -> str:
    status = _attr(resp, "status")
    if status == "incomplete":
        details = _attr(resp, "incomplete_details")
        raise LLMError(f"resposta incompleta da OpenAI ({_attr(details, 'reason')})")
    text = _attr(resp, "output_text")
    if isinstance(text, str) and text:
        return text
    chunks: list[str] = []
    for item in _attr(resp, "output") or []:
        for c in _attr(item, "content") or []:
            ctype = _attr(c, "type")
            if ctype == "refusal":
                raise LLMError(f"modelo recusou a requisição: {_attr(c, 'refusal')}")
            if ctype == "output_text":
                chunks.append(_attr(c, "text") or "")
    return "".join(chunks)


def _openai_chat_text(resp: Any) -> str:
    choices = _attr(resp, "choices") or []
    if not choices:
        raise LLMError("resposta da OpenAI sem choices")
    msg = _attr(choices[0], "message")
    if _attr(msg, "refusal"):
        raise LLMError(f"modelo recusou a requisição: {_attr(msg, 'refusal')}")
    if _attr(choices[0], "finish_reason") == "length":
        raise LLMError("resposta truncada (max_tokens)")
    return _attr(msg, "content") or ""


def _supports_temperature(model: str) -> bool:
    """Modelos de raciocínio (o1/o3/o4/gpt-5) rejeitam `temperature`."""
    return model.startswith(("gpt-4", "gpt-3.5"))


def openai_call(
    client: Any,
    *,
    model: str,
    system: str,
    parts: list[dict[str, Any]],
    schema: dict[str, Any] | None,
    name: str = "resposta",
    max_tokens: int = 16000,
    attempts: int = 3,
    task: str = "extract",
) -> Any:
    """OpenAI: devolve dict (com schema) ou texto (sem schema)."""
    use_responses = hasattr(client, "responses")

    def once() -> Any:
        t0 = time.monotonic()
        if use_responses:
            kwargs: dict[str, Any] = {
                "model": model,
                "instructions": system,
                "input": [{"role": "user", "content": _openai_responses_content(parts)}],
                "max_output_tokens": max_tokens,
            }
            if _supports_temperature(model):
                kwargs["temperature"] = 0
            if schema is not None:
                kwargs["text"] = {"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}}
            resp = client.responses.create(**kwargs)
            text = _openai_output_text(resp)
        else:
            kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": _openai_chat_content(parts)},
                ],
                "max_completion_tokens": max_tokens,
            }
            if _supports_temperature(model):
                kwargs["temperature"] = 0
            if schema is not None:
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": name, "schema": schema, "strict": True},
                }
            resp = client.chat.completions.create(**kwargs)
            text = _openai_chat_text(resp)
        _record_usage("openai", model, task, resp, time.monotonic() - t0)
        if schema is None:
            return (text or "").strip()
        return _parse_json(text)

    return _with_retry(once, name, attempts)


# ---------------------------------------------------------------- API única

def structured_call(
    task: str,
    *,
    system: str,
    parts: list[dict[str, Any]],
    schema: dict[str, Any],
    name: str,
    description: str = "",
    client: Any = None,
    max_tokens: int = 16000,
    effort: str | None = None,
) -> dict[str, Any]:
    """Chamada com saída estruturada no provedor do ambiente (ou do cliente injetado)."""
    provider, client = resolve(client)
    if provider == "heuristic":
        raise LLMError("structured_call não se aplica ao provedor heuristic")
    model = model_for(provider, task)
    if provider == "openai":
        return openai_call(client, model=model, system=system, parts=parts, schema=schema,
                           name=name, max_tokens=max_tokens, task=task)
    return call_tool(
        client, model=model, system=system, content=_anthropic_content(parts), tool_name=name,
        tool_description=description or name, input_schema=schema, max_tokens=max_tokens,
        effort=effort, task=task,
    )


def text_call(
    task: str,
    *,
    system: str,
    parts: list[dict[str, Any]],
    client: Any = None,
    max_tokens: int = 8000,
    effort: str | None = None,
) -> str:
    """Chamada com resposta em texto livre no provedor do ambiente (ou do cliente injetado)."""
    provider, client = resolve(client)
    if provider == "heuristic":
        raise LLMError("text_call não se aplica ao provedor heuristic")
    model = model_for(provider, task)
    if provider == "openai":
        return openai_call(client, model=model, system=system, parts=parts, schema=None,
                           name=task, max_tokens=max_tokens, task=task)
    return call_text(client, model=model, system=system, content=_anthropic_content(parts),
                     max_tokens=max_tokens, effort=effort)


def usage_summary(entries: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    entries = USAGE if entries is None else entries
    costs = [e["cost_usd"] for e in entries if e["cost_usd"] is not None]
    return {
        "calls": len(entries),
        "input_tokens": sum(e["input_tokens"] for e in entries),
        "output_tokens": sum(e["output_tokens"] for e in entries),
        "cost_usd": round(sum(costs), 4) if costs else None,
        "models": sorted({e["model"] for e in entries}),
    }
