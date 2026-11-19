# Plano MVP/PoC: "Regressiva": organização de documentos para ação regressiva de ressarcimento

> Entregável: ao aprovar, este plano será salvo como `PLANO_MVP.md` na raiz de `/home/bcr/Documentos/SANFRAN/hackton1/` (o diretório está vazio hoje, só tem `.claude/`).

## 1. Contexto

**Dor:** o advogado de direito securitário que atua para seguradoras recebe do cliente (seguradora ou regulador de sinistros) dezenas de PDFs soltos, com nomes como `scan001.pdf` ou `doc final (2).pdf`, às vezes vários documentos num único arquivo. Para ajuizar a **ação regressiva de ressarcimento** (art. 786 do CC e Súmula 188/STF: a seguradora se sub-roga nos direitos do segurado até o valor efetivamente pago), ele precisa:
1. abrir cada PDF e identificar o que é;
2. conferir se está faltando algum documento essencial;
3. montar um PDF único, ordenado, com índice e numeração ("Doc. 01, Doc. 02…");
4. redigir a petição citando esses documentos e os dados do sinistro.

Tudo isso é manual, repetitivo e sujeito a erro (documento esquecido, valor errado, prescrição perdida).

**Resultado esperado do MVP:** o cliente sobe os PDFs num link. O sistema classifica cada documento, extrai os dados-chave, aponta o que falta e gera (a) um **PDF consolidado** com capa, índice e marcadores e (b) uma **minuta da petição** em .docx, já preenchida e com as referências aos documentos. O advogado revisa e ajusta nas duas pontas.

## 2. Escopo do MVP

**Dentro:**
- Portal do cliente: link único por caso, upload de vários PDFs (arrastar e soltar), sem login.
- Painel do advogado: lista de casos, status, revisão da classificação, download dos entregáveis.
- Classificação por IA no nível de **página**, para separar PDFs que juntam vários documentos.
- Extração de dados estruturados (apólice, segurado, causador, datas, valores).
- Checklist de documentos obrigatórios por tipo de sinistro, com alerta de faltantes.
- Alerta de **prescrição** (3 anos contados do pagamento da indenização, art. 206, §3º, V, CC).
- PDF consolidado: capa, índice, folhas separadoras, bookmarks, carimbo "Doc. NN – fls. X".
- Minuta da petição inicial (.docx) a partir de um modelo, com dados extraídos e remissões aos documentos.

**Fora (pós-MVP):** integração com PJe/e-SAJ, multi-escritório/multi-tenant, assinatura digital, cálculo de correção monetária via índices oficiais, OCR on-premise, e-mail/WhatsApp de notificação.

## 3. Tipos de sinistro e taxonomia de documentos

O MVP cobre os três cenários mais comuns de regressiva:

| Cenário | Réu típico |
|---|---|
| **Auto** (colisão) | condutor ou proprietário do veículo causador |
| **Danos elétricos** | concessionária de energia (oscilação ou descarga) |
| **Transporte de carga** | transportadora (avaria ou roubo) |

**Categorias** (enum `DocType`; a ordem abaixo é a ordem no PDF final):
1. `PROCURACAO`: procuração ou substabelecimento
2. `DOCS_SEGURADORA`: estatuto/contrato social e atos de representação da autora
3. `APOLICE`: apólice ou certificado do seguro, condições gerais
4. `AVISO_SINISTRO`: aviso ou comunicação de sinistro
5. `BOLETIM_OCORRENCIA`: B.O. ou registro policial
6. `LAUDO_TECNICO`: laudo de regulação, laudo técnico de danos elétricos, vistoria
7. `ORCAMENTO_NF_REPARO`: orçamentos, notas fiscais de conserto ou reposição
8. `FOTOS`: registro fotográfico
9. `COMPROVANTE_PAGAMENTO`: comprovante da indenização (TED, recibo bancário)
10. `TERMO_QUITACAO_SUBROGACAO`: recibo de indenização com cláusula de sub-rogação (**essencial**)
11. `DOCS_TRANSPORTE`: CT-e, DACTE, NF da mercadoria, termo de avaria, ressalvas
12. `DOCS_VEICULO_CONDUTOR`: CRLV, CNH, croqui
13. `RECLAMACAO_ADMINISTRATIVA`: pedido à concessionária e resposta (ANEEL, Res. 1.000/2021), notificação extrajudicial ao causador
14. `OUTROS`: não classificado, vai para revisão manual

**Checklist mínimo por cenário** (fica em `checklists.py`, editável):
- Comum a todos: APOLICE, AVISO_SINISTRO, COMPROVANTE_PAGAMENTO, TERMO_QUITACAO_SUBROGACAO, PROCURACAO
- Auto: + BOLETIM_OCORRENCIA, ORCAMENTO_NF_REPARO
- Elétrico: + LAUDO_TECNICO, ORCAMENTO_NF_REPARO (RECLAMACAO_ADMINISTRATIVA recomendado)
- Transporte: + DOCS_TRANSPORTE

