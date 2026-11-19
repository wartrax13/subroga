"""Estruturas trocadas entre as etapas do pipeline. Contrato compartilhado.

Convenções:
- páginas são índices 0-based dentro do arquivo de origem;
- datas são strings ISO "AAAA-MM-DD";
- valores monetários são strings decimais com ponto, ex.: "48320.00".
"""
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.doc_types import DocType, Scenario


class PageClass(BaseModel):
    """Saída da classificação de UMA página."""
    page: int
    doc_type: DocType
    is_first_page_of_new_doc: bool
    confidence: float = Field(ge=0, le=1)
    short_title: str = ""


class LogicalDoc(BaseModel):
    """Documento lógico = sequência de páginas de um arquivo enviado."""
    id: str
    doc_type: DocType
    title: str
    source_file: Path
    pages: list[int]
    confidence: float = Field(ge=0, le=1)
    needs_review: bool = False


class FactField(BaseModel):
    value: str | None = None
    source_doc_id: str | None = None
    page: int | None = None


class Evidence(BaseModel):
    fato: str
    doc_id: str | None = None


class CaseFacts(BaseModel):
    scenario: Scenario | None = None
    seguradora_nome: FactField = FactField()
    seguradora_cnpj: FactField = FactField()
    segurado_nome: FactField = FactField()
    segurado_cpf_cnpj: FactField = FactField()
    apolice_numero: FactField = FactField()
    apolice_vigencia_inicio: FactField = FactField()
    apolice_vigencia_fim: FactField = FactField()
    apolice_ramo: FactField = FactField()
    sinistro_numero: FactField = FactField()
    sinistro_data: FactField = FactField()
    sinistro_local: FactField = FactField()
    sinistro_descricao: FactField = FactField()
    causador_nome: FactField = FactField()
    causador_cpf_cnpj: FactField = FactField()
    causador_endereco: FactField = FactField()
    causador_placa: FactField = FactField()
    valor_pago: FactField = FactField()
    data_pagamento: FactField = FactField()
    franquia: FactField = FactField()
    evidencias_culpa: list[Evidence] = []
    # valores do mesmo campo encontrados em documentos diferentes (para detectar conflito)
    valores_alternativos: dict[str, list[FactField]] = {}


class Alert(BaseModel):
    kind: Literal["MISSING_DOC", "PRESCRIPTION", "CONFLICT", "LOW_CONFIDENCE"]
    severity: Literal["info", "warn", "critical"]
    message: str
    field: str | None = None  # campo de CaseFacts relacionado (ex.: conflito em "valor_pago")


class IndexEntry(BaseModel):
    number: int            # 1 -> "Doc. 01"
    doc_id: str
    label: str             # LABELS[doc_type]
    title: str
    start_fl: int          # folha da separadora
    end_fl: int            # última folha do documento


class DossieResult(BaseModel):
    path: Path
    index: list[IndexEntry]
    total_pages: int
