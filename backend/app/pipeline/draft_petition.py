"""6.2 Minuta da petição inicial da ação regressiva (.docx via docxtpl)."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from docxtpl import DocxTemplate

from app.checklists import REQUIRED
from app.doc_types import LABELS, DocType, Scenario
from app.pipeline import llm
from app.schemas import CaseFacts, FactField, IndexEntry

TEMPLATE = Path(__file__).resolve().parent.parent.parent / "templates" / "peticao_regressiva.docx"

_MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
          "agosto", "setembro", "outubro", "novembro", "dezembro"]


def revisar(campo: str) -> str:
    return f"[[REVISAR: {campo}]]"


def doc_ref(number: int) -> str:
    return f"Doc. {number:02d}"


def fmt_brl(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        n = float(value)
    except ValueError:
        return value
    s = f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def fmt_date(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value).strftime("%d/%m/%Y")
    except ValueError:
        return value


def _label_of(entry: IndexEntry) -> DocType | None:
    for dt, label in LABELS.items():
        if label == entry.label:
            return dt
    return None


class _Ctx:
    """Helpers de formatação ligados ao caso."""

    def __init__(self, facts: CaseFacts, index: list[IndexEntry]):
        self.facts = facts
        self.index = index
        self.by_doc = {e.doc_id: e for e in index}

    def v(self, name: str, fmt: Any = None) -> str:
        ff: FactField = getattr(self.facts, name)
        val = fmt(ff.value) if fmt else ff.value
        if val is None:
            return revisar(name)
        if name in self.facts.valores_alternativos:
            return f"{val} {revisar(f'{name} diverge entre documentos')}"
        return val

    def cite(self, name: str) -> str:
        ff: FactField = getattr(self.facts, name)
        e = self.by_doc.get(ff.source_doc_id or "")
        return f" ({doc_ref(e.number)})" if e else ""

    def cite_type(self, dt: DocType) -> str:
        for e in self.index:
            if _label_of(e) == dt:
                return f" ({doc_ref(e.number)})"
        return f" {revisar(f'falta {LABELS[dt]}')}"


def deterministic_narrative(facts: CaseFacts, index: list[IndexEntry], scenario: Scenario) -> list[str]:
    c = _Ctx(facts, index)
    evento = {
        Scenario.AUTO: "acidente de trânsito",
        Scenario.ELETRICO: "dano elétrico decorrente de falha no fornecimento de energia",
        Scenario.TRANSPORTE: "sinistro com a carga transportada",
    }[scenario]
    paras = [
        f"A Autora celebrou com {c.v('segurado_nome')} contrato de seguro, materializado na apólice "
        f"nº {c.v('apolice_numero')}, com vigência de {c.v('apolice_vigencia_inicio', fmt_date)} a "
        f"{c.v('apolice_vigencia_fim', fmt_date)}{c.cite('apolice_numero') or c.cite_type(DocType.APOLICE)}.",
        f"Em {c.v('sinistro_data', fmt_date)}, em {c.v('sinistro_local')}, ocorreu {evento} "
        f"envolvendo o bem segurado (sinistro nº {c.v('sinistro_numero')}): {c.v('sinistro_descricao')}"
        f"{c.cite('sinistro_descricao') or c.cite('sinistro_data')}.",
    ]
    if facts.evidencias_culpa:
        evid = []
        for ev in facts.evidencias_culpa:
            e = c.by_doc.get(ev.doc_id or "")
            evid.append(ev.fato.rstrip(".") + (f" ({doc_ref(e.number)})" if e else ""))
        paras.append(
            f"O evento decorreu de conduta de {c.v('causador_nome')}, conforme se extrai dos documentos: "
            + "; ".join(evid) + "."
        )
    else:
        paras.append(
            f"O evento foi causado por {c.v('causador_nome')} {revisar('descrever a culpa/responsabilidade do Réu')}."
        )
    paras.append(
        f"Regulado o sinistro, a Autora indenizou o segurado no valor de {c.v('valor_pago', fmt_brl)}, "
        f"em {c.v('data_pagamento', fmt_date)}"
        f"{c.cite('valor_pago') or c.cite_type(DocType.COMPROVANTE_PAGAMENTO)}, sub-rogando-se nos direitos "
        f"do segurado contra o causador do dano, conforme termo de quitação e sub-rogação"
        f"{c.cite_type(DocType.TERMO_QUITACAO_SUBROGACAO)}."
    )
    return paras


DIREITO_BASE = [
    "Ao pagar a indenização, a Autora sub-rogou-se nos direitos e ações que competiam ao segurado contra o "
    "autor do dano, nos limites do valor efetivamente pago, nos termos do art. 786 do Código Civil.",
    "No mesmo sentido, a Súmula 188 do Supremo Tribunal Federal: \"O segurador tem ação regressiva contra o "
    "causador do dano, pelo que efetivamente pagou, até ao limite previsto no contrato de seguro.\"",
    "Aquele que, por ação ou omissão voluntária, negligência ou imprudência, violar direito e causar dano a "
    "outrem comete ato ilícito (art. 186 do Código Civil) e fica obrigado a repará-lo (art. 927 do Código Civil).",
]
DIREITO_CENARIO: dict[Scenario, list[str]] = {
    Scenario.AUTO: [],
    Scenario.ELETRICO: [
        "A Ré, concessionária de serviço público de distribuição de energia elétrica, responde objetivamente "
        "pelos danos que seus agentes causarem a terceiros, nos termos do art. 37, §6º, da Constituição Federal, "
        "independentemente da demonstração de culpa.",
        "A relação entre o segurado e a concessionária é, ademais, de consumo, aplicando-se o Código de Defesa do "
        "Consumidor e a responsabilidade objetiva do fornecedor de serviços, direitos aos quais a Autora se sub-roga.",
    ],
    Scenario.TRANSPORTE: [
        "O transportador obriga-se a conduzir a coisa ao seu destino, tomando todas as cautelas necessárias para "
        "mantê-la em bom estado, e responde por ela desde o recebimento até a entrega (arts. 749 e 750 do Código Civil).",
    ],
}


def _pedidos(c: _Ctx) -> list[str]:
    return [
        "a) a citação do Réu para, querendo, apresentar resposta, sob pena de revelia;",
        f"b) a procedência do pedido, condenando-se o Réu ao pagamento de {c.v('valor_pago', fmt_brl)}, valor "
        "efetivamente desembolsado pela Autora, com correção monetária desde a data do desembolso (Súmula 43 do STJ) "
        "e juros de mora desde a data do evento danoso (Súmula 54 do STJ);",
        "c) a condenação do Réu ao pagamento das custas processuais e dos honorários advocatícios;",
        "d) a produção de todas as provas em direito admitidas, especialmente documental, testemunhal e pericial.",
    ]


def _qualificacao(c: _Ctx) -> tuple[str, str]:
    autora = (
        f"{c.v('seguradora_nome').upper()}, pessoa jurídica de direito privado, inscrita no CNPJ sob o nº "
        f"{c.v('seguradora_cnpj')}, com sede em {revisar('endereço da Autora')}, por seus advogados "
        f"(procuração{c.cite_type(DocType.PROCURACAO)}), vem, respeitosamente, propor a presente"
    )
    reu = (
        f"em face de {c.v('causador_nome').upper()}, inscrito(a) no CPF/CNPJ sob o nº {c.v('causador_cpf_cnpj')}, "
        f"com endereço em {c.v('causador_endereco')}"
    )
    if c.facts.causador_placa.value or c.facts.scenario == Scenario.AUTO:
        reu += f", condutor/proprietário do veículo de placa {c.v('causador_placa')}"
    reu += ", pelos fatos e fundamentos a seguir expostos."
    return autora, reu


def _rol(index: list[IndexEntry], scenario: Scenario) -> list[str]:
    rol = [f"{doc_ref(e.number)} – {e.label}: {e.title} (fls. {e.start_fl}–{e.end_fl})" for e in index]
    present = {_label_of(e) for e in index}
    for dt in REQUIRED[scenario]:
        if dt not in present:
            rol.append(revisar(f"falta {LABELS[dt]}"))
    return rol


def build_context(
    facts: CaseFacts, index: list[IndexEntry], scenario: Scenario, narrative: str | None = None
) -> dict[str, Any]:
    c = _Ctx(facts, index)
    autora, reu = _qualificacao(c)
    if narrative and narrative.strip():
        fatos = [p.strip() for p in narrative.split("\n\n") if p.strip()]
    else:
        fatos = deterministic_narrative(facts, index, scenario)
    valor = fmt_brl(facts.valor_pago.value)
    hoje = date.today()
    return {
        "enderecamento": f"EXCELENTÍSSIMO(A) SENHOR(A) DOUTOR(A) JUIZ(A) DE DIREITO DA ___ VARA CÍVEL DA COMARCA DE "
        f"{revisar('comarca')}",
        "qualificacao_autora": autora,
        "qualificacao_reu": reu,
        "fatos": fatos,
        "direito": [*DIREITO_BASE, *DIREITO_CENARIO[scenario]],
        "pedidos": _pedidos(c),
        "valor_causa": f"{valor or revisar('valor_pago')} {revisar('atualizar')}",
        "rol": _rol(index, scenario),
        "local_data": f"{revisar('local')}, {hoje.day} de {_MESES[hoje.month - 1]} de {hoje.year}.",
        "advogado": f"{revisar('advogado(a) / OAB')}",
    }


def draft_petition(
    facts: CaseFacts,
    index: list[IndexEntry],
    scenario: Scenario,
    out_path: Path,
    narrative: str | None = None,
) -> Path:
    tpl = DocxTemplate(str(TEMPLATE))
    tpl.render(build_context(facts, index, scenario, narrative), autoescape=True)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tpl.save(str(out_path))
    return out_path


def write_narrative(
    facts: CaseFacts, index: list[IndexEntry], scenario: Scenario, client: Any = None
) -> str:
    """Seção 'Dos fatos' redigida pelo modelo, citando (Doc. NN) a partir do índice.

    No provedor `heuristic` (sem `client`), devolve a narrativa determinística.
    """
    if client is None and llm.provider_name() == "heuristic":
        return "\n\n".join(deterministic_narrative(facts, index, scenario))
    payload = {
        "cenario": scenario.value,
        "fatos": facts.model_dump(mode="json"),
        "indice": [
            {"doc": doc_ref(e.number), "doc_id": e.doc_id, "rotulo": e.label, "titulo": e.title} for e in index
        ],
    }
    return llm.text_call(
        "narrative",
        system=llm.load_prompt("narrative"),
        parts=[llm.text_part(json.dumps(payload, ensure_ascii=False, indent=1))],
        client=client,
        max_tokens=8000,
        effort="medium",
    )
