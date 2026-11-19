"""Avalia classify + segment + extract contra as fixtures sintéticas (expected.json).

Uso (de backend/):
    uv run python scripts/eval_fixtures.py --provider heuristic
    OPENAI_API_KEY=... uv run python scripts/eval_fixtures.py --provider openai [--scenario auto]

Métricas por cenário: acurácia de doc_type por página, acurácia de quebra de documento
(is_first_page_of_new_doc), documentos lógicos idênticos ao gabarito, campos de facts
corretos (com e sem os campos cujo valor esperado nem aparece nos PDFs), detecção do
conflito esperado (e conflitos espúrios), checklist de faltantes, tempo e custo aproximado.
Relatório: storage/eval/report_<provider>.json. Arquivos marcados como duplicata no
gabarito são pulados (o pipeline real os descarta por hash).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pypdf import PdfReader  # noqa: E402

from app.checklists import REQUIRED  # noqa: E402
from app.doc_types import Scenario  # noqa: E402
from app.pipeline import classify as cl  # noqa: E402
from app.pipeline import extract as ex  # noqa: E402
from app.pipeline import llm  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
SCENARIOS = ["auto", "eletrico", "transporte"]


def norm(v: Any) -> str:
    s = unicodedata.normalize("NFKD", str(v or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    return re.sub(r"[^a-z0-9]+", "", s)


def surface_forms(field: str, value: str) -> list[str]:
    """Como o valor esperado apareceria escrito no documento."""
    forms = [value]
    if field in ex.DATE_FIELDS:
        try:
            forms.append(date.fromisoformat(value).strftime("%d/%m/%Y"))
        except ValueError:
            pass
    if field in ex.MONEY_FIELDS:
        n = float(value)
        forms.append(f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    return forms


def eval_scenario(key: str) -> dict[str, Any]:
    exp = json.loads((FIX / key / "expected.json").read_text(encoding="utf-8"))
    scenario = Scenario(exp["scenario"])
    usage_start = len(llm.USAGE)
    t0 = time.monotonic()
    out: dict[str, Any] = {"scenario": exp["scenario"], "pages": [], "errors": []}

    docs = []
    all_text = ""
    for name, spec in exp["files"].items():
        if spec["duplicate_of"]:
            continue
        path = FIX / key / name
        all_text += " ".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
        try:
            pages = cl.classify_pdf(path)
        except llm.LLMError as e:
            out["errors"].append(f"classify {name}: {e}")
            continue
        for got, want in zip(pages, spec["pages"], strict=True):
            out["pages"].append({
                "file": name, "page": got.page,
                "expected": want["doc_type"], "got": got.doc_type.value,
                "expected_first": want["is_first_page_of_new_doc"], "got_first": got.is_first_page_of_new_doc,
                "confidence": got.confidence,
            })
        docs += cl.segment(pages, path)
    t_classify = time.monotonic() - t0

    n = len(out["pages"]) or 1
    out["page_accuracy"] = round(sum(p["expected"] == p["got"] for p in out["pages"]) / n, 3)
    out["break_accuracy"] = round(sum(p["expected_first"] == p["got_first"] for p in out["pages"]) / n, 3)
    out["page_errors"] = [p for p in out["pages"] if p["expected"] != p["got"] or p["expected_first"] != p["got_first"]]
    out["needs_review_pages"] = sum(p["confidence"] < cl.REVIEW_THRESHOLD for p in out["pages"])

    got_docs = {(Path(d.source_file).name, d.doc_type.value, tuple(d.pages)) for d in docs}
    want_docs = {(d["file"], d["doc_type"], tuple(d["pages"])) for d in exp["logical_docs"]}
    out["logical_docs_exact"] = f"{len(got_docs & want_docs)}/{len(want_docs)}"

    present = {d.doc_type for d in docs}
    out["missing_doc_types"] = {
        "expected": sorted(exp["missing_doc_types"]),
        "got": sorted(dt.value for dt in REQUIRED[scenario] if dt not in present),
    }

    t1 = time.monotonic()
    try:
        facts = ex.extract_facts(docs, scenario)
    except llm.LLMError as e:
        out["errors"].append(f"extract: {e}")
        facts = None
    t_extract = time.monotonic() - t1

    text_n = norm(all_text)
    fields: list[dict[str, Any]] = []
    for k, want in exp["facts"].items():
        if k == "scenario":
            continue
        got = getattr(facts, k).value if facts else None
        reachable = any(norm(f) in text_n for f in surface_forms(k, want))
        fields.append({"field": k, "expected": want, "got": got, "ok": norm(got) == norm(want),
                       "exact": got == want, "reachable": reachable})
    reach = [f for f in fields if f["reachable"]]
    out["facts"] = fields
    out["facts_correct"] = f"{sum(f['ok'] for f in fields)}/{len(fields)}"
    out["facts_correct_reachable"] = f"{sum(f['ok'] for f in reach)}/{len(reach)}"
    out["facts_unreachable"] = [f["field"] for f in fields if not f["reachable"]]

    alts = facts.valores_alternativos if facts else {}
    conflicts = []
    for c in exp["conflicts"]:
        values = {a.value for a in alts.get(c["field"], [])}
        conflicts.append({"field": c["field"], "expected": c["values"], "got": sorted(values),
                          "detected": set(c["values"]) <= values})
    out["conflicts"] = conflicts
    out["conflict_detected"] = all(c["detected"] for c in conflicts)
    out["spurious_conflicts"] = sorted(set(alts) - {c["field"] for c in exp["conflicts"]})
    out["evidencias_culpa"] = [e.fato for e in facts.evidencias_culpa] if facts else []

    out["seconds"] = {"classify": round(t_classify, 1), "extract": round(t_extract, 1),
                      "total": round(time.monotonic() - t0, 1)}
    out["usage"] = llm.usage_summary(llm.USAGE[usage_start:])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=llm.PROVIDERS, default=None,
                    help="padrão: o do ambiente (SUBROGA_LLM_PROVIDER / chaves)")
    ap.add_argument("--scenario", choices=SCENARIOS, action="append")
    ap.add_argument("--out", type=Path, default=ROOT / "storage" / "eval")
    args = ap.parse_args()

    if args.provider:
        os.environ["SUBROGA_LLM_PROVIDER"] = args.provider
    provider = llm.provider_name()
    if not llm.is_ready():
        sys.exit(f"provedor {provider} sem credenciais no ambiente")

    results = {}
    for key in args.scenario or SCENARIOS:
        print(f"== {key} ({provider})", flush=True)
        r = eval_scenario(key)
        results[key] = r
        print(f"   páginas {r['page_accuracy']:.0%} | quebras {r['break_accuracy']:.0%} | docs {r['logical_docs_exact']}"
              f" | campos {r['facts_correct']} (alcançáveis {r['facts_correct_reachable']})"
              f" | conflito {'ok' if r['conflict_detected'] else 'NÃO'} | espúrios {r['spurious_conflicts']}"
              f" | {r['seconds']['total']}s | US$ {r['usage']['cost_usd']}", flush=True)
        for p in r["page_errors"]:
            print(f"   ! {p['file']} p{p['page']}: {p['got']}/{p['got_first']} (esperado {p['expected']}/"
                  f"{p['expected_first']})")
        for f in r["facts"]:
            if not f["ok"]:
                print(f"   ! {f['field']}: {f['got']!r} (esperado {f['expected']!r}"
                      f"{'' if f['reachable'] else ', ausente dos PDFs'})")
        for e in r["errors"]:
            print(f"   ERRO {e}")

    pages = [p for r in results.values() for p in r["pages"]]
    n = len(pages) or 1

    def ratio(key: str) -> str:
        a = sum(int(r[key].split("/")[0]) for r in results.values())
        b = sum(int(r[key].split("/")[1]) for r in results.values())
        return f"{a}/{b}"

    summary = {
        "provider": provider,
        "models": {"classify": llm.model_for(provider, "classify"), "extract": llm.model_for(provider, "extract")},
        "prompts": {name: llm.load_prompt(name).splitlines()[0] for name in ("classify", "extract")},
        "pages": len(pages),
        "page_accuracy": round(sum(p["expected"] == p["got"] for p in pages) / n, 3),
        "break_accuracy": round(sum(p["expected_first"] == p["got_first"] for p in pages) / n, 3),
        "logical_docs_exact": ratio("logical_docs_exact"),
        "facts_correct": ratio("facts_correct"),
        "facts_correct_reachable": ratio("facts_correct_reachable"),
        "conflicts_detected": f"{sum(r['conflict_detected'] for r in results.values())}/{len(results)}",
        "seconds": round(sum(r["seconds"]["total"] for r in results.values()), 1),
        "usage": llm.usage_summary(),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"report_{provider}.json"
    path.write_text(json.dumps({"summary": summary, "scenarios": results}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"relatório: {path}")


if __name__ == "__main__":
    main()
