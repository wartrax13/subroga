"""Taxonomia de documentos e cenários. Contrato compartilhado: não renomear membros."""
from enum import StrEnum


class DocType(StrEnum):
    PROCURACAO = "PROCURACAO"
    DOCS_SEGURADORA = "DOCS_SEGURADORA"
    APOLICE = "APOLICE"
    AVISO_SINISTRO = "AVISO_SINISTRO"
    BOLETIM_OCORRENCIA = "BOLETIM_OCORRENCIA"
    LAUDO_TECNICO = "LAUDO_TECNICO"
    ORCAMENTO_NF_REPARO = "ORCAMENTO_NF_REPARO"
    FOTOS = "FOTOS"
    COMPROVANTE_PAGAMENTO = "COMPROVANTE_PAGAMENTO"
    TERMO_QUITACAO_SUBROGACAO = "TERMO_QUITACAO_SUBROGACAO"
    DOCS_TRANSPORTE = "DOCS_TRANSPORTE"
    DOCS_VEICULO_CONDUTOR = "DOCS_VEICULO_CONDUTOR"
    RECLAMACAO_ADMINISTRATIVA = "RECLAMACAO_ADMINISTRATIVA"
    OUTROS = "OUTROS"


# Ordem dos documentos no dossiê final = ordem de declaração do enum.
DOC_ORDER: list[DocType] = list(DocType)

LABELS: dict[DocType, str] = {
    DocType.PROCURACAO: "Procuração",
    DocType.DOCS_SEGURADORA: "Documentos da seguradora",
    DocType.APOLICE: "Apólice",
    DocType.AVISO_SINISTRO: "Aviso de sinistro",
    DocType.BOLETIM_OCORRENCIA: "Boletim de ocorrência",
    DocType.LAUDO_TECNICO: "Laudo técnico",
    DocType.ORCAMENTO_NF_REPARO: "Orçamentos e notas fiscais",
    DocType.FOTOS: "Registro fotográfico",
    DocType.COMPROVANTE_PAGAMENTO: "Comprovante de pagamento da indenização",
    DocType.TERMO_QUITACAO_SUBROGACAO: "Termo de quitação e sub-rogação",
    DocType.DOCS_TRANSPORTE: "Documentos de transporte",
    DocType.DOCS_VEICULO_CONDUTOR: "Documentos do veículo e do condutor",
    DocType.RECLAMACAO_ADMINISTRATIVA: "Reclamação administrativa",
    DocType.OUTROS: "Outros documentos",
}


class Scenario(StrEnum):
    AUTO = "AUTO"
    ELETRICO = "ELETRICO"
    TRANSPORTE = "TRANSPORTE"
