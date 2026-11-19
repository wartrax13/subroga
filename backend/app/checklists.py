"""Checklist de documentos por cenário (seção 3 do plano). Editável."""
from app.doc_types import DocType, Scenario

COMMON: list[DocType] = [
    DocType.PROCURACAO,
    DocType.APOLICE,
    DocType.AVISO_SINISTRO,
    DocType.COMPROVANTE_PAGAMENTO,
    DocType.TERMO_QUITACAO_SUBROGACAO,
]

REQUIRED: dict[Scenario, list[DocType]] = {
    Scenario.AUTO: COMMON + [DocType.BOLETIM_OCORRENCIA, DocType.ORCAMENTO_NF_REPARO],
    Scenario.ELETRICO: COMMON + [DocType.LAUDO_TECNICO, DocType.ORCAMENTO_NF_REPARO],
    Scenario.TRANSPORTE: COMMON + [DocType.DOCS_TRANSPORTE],
}

RECOMMENDED: dict[Scenario, list[DocType]] = {
    Scenario.AUTO: [DocType.DOCS_VEICULO_CONDUTOR, DocType.FOTOS],
    Scenario.ELETRICO: [DocType.RECLAMACAO_ADMINISTRATIVA],
    Scenario.TRANSPORTE: [],
}

# Sem este documento não há prova da sub-rogação: falta vira alerta crítico.
ESSENTIAL: set[DocType] = {DocType.TERMO_QUITACAO_SUBROGACAO, DocType.COMPROVANTE_PAGAMENTO}
