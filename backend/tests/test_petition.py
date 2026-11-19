from pathlib import Path
from types import SimpleNamespace
from typing import Any

from docx import Document

from app.doc_types import LABELS, DocType, Scenario
from app.pipeline import draft_petition as dp
from app.schemas import CaseFacts, Evidence, FactField, IndexEntry


def docx_text(path: Path) -> str:
    return "\n".join(p.text for p in Document(str(path)).paragraphs)


def entry(n: int, doc_id: str, dt: DocType, start: int) -> IndexEntry:
    return IndexEntry(number=n, doc_id=doc_id, label=LABELS[dt], title=f"{dt.value} título", start_fl=start, end_fl=start + 1)


INDEX_FULL = [
    entry(1, "d_proc", DocType.PROCURACAO, 3),
    entry(2, "d_apol", DocType.APOLICE, 5),
    entry(3, "d_aviso", DocType.AVISO_SINISTRO, 7),
    entry(4, "d_bo", DocType.BOLETIM_OCORRENCIA, 9),
    entry(5, "d_orc", DocType.ORCAMENTO_NF_REPARO, 11),
    entry(6, "d_comp", DocType.COMPROVANTE_PAGAMENTO, 13),
    entry(7, "d_termo", DocType.TERMO_QUITACAO_SUBROGACAO, 15),
]


def full_facts() -> CaseFacts:
    ff = lambda v, d=None: FactField(value=v, source_doc_id=d, page=0)
    return CaseFacts(
        scenario=Scenario.AUTO,
        seguradora_nome=ff("Seguradora Exemplo S.A."),
        seguradora_cnpj=ff("00.000.000/0001-00"),
        segurado_nome=ff("Maria Souza & Filhos", "d_apol"),
        apolice_numero=ff("AP-777", "d_apol"),
        apolice_vigencia_inicio=ff("2024-01-01", "d_apol"),
        apolice_vigencia_fim=ff("2024-12-31", "d_apol"),
        sinistro_numero=ff("S-1", "d_aviso"),
        sinistro_data=ff("2024-05-10", "d_bo"),
        sinistro_local=ff("Av. Paulista, São Paulo/SP", "d_bo"),
        sinistro_descricao=ff("colisão traseira", "d_bo"),
        causador_nome=ff("José Réu", "d_bo"),
        causador_cpf_cnpj=ff("111.222.333-44", "d_bo"),
        causador_endereco=ff("Rua A, 1", "d_bo"),
        causador_placa=ff("ABC1D23", "d_bo"),
        valor_pago=ff("48320.00", "d_comp"),
        data_pagamento=ff("2024-06-01", "d_comp"),
        evidencias_culpa=[Evidence(fato="O Réu colidiu na traseira do veículo segurado", doc_id="d_bo")],
    )


def test_docx_full_case(tmp_path: Path) -> None:
    out = dp.draft_petition(full_facts(), INDEX_FULL, Scenario.AUTO, tmp_path / "p.docx")
    text = docx_text(out)
    assert "Maria Souza & Filhos" in text
    assert "R$ 48.320,00" in text
    assert "Doc. 01" in text
    assert "(Doc. 06)" in text  # citação do comprovante na narrativa
    assert "[[REVISAR: comarca]]" in text
    assert "[[REVISAR: atualizar]]" in text
    assert "Súmula 188" in text and "art. 786" in text
    assert "Súmula 43" in text and "Súmula 54" in text
    assert "37, §6º" not in text and "749" not in text  # blocos de outros cenários
    assert "[[REVISAR: falta" not in text
    assert "{{" not in text and "{%" not in text


def test_docx_missing_data_and_docs(tmp_path: Path) -> None:
    facts = CaseFacts(scenario=Scenario.ELETRICO, segurado_nome=FactField(value="Empresa X Ltda"))
    index = [entry(1, "d_apol", DocType.APOLICE, 3)]
    text = docx_text(dp.draft_petition(facts, index, Scenario.ELETRICO, tmp_path / "p.docx"))
    assert "Empresa X Ltda" in text
    assert "[[REVISAR: valor_pago]]" in text
    assert "[[REVISAR: causador_nome]]" in text
    assert f"[[REVISAR: falta {LABELS[DocType.TERMO_QUITACAO_SUBROGACAO]}]]" in text
    assert f"[[REVISAR: falta {LABELS[DocType.LAUDO_TECNICO]}]]" in text
    assert "art. 37, §6º" in text and "Código de Defesa do Consumidor" in text


def test_conflict_marked_and_transport_block(tmp_path: Path) -> None:
    facts = full_facts()
    facts.valores_alternativos = {"valor_pago": [FactField(value="48320.00"), FactField(value="45000.00")]}
    text = docx_text(dp.draft_petition(facts, INDEX_FULL, Scenario.TRANSPORTE, tmp_path / "p.docx"))
    assert "[[REVISAR: valor_pago diverge entre documentos]]" in text
    assert "arts. 749 e 750" in text


def test_narrative_injected(tmp_path: Path) -> None:
    class Fake:
        def __init__(self) -> None:
            self.messages = self

        def create(self, **kw: Any) -> SimpleNamespace:
            return SimpleNamespace(
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text="Primeiro parágrafo (Doc. 02).\n\nSegundo [[REVISAR]].")],
            )

    narrative = dp.write_narrative(full_facts(), INDEX_FULL, Scenario.AUTO, client=Fake())
    text = docx_text(dp.draft_petition(full_facts(), INDEX_FULL, Scenario.AUTO, tmp_path / "p.docx", narrative))
    assert "Primeiro parágrafo (Doc. 02)." in text and "Segundo [[REVISAR]]." in text