## 4. Arquitetura

```
[Cliente] --upload--> [Next.js: /enviar/{token}]
                              |
                              v
                     [FastAPI backend] --> SQLite (casos, arquivos, páginas, docs, extrações)
                              |               storage/ local (PDFs originais e gerados)
                              v
                  [Pipeline em background]
     1. normaliza PDF (pypdf; renderiza páginas em PNG com pypdfium2)
     2. classifica páginas e agrupa em documentos (Claude)
     3. extrai campos por documento (Claude, saída JSON estruturada)
     4. consolida dados do caso + checklist + prescrição
     5. gera PDF consolidado (pypdf + reportlab)
     6. gera minuta .docx (docxtpl)
                              |
                              v
[Advogado] <--revisa/ajusta-- [Next.js: /casos/{id}]
```

**Stack (escolhida para entregar rápido num hackathon):**
- Backend: **Python 3.12 + FastAPI**, SQLModel + SQLite, `BackgroundTasks` (sem fila externa no MVP).
- PDF: `pypdf` (split/merge/bookmarks), `pypdfium2` (renderizar páginas), `reportlab` (capa, índice, separadores, carimbo).
- IA: **Claude API** (SDK `anthropic`). Os PDFs vão direto como bloco `document`, que já trata PDF escaneado sem precisar de OCR à parte. `claude-haiku-5-5` faz a classificação de páginas (barato e rápido) e `claude-sonnet-5-5` faz a extração e a redação dos fatos. Tool use / JSON schema garante saída estruturada.
- Petição: `docxtpl` (Jinja dentro de .docx), com modelo em `templates/peticao_regressiva.docx`.
- Frontend: **Next.js + Tailwind**, com duas áreas (`/enviar/[token]` e `/casos`).
- Execução local: `docker compose` (api e web) ou `uv run` mais `npm run dev`.

## 5. Pipeline de IA em detalhe

### 5.1 Classificação e segmentação
- Para cada arquivo enviado, mandar o PDF (lotes de até cerca de 20 páginas) e pedir, **por página**: `{page, doc_type, is_first_page_of_new_doc, confidence, short_title}`.
- Juntar páginas consecutivas do mesmo tipo em um `Document` lógico, quebrando onde `is_first_page_of_new_doc = true`.
- Abaixo de `confidence < 0.7`, marcar para revisão humana (badge amarelo no painel).
- Deduplicação: hash SHA-256 por arquivo e por página, para descartar documento repetido.

### 5.2 Extração (schema `CaseFacts`)
```
seguradora_autora {nome, cnpj}
segurado {nome, cpf_cnpj}
apolice {numero, vigencia_inicio, vigencia_fim, ramo}
sinistro {numero, data, local, descricao_resumida, cenario: AUTO|ELETRICO|TRANSPORTE}
causador {nome, cpf_cnpj, endereco, placa_veiculo?}
indenizacao {valor_pago, data_pagamento, franquia?}
evidencias_culpa [ {fato, doc_ref} ]
```
- Cada campo guarda `source_doc_id` e `page` (rastreabilidade: o advogado clica e vê de onde veio).
- Conflitos (por exemplo, valor do comprovante diferente do valor do termo de quitação) viram **alerta**, nunca são resolvidos em silêncio.

### 5.3 Regras determinísticas (sem IA)
- Checklist: compara os `DocType` presentes com o checklist do cenário.
- Prescrição: `data_pagamento + 3 anos`. Vermelho se faltam menos de 90 dias ou se já venceu.
- Valor da causa: `valor_pago` (sem correção no MVP, com nota "atualizar").

## 6. Entregáveis gerados

### 6.1 PDF consolidado (`dossie_{caso}.pdf`)
1. Capa: partes, nº do sinistro, apólice, data de geração.
2. Índice: "Doc. 01 – Procuração ........ fls. 3".
3. Para cada documento, na ordem do enum: folha separadora + páginas originais, com carimbo no rodapé `Doc. NN – fl. X/Y`.
4. Bookmarks (outline) por documento.
5. Opcional: dividir em partes de no máximo N MB, para respeitar o limite de upload do PJe (configurável, padrão 10 MB).

### 6.2 Minuta da petição (`peticao_{caso}.docx`)
Modelo com seções fixas e trechos variáveis:
- Endereçamento (comarca do domicílio do réu ou do local do fato, campo editável)
- Qualificação das partes (de `CaseFacts`)
- **Dos fatos**: narrativa gerada pelo Claude a partir das extrações, sempre citando "(Doc. NN)"
- **Do direito**: blocos fixos por cenário (sub-rogação, art. 786 CC, Súmula 188/STF, arts. 186 e 927 CC; responsabilidade objetiva da concessionária, art. 37, §6º, CF e CDC, no elétrico; responsabilidade do transportador, arts. 749-750 CC, no transporte)
- **Dos pedidos**: condenação ao valor pago, com correção desde o desembolso (Súmula 43/STJ) e juros desde o evento (Súmula 54/STJ, extracontratual) ou desde a citação (contratual)
- Valor da causa e rol de documentos (gerado a partir do índice)

