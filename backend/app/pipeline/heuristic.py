"""Provedor `heuristic`: classificação por palavras-chave e extração por regex.

Sem rede e sem custo. Usa o texto embutido no PDF (pypdf); PDFs só de imagem
(scans sem OCR) caem em OUTROS com confiança baixa e vão para revisão humana.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from app.doc_types import DocType, Scenario
from app.schemas import CaseFacts, Evidence, FactField, LogicalDoc, PageClass

HEADER_CONF = 0.85     # cabeçalho reconhecido no topo da página
BODY_CONF = 0.55       # palavras-chave só no corpo
CONTINUATION_CONF = 0.6
NO_MATCH_CONF = 0.3
HEAD_CHARS = 160       # tamanho do "cabeçalho" (primeiras linhas) analisado


def fold(s: str) -> str:
    """Maiúsculas sem acento, para casar palavras-chave."""
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).upper()


# Padrões sobre texto `fold`ado. No cabeçalho vence o padrão que aparece primeiro.
PATTERNS: list[tuple[DocType, str]] = [
    (DocType.PROCURACAO, r"\bPROCURACAO\b|\bSUBSTABELECIMENTO\b"),
    (DocType.DOCS_SEGURADORA, r"ESTATUTO SOCIAL|CONTRATO SOCIAL|\bATA DA\b|\bATA DE\b|ELEICAO DA DIRETORIA"),
    (DocType.TERMO_QUITACAO_SUBROGACAO, r"QUITACAO E SUB-?ROGACAO|TERMO DE SUB-?ROGACAO|RECIBO DE INDENIZACAO|"
                                        r"TERMO DE QUITACAO"),
    (DocType.AVISO_SINISTRO, r"AVISO DE SINISTRO|COMUNICACAO DE SINISTRO|ABERTURA DE SINISTRO|"
                             r"COMUNICADO DE SINISTRO"),
    (DocType.BOLETIM_OCORRENCIA, r"BOLETIM DE OCORRENCIA|BOLETIM DE ACIDENTE|REGISTRO POLICIAL|"
                                 r"POLICIA (CIVIL|MILITAR|RODOVIARIA)|\bB\.O\.|\bBOAT\b"),
    (DocType.FOTOS, r"REGISTRO FOTOGRAFICO|RELATORIO FOTOGRAFICO|\bFOTOS\b"),
    (DocType.LAUDO_TECNICO, r"\bLAUDO\b|RELATORIO DE REGULACAO|REGULACAO DE SINISTRO|RELATORIO DE VISTORIA|"
                            r"\bPERICIA\b"),
    (DocType.COMPROVANTE_PAGAMENTO, r"COMPROVANTE DE (TRANSFERENCIA|PAGAMENTO|PIX|TED)|\bTED\b|\bPIX\b|"
                                    r"COMPROVANTE BANCARIO"),
    (DocType.DOCS_TRANSPORTE, r"\bDACTE\b|\bCT-?E\b|CONHECIMENTO DE TRANSPORTE|\bDANFE\b|MANIFESTO|"
                              r"TERMO DE AVARIA|RESSALVA"),
    (DocType.DOCS_VEICULO_CONDUTOR, r"\bCRLV|LICENCIAMENTO DE VEICULO|CARTEIRA NACIONAL DE HABILITACAO|\bCNH\b|"
                                    r"\bCROQUI\b"),
    (DocType.RECLAMACAO_ADMINISTRATIVA, r"NOTIFICACAO EXTRAJUDICIAL|PEDIDO DE RESSARCIMENTO|"
                                        r"RESPOSTA DA CONCESSIONARIA|RECLAMACAO|\bANEEL\b|OUVIDORIA"),
    (DocType.ORCAMENTO_NF_REPARO, r"\bORCAMENTO\b|NOTA FISCAL|\bNF-?E\b|\bNFS-?E\b|ORDEM DE SERVICO"),
    (DocType.APOLICE, r"\bAPOLICE\b|CONDICOES GERAIS|CONDICOES ESPECIAIS|CERTIFICADO DE SEGURO|\bENDOSSO\b"),
    (DocType.OUTROS, r"\bE-?MAIL\b|ENCAMINHAMENTO DE DOCUMENTOS"),
]
_COMPILED = [(dt, re.compile(p)) for dt, p in PATTERNS]
_CONTINUATION = re.compile(r"CONTINUACAO|\(CONT\.?\)")


def page_texts(path: Path) -> list[str]:
    reader = PdfReader(str(path))
    out: list[str] = []
    for p in reader.pages:
        try:
            out.append(p.extract_text() or "")
        except Exception:  # noqa: BLE001 - página corrompida vira texto vazio
            out.append("")
    return out


def _head(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return fold(" ".join(lines[:3]))[:HEAD_CHARS]


def classify_text(text: str) -> tuple[DocType | None, float]:
    """(tipo, confiança) de uma página; None quando nada casa."""
    head = _head(text)
    best: tuple[int, int, DocType] | None = None
    for dt, rx in _COMPILED:
        m = rx.search(head)
        if m and (best is None or (m.start(), -len(m.group())) < best[:2]):
            best = (m.start(), -len(m.group()), dt)
    if best:
        return best[2], (0.6 if best[2] == DocType.OUTROS else HEADER_CONF)
    body = fold(text)
    counts = Counter({dt: len(rx.findall(body)) for dt, rx in _COMPILED if dt != DocType.OUTROS})
    if counts and counts.most_common(1)[0][1] > 0:
        return counts.most_common(1)[0][0], BODY_CONF
    return None, 0.0


def _title(text: str) -> str:
    for ln in text.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("DOCUMENTO SINTÉTICO"):
            return ln[:60]
    return ""


def classify_pages(texts: list[str]) -> list[PageClass]:
    pages: list[PageClass] = []
    prev: DocType | None = None
    for i, text in enumerate(texts):
        dt, conf = classify_text(text)
        continuation = bool(_CONTINUATION.search(_head(text)))
        if dt is None:
            if continuation and prev is not None:
                dt, conf = prev, CONTINUATION_CONF
            else:
                dt, conf = DocType.OUTROS, NO_MATCH_CONF
        # documento novo: 1ª página, mudança de tipo, ou página sem relação reconhecida
        first = i == 0 or dt != prev or (dt == DocType.OUTROS and conf <= NO_MATCH_CONF)
        if continuation and dt == prev:
            first = False
        pages.append(PageClass(page=i, doc_type=dt, is_first_page_of_new_doc=first,
                               confidence=conf, short_title=_title(text) if first else ""))
        prev = dt
    return pages


def classify_pdf(path: Path) -> list[PageClass]:
    return classify_pages(page_texts(Path(path)))


# ======================================================================= extração

_DOC_NUM = r"\d{3}\.\d{3}\.\d{3}-\d{2}|\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}"
_DATE = r"\d{1,2}/\d{1,2}/\d{2,4}"
_MONEY = r"R\$\s*([\d.]+,\d{2})"
_PLATE = r"\b[A-Z]{3}-?\d[A-Z0-9]\d{2}\b"
_NAME_STOP = re.compile(r"\s+[–—-]\s+(CPF|CNPJ|placa|Código|C[oó]digo)\b|,\s*(CPF|CNPJ|inscrit)|\s+[–—-]\s*$", re.I)

# Campos em que valores diferentes entre documentos viram `valores_alternativos`.
CONFLICT_FIELDS = {
    "valor_pago", "data_pagamento", "sinistro_data", "apolice_numero", "sinistro_numero",
    "segurado_cpf_cnpj", "seguradora_cnpj", "causador_cpf_cnpj", "causador_placa", "franquia",
}


@dataclass
class _Page:
    doc: LogicalDoc
    page: int
    text: str       # com quebras de linha
    flat: str       # quebras viram espaço


@dataclass
class _Cand:
    value: str
    doc_id: str
    page: int
    prio: int       # menor = mais confiável


def _clean_name(s: str) -> str:
    s = s.strip()
    m = _NAME_STOP.search(s)
    if m:
        s = s[: m.start()]
    s = s.strip(" .,;:–—-")
    return s + ("." if re.search(r"\b(Ltda|S\.A|Cia)$", s) else "")


def _norm_name(s: str) -> str:
    s = s.strip(" ,;:–—-")
    s = re.sub(r"\s+", " ", s)
    if s.endswith(("Ltda", "S.A", "Cia")):
        s += "."
    return s


def _doc_pages(docs: list[LogicalDoc], cache: dict[Path, list[str]]) -> list[_Page]:
    out: list[_Page] = []
    for d in docs:
        texts = cache.setdefault(Path(d.source_file), page_texts(Path(d.source_file)))
        for p in d.pages:
            if 0 <= p < len(texts):
                t = texts[p]
                out.append(_Page(d, p, t, re.sub(r"\s*\n\s*", " ", t)))
    return out


class _Collector:
    def __init__(self, pages: list[_Page]):
        self.pages = pages
        self.cands: dict[str, list[_Cand]] = {}

    def add(self, field: str, value: str | None, pg: _Page, prio: int) -> None:
        if value and value.strip():
            self.cands.setdefault(field, []).append(_Cand(value.strip(), pg.doc.id, pg.page, prio))

    def scan(self, field: str, pattern: str, types: dict[DocType, int] | None = None, *, flat: bool = False,
             group: int = 1, flags: int = re.I, post=None) -> None:
        rx = re.compile(pattern, flags)
        for pg in self.pages:
            if types is not None and pg.doc.doc_type not in types:
                continue
            prio = types[pg.doc.doc_type] if types else 5
            for m in rx.finditer(pg.flat if flat else pg.text):
                v = m.group(group)
                self.add(field, post(v) if post else v, pg, prio)


def _money(v: str) -> str | None:
    from app.pipeline.extract import normalize_money

    return normalize_money(v)


def _date(v: str) -> str | None:
    from app.pipeline.extract import normalize_date

    return normalize_date(v)


def _key(field: str, v: str) -> str:
    if field in {"causador_placa"}:
        return v.replace("-", "").upper()
    return fold(re.sub(r"\s+", " ", v)).strip(" .")


def extract_facts(docs: list[LogicalDoc], scenario: Scenario | None) -> CaseFacts:
    pages = _doc_pages(docs, {})
    c = _Collector(pages)
    T = DocType

    # --- seguradora
    c.scan("seguradora_nome", r"Seguradora:\s*([^\n]+)", {T.APOLICE: 0}, post=_clean_name)
    c.scan("seguradora_nome", r"Remetente:\s*([^\n]+)", {T.COMPROVANTE_PAGAMENTO: 1}, post=_clean_name)
    c.scan("seguradora_nome", r"recebido d[ao]\s+(.+?)\s+a import[âa]ncia", {T.TERMO_QUITACAO_SUBROGACAO: 2},
           flat=True, post=_norm_name)
    c.scan("seguradora_cnpj", r"(?:Seguradora|Remetente):[^\n]*?CNPJ[^\d]{0,10}(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})",
           {T.APOLICE: 0, T.COMPROVANTE_PAGAMENTO: 1})

    # --- segurado
    c.scan("segurado_nome", r"Segurad[oa]:\s*([^\n]+)", {T.APOLICE: 0, T.AVISO_SINISTRO: 1}, post=_clean_name)
    c.scan("segurado_nome", r"Favorecido:\s*([^\n]+)", {T.COMPROVANTE_PAGAMENTO: 2}, post=_clean_name)
    c.scan("segurado_nome", r"instrumento,\s*(.+?),\s*inscrit", {T.TERMO_QUITACAO_SUBROGACAO: 3}, flat=True,
           post=_norm_name)
    c.scan("segurado_cpf_cnpj", rf"(?:Segurad[oa]|Favorecido):[^\n]*?(?:CPF|CNPJ)[^\d\n]{{0,12}}({_DOC_NUM})",
           {T.APOLICE: 0, T.AVISO_SINISTRO: 1, T.COMPROVANTE_PAGAMENTO: 2})
    c.scan("segurado_cpf_cnpj", rf"inscrit[oa(). ]*sob o n[ºo°]\s*({_DOC_NUM})", {T.TERMO_QUITACAO_SUBROGACAO: 3},
           flat=True)

    # --- apólice
    c.scan("apolice_numero", r"AP[ÓO]LICE\b[^\n]{0,80}?N[º°o]\.?\s*(\d[\d./-]{4,}\d)", {T.APOLICE: 0}, flat=True)
    c.scan("apolice_numero", r"Ap[óo]lice(?:\s*n[º°o]\.?|:)?\s*(\d[\d./-]{4,}\d)",
           {T.APOLICE: 0, T.AVISO_SINISTRO: 1, T.TERMO_QUITACAO_SUBROGACAO: 2, T.COMPROVANTE_PAGAMENTO: 2},
           flat=True, flags=0)
    for pg in pages:
        if pg.doc.doc_type != T.APOLICE:
            continue
        m = re.search(rf"Vig[êe]ncia:?[^\n]*?({_DATE})[^\n]*?({_DATE})", pg.text, re.I)
        if m:
            c.add("apolice_vigencia_inicio", _date(m[1]), pg, 0)
            c.add("apolice_vigencia_fim", _date(m[2]), pg, 0)
    c.scan("apolice_ramo", r"Ramo:\s*(?:\d+\s*[–—-]\s*)?([^\n]+?)\s*$", {T.APOLICE: 0}, flags=re.I | re.M)

    # --- sinistro
    c.scan("sinistro_numero", r"Sinistro(?:\s*n[º°o]\.?|:)?\s*:?\s*([A-Z]{2,5}-\d{4}-\d{3,})",
           {T.AVISO_SINISTRO: 0, T.COMPROVANTE_PAGAMENTO: 1, T.TERMO_QUITACAO_SUBROGACAO: 1, T.LAUDO_TECNICO: 2,
            T.ORCAMENTO_NF_REPARO: 3, T.RECLAMACAO_ADMINISTRATIVA: 3})
    c.scan("sinistro_numero", r"N[º°o] do sinistro:\s*([A-Z0-9][\w./-]{4,})", {T.AVISO_SINISTRO: 0})
    c.scan("sinistro_data", rf"Data (?:do evento|da ocorr[êe]ncia|do sinistro|do acidente):?\s*({_DATE})",
           {T.AVISO_SINISTRO: 0, T.BOLETIM_OCORRENCIA: 1, T.RECLAMACAO_ADMINISTRATIVA: 2}, post=_date)
    c.scan("sinistro_data", rf"Data(?:/hora)? do fato:?\s*({_DATE})", {T.BOLETIM_OCORRENCIA: 1}, post=_date)
    c.scan("sinistro_data", rf"^\s*Data:\s*({_DATE})", {T.BOLETIM_OCORRENCIA: 1}, post=_date,
           flags=re.I | re.M)
    c.scan("sinistro_local", r"Local:\s*(.+?)(?=\s+(?:Tipo|Natureza|Descri|PARTE|Hor[áa]rio|Data)\b|\n|$)",
           {T.BOLETIM_OCORRENCIA: 0, T.AVISO_SINISTRO: 2}, flags=re.I)
    c.scan("sinistro_local", r"Local de risco:\s*([^\n]+)", {T.APOLICE: 1})
    c.scan("sinistro_descricao", r"(?:Descri[çc][ãa]o do segurado|Relato|Descri[çc][ãa]o):\s*(.+?\.)(?:\s|$)",
           {T.AVISO_SINISTRO: 0}, flat=True)
    c.scan("sinistro_descricao", r"HIST[ÓO]RICO:\s*(.+?\.)(?:\s|$)", {T.BOLETIM_OCORRENCIA: 1}, flat=True)
    c.scan("sinistro_descricao", r"(?:Tipo|Natureza):\s*(.+?\.)(?:\s|$)", {T.BOLETIM_OCORRENCIA: 2}, flat=True)
    c.scan("sinistro_descricao", r"Constatado:\s*(.+?\.)(?:\s|$)", {T.DOCS_TRANSPORTE: 3}, flat=True)

    # --- causador
    c.scan("causador_nome", r"Terceiro envolvido:\s*([^\n]+)", {T.AVISO_SINISTRO: 1}, post=_clean_name)
    c.scan("causador_nome", r"PARTE 2[^:\n]*:\s*([^,\n]+)", {T.BOLETIM_OCORRENCIA: 0})
    c.scan("causador_nome", r"Concession[áa]ria respons[áa]vel:\s*([^\n]+)", {T.AVISO_SINISTRO: 0},
           post=lambda v: v.strip(" .;"))
    c.scan("causador_nome", r"(?:Emitente/)?Transportador(?:a)?:\s*([^\n]+)", {T.DOCS_TRANSPORTE: 0},
           post=_clean_name)
    c.scan("causador_nome", r"Notificado:\s*([^\n]+)", {T.RECLAMACAO_ADMINISTRATIVA: 2}, post=_clean_name)
    c.scan("causador_nome", r"em especial\s+contra\s+(.+?),\s*at[ée] o limite", {T.TERMO_QUITACAO_SUBROGACAO: 3},
           flat=True, post=_norm_name)

    c.scan("causador_endereco", r"residente (?:na|no|em)\s+(.+?),\s*(?:condutor|portador|propriet)",
           {T.BOLETIM_OCORRENCIA: 0}, flat=True)
    c.scan("causador_endereco", rf"Notificado:[^\n]*?(?:{_DOC_NUM})\s*[–—-]\s*(.+?)(?:\s+A\s|\s+Ao\s|$)",
           {T.RECLAMACAO_ADMINISTRATIVA: 1}, flat=True)

    facts = CaseFacts(scenario=scenario)
    picked = _pick(c)

    # causador_cpf_cnpj / placa dependem do nome escolhido
    name = picked.get("causador_nome")
    if name:
        first = re.escape(fold(name.value.split(" – ")[0].split(" (")[0])[:30])
        rx = re.compile(rf"{first}[^\n]{{0,40}}?(?:CPF|CNPJ)[^\d\n]{{0,12}}({_DOC_NUM})")
        prio_types = {T.BOLETIM_OCORRENCIA: 0, T.AVISO_SINISTRO: 1, T.DOCS_TRANSPORTE: 1,
                      T.RECLAMACAO_ADMINISTRATIVA: 2}
        for pg in pages:
            for m in rx.finditer(fold(pg.flat)):
                c.add("causador_cpf_cnpj", m[1], pg, prio_types.get(pg.doc.doc_type, 4))
    c.scan("causador_placa", rf"(?:placa|Ve[íi]culo placa)\s*({_PLATE})", {
        T.BOLETIM_OCORRENCIA: 0, T.AVISO_SINISTRO: 1, T.DOCS_TRANSPORTE: 2, T.LAUDO_TECNICO: 3,
        T.RECLAMACAO_ADMINISTRATIVA: 3}, flags=0)
    # placas do veículo segurado (apólice/CRLV) não são do causador
    own = {_key("causador_placa", m) for pg in pages if pg.doc.doc_type in {T.APOLICE, T.DOCS_VEICULO_CONDUTOR}
           for m in re.findall(_PLATE, pg.text)}
    for pg in pages:
        if pg.doc.doc_type == T.BOLETIM_OCORRENCIA:
            for m in re.finditer(rf"\((?:v[íi]tima|segurado)\)[^\n]*?placa\s*({_PLATE})", pg.flat, re.I):
                own.add(_key("causador_placa", m[1]))
    c.cands["causador_placa"] = [x for x in c.cands.get("causador_placa", [])
                                 if _key("causador_placa", x.value) not in own]

    # --- valores
    c.scan("valor_pago", rf"Valor(?: pago| da transfer[êe]ncia)?:\s*{_MONEY}", {T.COMPROVANTE_PAGAMENTO: 0},
           post=_money)
    c.scan("valor_pago", rf"import[âa]ncia de\s*{_MONEY}", {T.TERMO_QUITACAO_SUBROGACAO: 1}, flat=True, post=_money)
    c.scan("valor_pago", rf"Indeniza[çc][ãa]o(?: l[íi]quida)?(?: recomendada| paga)?:\s*{_MONEY}",
           {T.LAUDO_TECNICO: 2}, flat=True, post=_money)
    c.scan("valor_pago", rf"TOTAL DO OR[ÇC]AMENTO:?\s*{_MONEY}", {T.ORCAMENTO_NF_REPARO: 3}, flat=True,
           post=_money)
    c.scan("data_pagamento", rf"Data (?:da transfer[êe]ncia|do pagamento|da opera[çc][ãa]o):?\s*({_DATE})",
           {T.COMPROVANTE_PAGAMENTO: 0}, post=_date)
    c.scan("franquia", rf"Franquia[^:\n]*:\s*{_MONEY}", {T.APOLICE: 0, T.LAUDO_TECNICO: 1}, post=_money)

    picked = _pick(c)
    for name_, cand in picked.items():
        setattr(facts, name_, FactField(value=cand.value, source_doc_id=cand.doc_id, page=cand.page))
    facts.valores_alternativos = _alternatives(c)
    facts.evidencias_culpa = _evidences(pages)
    return facts


def _pick(c: _Collector) -> dict[str, _Cand]:
    out: dict[str, _Cand] = {}
    for field, cands in c.cands.items():
        cands = [x for x in cands if x.value]
        if not cands:
            continue
        # mais confiável (prio) e, no empate, o valor mais frequente
        freq = Counter(_key(field, x.value) for x in cands)
        out[field] = min(cands, key=lambda x: (x.prio, -freq[_key(field, x.value)]))
    return out


def _alternatives(c: _Collector) -> dict[str, list[FactField]]:
    alts: dict[str, list[FactField]] = {}
    for field in CONFLICT_FIELDS:
        cands = sorted(c.cands.get(field, []), key=lambda x: x.prio)
        if len({_key(field, x.value) for x in cands}) < 2:
            continue
        seen: set[tuple[str, str]] = set()
        items: list[FactField] = []
        for x in cands:
            k = (_key(field, x.value), x.doc_id)
            if k in seen:
                continue
            seen.add(k)
            items.append(FactField(value=x.value, source_doc_id=x.doc_id, page=x.page))
        alts[field] = items
    return alts


_EVIDENCE_RX = re.compile(
    r"\b(culpa|admitiu|velocidade incompat[ií]vel|causa prov[áa]vel|avan[çc]ou|distra[ií]|embriag|fadiga|"
    r"responsabilidade|colidiu na traseira|atingido na traseira|perturba[çc][ãa]o|tombamento)",
    re.I,
)
_EVIDENCE_TYPES = {DocType.BOLETIM_OCORRENCIA, DocType.LAUDO_TECNICO, DocType.AVISO_SINISTRO,
                   DocType.RECLAMACAO_ADMINISTRATIVA, DocType.DOCS_TRANSPORTE}
_LABEL_LINE = re.compile(r"^[A-ZÀ-Ú][\wÀ-ú/ ()]{1,40}:\s")
_ABBREV = re.compile(r"\b(Av|Dr|Dra|Sr|Sra|arts?|n[º°o]|km|Ltda|S\.A|Cia|Rod|fls?)\.$", re.I)


def _is_header(line: str) -> bool:
    letters = [ch for ch in line if ch.isalpha()]
    return bool(letters) and sum(ch.isupper() for ch in letters) / len(letters) > 0.8


def _sentences(text: str) -> list[str]:
    """Frases do corpo: ignora títulos em caixa alta e começa segmento novo em linha 'Rótulo:'."""
    segments: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or _is_header(line) or line.startswith("DOCUMENTO SINTÉTICO"):
            continue
        if not segments or _LABEL_LINE.match(line):
            segments.append(line)
        else:
            segments[-1] += " " + line
    out: list[str] = []
    for seg in segments:
        buf = ""
        for tok in re.split(r"(?<=\.)\s+", seg):
            buf = f"{buf} {tok}".strip()
            if buf.endswith(".") and not _ABBREV.search(buf):
                out.append(buf)
                buf = ""
        if buf:
            out.append(buf)
    return out


def _evidences(pages: list[_Page]) -> list[Evidence]:
    out: list[Evidence] = []
    seen: set[str] = set()
    for pg in pages:
        if pg.doc.doc_type not in _EVIDENCE_TYPES:
            continue
        for sent in _sentences(pg.text):
            if not _EVIDENCE_RX.search(sent):
                continue
            fato = re.sub(r"^(HIST[ÓO]RICO|Constata[çc][õo]es|Constatado|Conclus[ãa]o|Relato|"
                          r"Descri[çc][ãa]o do segurado):\s*", "", sent, flags=re.I).strip()
            k = fold(fato)
            if len(fato) < 15 or k in seen:
                continue
            seen.add(k)
            out.append(Evidence(fato=fato, doc_id=pg.doc.id))
    return out[:6]
