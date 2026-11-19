"""Gera PDFs SINTÉTICOS (dados fictícios) em tests/fixtures/<cenario>/ + expected.json.

Uso: uv run python scripts/make_fixtures.py
Idempotente: apaga e recria os diretórios; PDFs gerados com invariant=1 (bytes estáveis).

Cada cenário tem: um PDF "misturado" (3 documentos), um documento obrigatório faltando,
um conflito de valor e um arquivo duplicado.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"

AVISO = "DOCUMENTO SINTÉTICO PARA TESTES – DADOS FICTÍCIOS"


@dataclass
class Page:
    lines: list[str]
    size: tuple[float, float] = A4
    rotate: int = 0
    photos: int = 0  # nº de "fotos" (retângulos) desenhadas


@dataclass
class Doc:
    doc_type: str
    title: str
    pages: list[Page]


@dataclass
class FileSpec:
    name: str
    docs: list[Doc]
    duplicate_of: str | None = None


@dataclass
class Scenario:
    key: str
    enum: str
    files: list[FileSpec]
    facts: dict[str, str]
    missing: list[str]
    conflicts: list[dict[str, object]] = field(default_factory=list)


# ------------------------------------------------------------------ render

def _wrap(c: canvas.Canvas, text: str, font: str, size: float, max_w: float) -> list[str]:
    out: list[str] = []
    cur = ""
    for w in text.split():
        cand = f"{cur} {w}".strip()
        if c.stringWidth(cand, font, size) <= max_w or not cur:
            cur = cand
        else:
            out.append(cur)
            cur = w
    out.append(cur)
    return out


def _draw_page(c: canvas.Canvas, page: Page) -> None:
    w, h = page.size
    c.setPageSize(page.size)
    c.setPageRotation(page.rotate)
    if page.rotate % 180:  # reportlab grava o mediabox trocado: desenha no sistema "deitado" (scan de lado)
        w, h = h, w
    y = h - 60
    header, *body = page.lines
    c.setFont("Helvetica-Bold", 14)
    for ln in _wrap(c, header, "Helvetica-Bold", 14, w - 100):
        c.drawCentredString(w / 2, y, ln)
        y -= 18
    y -= 10
    for text in body:
        if text == "":
            y -= 8
            continue
        bold = text.startswith("**")
        font = "Helvetica-Bold" if bold else "Helvetica"
        text = text.strip("*")
        c.setFont(font, 10)
        for ln in _wrap(c, text, font, 10, w - 120):
            c.drawString(60, y, ln)
            y -= 14
    if page.photos:
        pw = (w - 120 - 20 * (page.photos - 1)) / page.photos
        for i in range(page.photos):
            x = 60 + i * (pw + 20)
            c.setFillColorRGB(0.82, 0.84, 0.87)
            c.rect(x, 90, pw, min(220, y - 120), stroke=1, fill=1)
            c.setFillColorRGB(0, 0, 0)
            c.setFont("Helvetica", 8)
            c.drawString(x + 4, 78, f"Foto {i + 1}")
    c.setFont("Helvetica-Oblique", 7)
    c.drawCentredString(w / 2, 30, AVISO)
    c.showPage()


def write_pdf(path: Path, docs: list[Doc]) -> None:
    c = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    c.setTitle(path.stem)
    for d in docs:
        for p in d.pages:
            _draw_page(c, p)
    c.save()


# ------------------------------------------------------------------ documentos comuns

SEG = {"nome": "Aurora Companhia de Seguros S.A.", "cnpj": "11.222.333/0001-81",
       "end": "Av. Paulista, 1000, 12º andar, Bela Vista, São Paulo/SP, CEP 01310-100"}


def procuracao() -> Doc:
    return Doc("PROCURACAO", "Procuração ad judicia", [Page([
        "PROCURAÇÃO AD JUDICIA ET EXTRA",
        f"OUTORGANTE: {SEG['nome'].upper()}, pessoa jurídica de direito privado, inscrita no CNPJ sob o nº "
        f"{SEG['cnpj']}, com sede na {SEG['end']}, neste ato representada por seus diretores.",
        "",
        "OUTORGADOS: Brandão & Lima Sociedade de Advogados, OAB/SP nº 99.999, e seus advogados associados "
        "Dra. Helena Brandão (OAB/SP 123.456) e Dr. Otávio Lima (OAB/SP 234.567).",
        "",
        "PODERES: os da cláusula ad judicia et extra, para o foro em geral, em qualquer juízo, instância ou "
        "tribunal, podendo propor ações regressivas de ressarcimento, transigir, desistir, receber e dar "
        "quitação, firmar compromissos e substabelecer, com ou sem reserva de poderes.",
        "",
        "São Paulo, 02 de janeiro de 2024.",
        "",
        "_______________________________________",
        f"{SEG['nome']}",
    ])])


def estatuto() -> Doc:
    return Doc("DOCS_SEGURADORA", "Estatuto social e ata de eleição da diretoria", [
        Page(["ESTATUTO SOCIAL – AURORA COMPANHIA DE SEGUROS S.A.",
              "**CAPÍTULO I – DENOMINAÇÃO, SEDE, OBJETO E DURAÇÃO",
              "Art. 1º A Aurora Companhia de Seguros S.A. é uma sociedade anônima de capital fechado, regida "
              "por este Estatuto e pelas disposições legais aplicáveis, autorizada a operar pela SUSEP.",
              "Art. 2º A Companhia tem sede e foro na cidade de São Paulo, Estado de São Paulo.",
              "Art. 3º A Companhia tem por objeto a exploração de seguros de danos e de pessoas em todo o "
              "território nacional.",
              "**CAPÍTULO IV – DA ADMINISTRAÇÃO",
              "Art. 18. A Companhia será representada, ativa e passivamente, por dois diretores em conjunto, "
              "ou por um diretor em conjunto com um procurador constituído na forma deste artigo."]),
        Page(["ATA DA ASSEMBLEIA GERAL ORDINÁRIA",
              "Aos 28 dias do mês de abril de 2023, às 10h00, na sede social, reuniram-se os acionistas "
              "representando a totalidade do capital social da Aurora Companhia de Seguros S.A.",
              "Deliberações: eleição da Diretoria para mandato de 3 (três) anos: Diretor-Presidente Sr. "
              "Marcos Vinícius Teixeira; Diretora Jurídica Sra. Patrícia Gonçalves Moura.",
              "Nada mais havendo a tratar, foi lavrada a presente ata, lida, aprovada e assinada.",
              "JUCESP – Registro nº 999.888/23-1 em 15/05/2023."]),
    ])


def ted(valor: str, data_br: str, favorecido: str, doc_fav: str, sinistro: str) -> Doc:
    return Doc("COMPROVANTE_PAGAMENTO", f"Comprovante de TED – R$ {valor}", [Page([
        "COMPROVANTE DE TRANSFERÊNCIA – TED",
        "Banco Fictício S.A. – Comprovante emitido via Internet Banking Empresas",
        "",
        f"**Data da transferência: {data_br}",
        f"**Valor: R$ {valor}",
        "",
        f"Remetente: {SEG['nome']} – CNPJ {SEG['cnpj']}",
        "Agência: 0001  Conta corrente: 12345-6",
        "",
        f"Favorecido: {favorecido} – CPF/CNPJ {doc_fav}",
        "Banco: 999 – Banco Exemplo  Agência: 4321  Conta: 98765-0",
        "",
        f"Finalidade: Pagamento de indenização securitária – Sinistro nº {sinistro}",
        "Autenticação: 7F3A-91C2-55D0-AB12-0E44",
    ])])


def termo(valor: str, segurado: str, doc_seg: str, sinistro: str, apolice: str, data_br: str,
          causador: str) -> Doc:
    return Doc("TERMO_QUITACAO_SUBROGACAO", "Termo de quitação e sub-rogação", [Page([
        "TERMO DE QUITAÇÃO E SUB-ROGAÇÃO DE DIREITOS",
        f"Pelo presente instrumento, {segurado}, inscrito(a) sob o nº {doc_seg}, declara ter recebido da "
        f"{SEG['nome']} a importância de R$ {valor}, a título de indenização integral referente ao sinistro "
        f"nº {sinistro}, coberto pela apólice nº {apolice}.",
        "",
        "Com o recebimento da indenização, o(a) segurado(a) dá à seguradora plena, geral e irrevogável "
        "quitação, e a sub-roga em todos os direitos e ações que lhe competiam contra o causador do dano, "
        f"em especial contra {causador}, até o limite do valor pago, nos termos do art. 786 do Código "
        "Civil e da Súmula 188 do STF.",
        "",
        f"Local e data: São Paulo, {data_br}.",
        "",
        "_______________________________________",
        segurado,
    ])])


# ------------------------------------------------------------------ cenário AUTO

def scenario_auto() -> Scenario:
    sin, apo = "AUT-2023-0045871", "01.31.0098765"
    segurado, cpf = "Rafael Augusto Nogueira", "123.456.789-09"
    causador, cpf_c = "Bruno César Albuquerque", "987.654.321-00"
    placa_c = "FIC-2B34"
    apolice = Doc("APOLICE", "Apólice de seguro auto nº " + apo, [
        Page([f"APÓLICE DE SEGURO AUTO Nº {apo}",
              f"Seguradora: {SEG['nome']} – CNPJ {SEG['cnpj']} – Código SUSEP 09999",
              f"Segurado: {segurado} – CPF {cpf}",
              "Endereço: Rua das Palmeiras, 250, apto. 42, Pinheiros, São Paulo/SP",
              "",
              "**Vigência: das 24h de 10/03/2023 às 24h de 10/03/2024",
              "Ramo: 0531 – Automóvel",
              "Veículo segurado: Toyota Corolla XEi 2.0 Flex, ano/modelo 2021/2022, placa SEG-1A23, "
              "chassi 9BRBDWHE0M0000000",
              "",
              "**COBERTURAS CONTRATADAS",
              "Casco – Compreensiva (colisão, incêndio, roubo e furto): 100% da Tabela FIPE",
              "RCF-V Danos materiais: R$ 100.000,00   RCF-V Danos corporais: R$ 100.000,00",
              "Franquia casco (normal): R$ 3.850,00",
              "Prêmio total: R$ 4.912,37 em 10 parcelas"]),
        Page(["CONDIÇÕES GERAIS – SEGURO AUTO (EXTRATO)",
              "Cláusula 22 – Sub-rogação: paga a indenização, a Seguradora ficará sub-rogada, até o limite "
              "do valor indenizado, nos direitos e ações que competirem ao Segurado contra o autor do dano.",
              "Cláusula 23 – Perda parcial: a indenização corresponderá ao valor dos reparos, deduzida a "
              "franquia contratada.",
              f"Apólice nº {apo} – Processo SUSEP nº 15414.000000/2020-00"]),
    ])
    aviso = Doc("AVISO_SINISTRO", "Aviso de sinistro nº " + sin, [Page([
        "AVISO DE SINISTRO – AUTOMÓVEL",
        f"Nº do sinistro: {sin}     Apólice: {apo}",
        f"Segurado: {segurado} – CPF {cpf}",
        "Data do evento: 18/11/2023, às 07h40     Comunicado em: 18/11/2023",
        "Local: Av. Rebouças, altura do nº 1500, Pinheiros, São Paulo/SP",
        "",
        "Descrição do segurado: estava parado no semáforo vermelho quando o veículo VW Amarok, placa "
        f"{placa_c}, conduzido por {causador}, colidiu na traseira do meu veículo, que foi projetado "
        "contra o carro da frente.",
        "",
        "Terceiro envolvido: " + causador + f" – CPF {cpf_c} – placa {placa_c}",
        "Houve registro policial: sim (B.O. nº 4521/2023 – 14º DP).",
    ])])
    bo = Doc("BOLETIM_OCORRENCIA", "Boletim de ocorrência nº 4521/2023", [
        Page(["SECRETARIA DA SEGURANÇA PÚBLICA – POLÍCIA CIVIL DO ESTADO DE SÃO PAULO",
              "**BOLETIM DE OCORRÊNCIA Nº 4521/2023 – 14º DISTRITO POLICIAL – PINHEIROS",
              "Natureza: Acidente de trânsito sem vítima – colisão traseira",
              "Data/hora do fato: 18/11/2023 07:40     Data/hora do registro: 18/11/2023 10:12",
              "Local: Av. Rebouças, 1500, Pinheiros, São Paulo/SP",
              "",
              f"PARTE 1 (vítima): {segurado}, CPF {cpf}, condutor do Toyota Corolla placa SEG-1A23.",
              f"PARTE 2 (autor): {causador}, CPF {cpf_c}, residente na Rua Harmonia, 77, Vila Madalena, "
              f"São Paulo/SP, condutor da VW Amarok placa {placa_c}.",
              "",
              "HISTÓRICO: Segundo relatado, o veículo 1 encontrava-se parado aguardando a abertura do "
              "semáforo quando foi atingido na traseira pelo veículo 2, que trafegava em velocidade "
              "incompatível com a via. O condutor do veículo 2 admitiu ter se distraído com o celular."]),
        Page(["BOLETIM DE OCORRÊNCIA Nº 4521/2023 – CONTINUAÇÃO",
              "Danos: veículo 1 com avarias na traseira e na dianteira (engavetamento); veículo 2 com "
              "avarias no para-choque dianteiro.",
              "Testemunha: Luciana Prado Mendes, que presenciou o fato da calçada.",
              "Exames: não houve vítimas. Partes orientadas quanto ao prazo de representação.",
              "Autoridade: Dr. Fernando Rezende – Delegado de Polícia."]),
    ])
    fotos = Doc("FOTOS", "Registro fotográfico das avarias", [Page(
        ["REGISTRO FOTOGRÁFICO – VISTORIA DE SINISTRO " + sin,
         "Fotos 1 a 3: avarias na traseira (tampa, para-choque, lanternas) e dianteira do veículo SEG-1A23."],
        size=landscape(A4), photos=3)])
    laudo = Doc("LAUDO_TECNICO", "Laudo de vistoria e regulação", [Page([
        "LAUDO DE VISTORIA E REGULAÇÃO DE SINISTRO",
        f"Sinistro: {sin}   Regulador: Vistoriadora Exemplo Ltda.",
        "Data da vistoria: 21/11/2023 – Oficina Auto Center Pinheiros",
        "Constatações: danos compatíveis com colisão traseira seguida de impacto frontal. Necessária a "
        "substituição de tampa traseira, para-choques, radiador, capô e faróis, além de funilaria e pintura.",
        "Conclusão: perda parcial. Reparo economicamente viável (orçamento aprovado inferior a 75% da FIPE).",
        "Responsabilidade: elementos indicam culpa exclusiva do condutor do veículo terceiro (colisão traseira).",
    ], rotate=90)])
    orc = Doc("ORCAMENTO_NF_REPARO", "Orçamento de reparo – R$ 48.230,00", [Page([
        "ORÇAMENTO DE REPARO Nº 2023/1187 – AUTO CENTER PINHEIROS LTDA.",
        "CNPJ 55.666.777/0001-20 – Rua Teodoro Sampaio, 2000, São Paulo/SP",
        "Veículo: Toyota Corolla XEi placa SEG-1A23     Sinistro: " + sin,
        "",
        "Peças: tampa traseira, para-choque traseiro e dianteiro, radiador, capô, faróis ..... R$ 31.480,00",
        "Mão de obra: funilaria, pintura e montagem ............................................ R$ 14.950,00",
        "Alinhamento, balanceamento e diagnóstico eletrônico ................................ R$ 1.800,00",
        "",
        "**TOTAL DO ORÇAMENTO: R$ 48.230,00",
        "Validade: 15 dias. Prazo de execução: 20 dias úteis.",
    ])])
    cnh = Doc("DOCS_VEICULO_CONDUTOR", "CRLV e CNH do segurado", [
        Page(["CERTIFICADO DE REGISTRO E LICENCIAMENTO DE VEÍCULO – CRLV-e",
              "Placa: SEG-1A23   RENAVAM: 01234567890   Exercício: 2023",
              "Marca/Modelo: TOYOTA/COROLLA XEI 2.0   Ano fab./mod.: 2021/2022",
              f"Proprietário: {segurado.upper()} – CPF {cpf}"]),
        Page(["CARTEIRA NACIONAL DE HABILITAÇÃO",
              f"Nome: {segurado.upper()}   CPF: {cpf}",
              "Categoria: B   Validade: 05/08/2027   Nº registro: 00000000000"]),
    ])
    notif = Doc("RECLAMACAO_ADMINISTRATIVA", "Notificação extrajudicial ao causador", [Page([
        "NOTIFICAÇÃO EXTRAJUDICIAL",
        f"Notificado: {causador} – CPF {cpf_c} – Rua Harmonia, 77, Vila Madalena, São Paulo/SP",
        f"A {SEG['nome']}, sub-rogada nos direitos de seu segurado {segurado}, NOTIFICA Vossa Senhoria "
        f"para que, no prazo de 10 dias, efetue o ressarcimento de R$ 48.320,00 referente ao sinistro {sin}, "
        "sob pena de ajuizamento de ação regressiva.",
        "São Paulo, 15/01/2024.",
    ])])
    pag = ted("48.320,00", "24/12/2023", segurado, cpf, sin)
    files = [
        FileSpec("scan001.pdf", [apolice]),
        FileSpec("doc final (2).pdf", [aviso, bo, fotos]),  # misturado: 3 documentos
        FileSpec("procuracao assinada.pdf", [procuracao()]),
        FileSpec("estatuto+ata.pdf", [estatuto()]),
        FileSpec("IMG_20231121_vistoria.pdf", [laudo]),
        FileSpec("orcamento oficina.pdf", [orc]),
        FileSpec("comprovante.pdf", [pag]),
        FileSpec("WhatsApp Image 2023-11-20 docs.pdf", [cnh]),
        FileSpec("notificacao.pdf", [notif]),
        FileSpec("comprovante (1).pdf", [pag], duplicate_of="comprovante.pdf"),
    ]
    facts = {
        "scenario": "AUTO", "seguradora_nome": SEG["nome"], "seguradora_cnpj": SEG["cnpj"],
        "segurado_nome": segurado, "segurado_cpf_cnpj": cpf, "apolice_numero": apo,
        "apolice_vigencia_inicio": "2023-03-10", "apolice_vigencia_fim": "2024-03-10",
        "apolice_ramo": "Automóvel", "sinistro_numero": sin, "sinistro_data": "2023-11-18",
        "sinistro_local": "Av. Rebouças, 1500, Pinheiros, São Paulo/SP",
        "causador_nome": causador, "causador_cpf_cnpj": cpf_c,
        "causador_endereco": "Rua Harmonia, 77, Vila Madalena, São Paulo/SP", "causador_placa": placa_c,
        "valor_pago": "48320.00", "data_pagamento": "2023-12-24", "franquia": "3850.00",
    }
    return Scenario("auto", "AUTO", files, facts, ["TERMO_QUITACAO_SUBROGACAO"], [
        {"field": "valor_pago", "values": ["48320.00", "48230.00"],
         "sources": ["comprovante.pdf", "orcamento oficina.pdf"]}])


# ------------------------------------------------------------------ cenário ELÉTRICO

def scenario_eletrico() -> Scenario:
    sin, apo = "RES-2025-0012093", "14.18.0045512"
    segurado, cpf = "Mariana Lopes Carvalho", "321.654.987-11"
    conc, cnpj_c = "Companhia Paulista de Distribuição de Energia – CPDE (fictícia)", "44.555.666/0001-02"
    end_c = "Rua da Energia, 500, Centro, Campinas/SP"
    apolice = Doc("APOLICE", "Apólice de seguro residencial nº " + apo, [Page([
        f"APÓLICE DE SEGURO RESIDENCIAL Nº {apo}",
        f"Seguradora: {SEG['nome']} – CNPJ {SEG['cnpj']}",
        f"Segurada: {segurado} – CPF {cpf}",
        "Local de risco: Rua dos Ipês, 88, Jardim das Flores, Campinas/SP",
        "**Vigência: 01/06/2024 a 01/06/2025     Ramo: 0114 – Compreensivo residencial",
        "Coberturas: Incêndio/raio/explosão R$ 500.000,00; Danos elétricos R$ 30.000,00; "
        "Roubo R$ 20.000,00",
        "Franquia danos elétricos: R$ 500,00",
    ])])
    aviso = Doc("AVISO_SINISTRO", "Aviso de sinistro nº " + sin, [Page([
        "COMUNICAÇÃO DE SINISTRO – DANOS ELÉTRICOS",
        f"Sinistro nº {sin}   Apólice {apo}",
        f"Segurada: {segurado}",
        "Data do evento: 20/01/2025, por volta das 19h15",
        "Relato: após queda e retorno brusco de energia em todo o bairro, diversos equipamentos queimaram: "
        "geladeira, smart TV 65\", ar-condicionado split e portão eletrônico. Vizinhos relataram o mesmo.",
        f"Concessionária responsável: {conc}",
    ])])
    laudo_ok = Doc("ORCAMENTO_NF_REPARO", "Orçamentos de assistência técnica", [
        Page(["ORÇAMENTO – ELETROTEC ASSISTÊNCIA TÉCNICA LTDA.",
              "Equipamento: Smart TV 65\" – placa principal e fonte queimadas por surto de tensão.",
              "Reparo inviável. Valor de reposição de equipamento equivalente: R$ 6.290,00"]),
        Page(["NOTA FISCAL DE SERVIÇO Nº 000.457 – FRIOTEC REFRIGERAÇÃO",
              "Serviço: troca de placa eletrônica do ar-condicionado split e compressor da geladeira",
              "Valor total dos serviços e peças: R$ 7.080,00"]),
    ])
    pag = ted("12.870,00", "10/03/2025", segurado, cpf, sin)
    term = termo("12.780,00", segurado, cpf, sin, apo, "12/03/2025", conc)
    recl = Doc("RECLAMACAO_ADMINISTRATIVA", "Pedido de ressarcimento à concessionária e resposta", [
        Page(["PEDIDO DE RESSARCIMENTO DE DANOS ELÉTRICOS – PROTOCOLO 2025.0001.7788",
              f"À {conc} – CNPJ {cnpj_c}",
              f"Solicitante: {segurado}. Data da ocorrência: 20/01/2025. Unidade consumidora nº 1234567.",
              "Fundamento: arts. 609 e seguintes da Resolução Normativa ANEEL nº 1.000/2021."]),
        Page(["RESPOSTA DA CONCESSIONÁRIA",
              "Protocolo 2025.0001.7788 – Indeferido. Alega-se ausência de nexo causal, embora o registro "
              "de perturbação no alimentador CPS-07 conste às 19h12 de 20/01/2025."]),
    ])
    fotos = Doc("FOTOS", "Fotos dos equipamentos danificados", [Page(
        ["REGISTRO FOTOGRÁFICO – EQUIPAMENTOS DANIFICADOS",
         "Placas eletrônicas com marcas de queima. Sinistro " + sin], photos=2)])
    files = [
        FileSpec("scan_0001.pdf", [apolice]),
        FileSpec("documentos sinistro.pdf", [aviso, fotos, laudo_ok]),  # misturado
        FileSpec("ted.pdf", [pag]),
        FileSpec("recibo quitacao.pdf", [term]),
        FileSpec("procuracao.pdf", [procuracao()]),
        FileSpec("doc seguradora.pdf", [estatuto()]),
        FileSpec("aneel protocolo.pdf", [recl]),
        FileSpec("Novo Documento 2025-03-12.pdf", [Doc("OUTROS", "E-mail de encaminhamento", [Page([
            "E-MAIL – ENCAMINHAMENTO DE DOCUMENTOS",
            "De: regulacao@aurora-seguros.example   Para: juridico@brandaolima.example",
            "Assunto: Sinistro " + sin + " – documentos para regressiva",
            "Segue documentação para análise de viabilidade de ressarcimento."])])]),
        FileSpec("ted (2).pdf", [pag], duplicate_of="ted.pdf"),
    ]
    facts = {
        "scenario": "ELETRICO", "seguradora_nome": SEG["nome"], "seguradora_cnpj": SEG["cnpj"],
        "segurado_nome": segurado, "segurado_cpf_cnpj": cpf, "apolice_numero": apo,
        "apolice_vigencia_inicio": "2024-06-01", "apolice_vigencia_fim": "2025-06-01",
        "apolice_ramo": "Compreensivo residencial", "sinistro_numero": sin, "sinistro_data": "2025-01-20",
        "sinistro_local": "Rua dos Ipês, 88, Jardim das Flores, Campinas/SP",
        "causador_nome": conc, "causador_cpf_cnpj": cnpj_c, "causador_endereco": end_c,
        "valor_pago": "12870.00", "data_pagamento": "2025-03-10", "franquia": "500.00",
    }
    return Scenario("eletrico", "ELETRICO", files, facts, ["LAUDO_TECNICO"], [
        {"field": "valor_pago", "values": ["12870.00", "12780.00"],
         "sources": ["ted.pdf", "recibo quitacao.pdf"]}])


# ------------------------------------------------------------------ cenário TRANSPORTE

def scenario_transporte() -> Scenario:
    sin, apo = "TRN-2024-0007731", "54.21.0003301"
    segurado, cnpj_s = "Distribuidora Horizonte de Eletrônicos Ltda.", "22.333.444/0001-55"
    transp, cnpj_t = "Rodovias Rápidas Transportes Ltda.", "66.777.888/0001-99"
    end_t = "Rod. Anhanguera, km 105, Galpão 3, Sumaré/SP"
    apolice = Doc("APOLICE", "Apólice de transporte nacional nº " + apo, [Page([
        f"APÓLICE DE SEGURO DE TRANSPORTE NACIONAL – EMBARCADOR Nº {apo}",
        f"Seguradora: {SEG['nome']} – CNPJ {SEG['cnpj']}",
        f"Segurado: {segurado} – CNPJ {cnpj_s}",
        "**Vigência: 01/01/2024 a 01/01/2025   Ramo: 0621 – Transporte nacional",
        "Mercadorias: eletrônicos embalados. Cobertura ampla (avaria, roubo, acidente).",
        "Limite por embarque: R$ 500.000,00   Franquia: 10% dos prejuízos, mínimo R$ 2.000,00",
    ])])
    cte = Doc("DOCS_TRANSPORTE", "CT-e nº 88412 e DANFE da mercadoria", [
        Page(["DACTE – DOCUMENTO AUXILIAR DO CONHECIMENTO DE TRANSPORTE ELETRÔNICO",
              "CT-e nº 88412  Série 1  Emissão: 04/08/2024",
              f"Emitente/Transportador: {transp} – CNPJ {cnpj_t}",
              f"Remetente: {segurado}   Destinatário: Loja Exemplo Comércio Ltda. – Recife/PE",
              "Valor total da carga: R$ 214.900,00   Peso: 3.420 kg   Volumes: 180",
              "Chave de acesso: 3524 0866 7778 8800 0199 5700 1000 0884 1210 0000 0000"]),
        Page(["DANFE – NOTA FISCAL ELETRÔNICA Nº 145.002",
              f"Emitente: {segurado} – CNPJ {cnpj_s}",
              "Produtos: 120 notebooks e 60 monitores 27\"",
              "Valor total da nota: R$ 214.900,00"]),
    ])
    avaria = Doc("DOCS_TRANSPORTE", "Termo de avaria e ressalvas no recebimento", [Page([
        "TERMO DE AVARIA E RESSALVAS NO RECEBIMENTO",
        "CT-e 88412 – Recebimento em 09/08/2024 no CD do destinatário.",
        "Constatado: tombamento da carga no baú; 74 volumes com embalagem amassada e produtos danificados.",
        f"Transportador ciente: motorista do veículo placa TRK-9C87 a serviço de {transp}.",
    ])])
    bo = Doc("BOLETIM_OCORRENCIA", "Boletim de ocorrência rodoviário", [Page([
        "POLÍCIA RODOVIÁRIA FEDERAL – BOLETIM DE ACIDENTE DE TRÂNSITO Nº 0099887766",
        "Data: 05/08/2024 03:20   Local: BR-116, km 420, Feira de Santana/BA",
        "Tipo: saída de pista com tombamento parcial. Veículo placa TRK-9C87.",
        "Causa provável: velocidade incompatível e fadiga do condutor.",
    ])])
    laudo = Doc("LAUDO_TECNICO", "Relatório de regulação do sinistro de transporte", [
        Page(["RELATÓRIO DE REGULAÇÃO DE SINISTRO – TRANSPORTES",
              f"Sinistro {sin} – Regulador: Regulação Exemplo Ltda.",
              "Prejuízo apurado: 74 volumes (52 notebooks e 22 monitores), valor de R$ 189.540,00.",
              "Salvados vendidos por R$ 2.090,00. Indenização líquida recomendada: R$ 187.450,00."]),
        Page(["RELATÓRIO DE REGULAÇÃO – CONCLUSÃO",
              "Responsabilidade do transportador caracterizada (arts. 749 e 750 do Código Civil).",
              "Recomenda-se o ajuizamento de ação regressiva."]),
    ])
    pag = ted("187.450,00", "02/10/2024", segurado, cnpj_s, sin)
    term = termo("187.540,00", segurado, cnpj_s, sin, apo, "03/10/2024", transp)
    files = [
        FileSpec("scan 14-08.pdf", [apolice]),
        FileSpec("CTE_NF.pdf", [cte]),
        FileSpec("docs recebimento.pdf", [avaria, bo, laudo]),  # misturado
        FileSpec("pagamento indenizacao.pdf", [pag]),
        FileSpec("termo sub-rogacao assinado.pdf", [term]),
        FileSpec("procuração.pdf", [procuracao()]),
        FileSpec("seguradora docs.pdf", [estatuto()]),
        FileSpec("Fotos carga.pdf", [Doc("FOTOS", "Fotos da carga avariada", [Page(
            ["REGISTRO FOTOGRÁFICO – CARGA AVARIADA", "CT-e 88412 – baú tombado e volumes danificados."],
            size=landscape(A4), photos=2)])]),
        FileSpec("CTE_NF - Copia.pdf", [cte], duplicate_of="CTE_NF.pdf"),
    ]
    facts = {
        "scenario": "TRANSPORTE", "seguradora_nome": SEG["nome"], "seguradora_cnpj": SEG["cnpj"],
        "segurado_nome": segurado, "segurado_cpf_cnpj": cnpj_s, "apolice_numero": apo,
        "apolice_vigencia_inicio": "2024-01-01", "apolice_vigencia_fim": "2025-01-01",
        "apolice_ramo": "Transporte nacional", "sinistro_numero": sin, "sinistro_data": "2024-08-05",
        "sinistro_local": "BR-116, km 420, Feira de Santana/BA",
        "causador_nome": transp, "causador_cpf_cnpj": cnpj_t, "causador_endereco": end_t,
        "causador_placa": "TRK-9C87", "valor_pago": "187450.00", "data_pagamento": "2024-10-02",
    }
    return Scenario("transporte", "TRANSPORTE", files, facts, ["AVISO_SINISTRO"], [
        {"field": "valor_pago", "values": ["187450.00", "187540.00"],
         "sources": ["pagamento indenizacao.pdf", "termo sub-rogacao assinado.pdf"]}])


# ------------------------------------------------------------------ main

def build(sc: Scenario) -> Path:
    out = FIX / sc.key
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    files_json: dict[str, object] = {}
    logical: list[dict[str, object]] = []
    for fs in sc.files:
        if fs.duplicate_of:  # duplicado = cópia byte a byte (mesmo SHA-256)
            shutil.copyfile(out / fs.duplicate_of, out / fs.name)
        else:
            write_pdf(out / fs.name, fs.docs)
        pages: list[dict[str, object]] = []
        idx = 0
        for d in fs.docs:
            doc_pages = list(range(idx, idx + len(d.pages)))
            for k, _ in enumerate(d.pages):
                pages.append({"page": idx, "doc_type": d.doc_type, "is_first_page_of_new_doc": k == 0})
                idx += 1
            if fs.duplicate_of is None:
                logical.append({"file": fs.name, "doc_type": d.doc_type, "title": d.title,
                                "pages": doc_pages})
        files_json[fs.name] = {"duplicate_of": fs.duplicate_of, "pages": pages}
    expected = {
        "scenario": sc.enum,
        "files": files_json,
        "duplicates": {f.name: f.duplicate_of for f in sc.files if f.duplicate_of},
        "missing_doc_types": sc.missing,
        "conflicts": sc.conflicts,
        "logical_docs": logical,
        "facts": sc.facts,
    }
    (out / "expected.json").write_text(json.dumps(expected, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main() -> None:
    for sc in (scenario_auto(), scenario_eletrico(), scenario_transporte()):
        out = build(sc)
        print(f"{sc.key}: {len(sc.files)} PDFs em {out}")


if __name__ == "__main__":
    main()