Marcadores `[[REVISAR]]` em todo trecho de baixa confiança.

## 7. Telas

**Cliente (`/enviar/[token]`):** nome do caso, seletor do cenário (auto, elétrico, transporte), área de arrastar e soltar com barra de progresso, lista do que já foi enviado e, depois do processamento, lista do que ainda falta, para o cliente completar no mesmo link.

**Advogado:**
- `/casos`: tabela com caso, cenário, status (recebendo, processando, revisar, pronto) e alerta de prescrição.
- `/casos/[id]`: três colunas: (1) documentos detectados, com miniatura, tipo (dropdown para corrigir) e arrastar para reordenar; (2) dados extraídos editáveis, com link para a página de origem; (3) checklist e alertas. Botões **Gerar dossiê PDF** e **Gerar minuta .docx**. Correções manuais disparam nova geração, sem reprocessar a IA.
- Botão "Novo caso" gera o link de upload para mandar ao cliente.

## 8. Estrutura de pastas

```
hackton1/
  PLANO_MVP.md
  backend/
    app/main.py              # rotas FastAPI
    app/models.py            # Case, UploadedFile, Page, Document, Extraction
    app/pipeline/
      classify.py            # 5.1
      extract.py             # 5.2
      rules.py               # checklist, prescrição, conflitos
      assemble_pdf.py        # 6.1
      draft_petition.py      # 6.2
    app/doc_types.py         # enum DocType + ordem
    app/checklists.py
    app/prompts/             # prompts versionados (.md)
    templates/peticao_regressiva.docx
    tests/fixtures/          # PDFs sintéticos de exemplo
  frontend/                  # Next.js
  storage/                   # gitignored
  docker-compose.yml
```

## 9. API (resumo)

- `POST /cases` cria o caso e devolve `upload_token`
- `POST /upload/{token}` recebe arquivos multipart e dispara o pipeline
- `GET /upload/{token}/status` mostra progresso e faltantes (visão do cliente)
- `GET /cases`, `GET /cases/{id}` (documentos, fatos, alertas)
- `PATCH /cases/{id}/documents/{doc_id}` corrige tipo ou ordem
- `PATCH /cases/{id}/facts` edita os dados extraídos
- `POST /cases/{id}/dossie` e `POST /cases/{id}/peticao` geram e devolvem o arquivo

## 10. Cronograma sugerido (hackathon, cerca de 2 dias)

| Bloco | Entrega |
|---|---|
| H0–H3 | Repositório, FastAPI + SQLite, upload e storage, PDFs sintéticos de teste |
| H3–H8 | `classify.py` + segmentação; testes com as fixtures |
| H8–H12 | `extract.py` + `rules.py` (checklist, prescrição, conflitos) |
| H12–H16 | `assemble_pdf.py` (capa, índice, separadores, bookmarks, carimbo) |
| H16–H20 | `draft_petition.py` + modelo .docx |
| H20–H28 | Frontend: portal do cliente + painel do advogado |
| H28–H32 | Polimento, roteiro de demo, pitch |

## 11. Riscos e cuidados

- **LGPD / sigilo:** os documentos têm dados pessoais (CPF, CNH). No MVP: storage local, link com token aleatório e expiração, e nada de dados reais na demo. Antes de produção: contrato de tratamento com o provedor de IA, retenção zero e criptografia em repouso.
- **Alucinação:** todo dado extraído aponta para a página de origem; petição só como minuta, com `[[REVISAR]]`; o advogado é sempre o revisor final.
- **Qualidade do scan:** páginas ilegíveis viram `OUTROS` com baixa confiança e vão para revisão manual.
- **Custo:** Haiku para classificar e Sonnet só para extrair e redigir; cache por hash de página.

## 12. Verificação (como testar ponta a ponta)

1. Criar em `tests/fixtures/` um caso sintético de cada cenário (cerca de 10 PDFs cada, com um PDF "misturado" de 3 documentos juntos e um documento propositalmente faltando).
2. `pytest backend/tests`:
   - classificação: acurácia de no mínimo 90% por página nas fixtures;
   - segmentação: o PDF misturado vira 3 documentos;
   - regras: checklist aponta o faltante; prescrição calculada corretamente; conflito de valor detectado;
   - PDF consolidado: número de páginas = soma das originais + capa + índice + separadores; bookmarks presentes; ordem conforme o enum;
   - .docx: abre, contém nome do segurado, valor pago e "Doc. 01".
3. Teste manual da demo: `docker compose up`, criar caso, abrir o link do cliente, subir as fixtures, ver o status chegar em "revisar", corrigir um tipo no painel, gerar dossiê e minuta, abrir os dois arquivos e conferir.

## 13. Pitch da demo (3 min)

"40 PDFs bagunçados → 2 minutos → dossiê numerado e indexado + minuta da regressiva + alerta de que falta o termo de sub-rogação e de que a prescrição vence em 47 dias."
