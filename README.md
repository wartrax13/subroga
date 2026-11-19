# Sub-Roga

Plataforma para escritórios que movem **ações regressivas de ressarcimento** para seguradoras. O cliente sobe os PDFs do sinistro por um link; o sistema classifica cada página, extrai os dados do caso, aponta documentos faltantes, prescrição e valores divergentes, e gera o **dossiê PDF numerado** e a **minuta da petição (.docx)** para o advogado revisar.

- Plano do produto: [`PLANO_MVP.md`](PLANO_MVP.md)
- Contrato da API: [`docs/API.md`](docs/API.md)
- Avaliação da IA nas fixtures: [`docs/AVALIACAO.md`](docs/AVALIACAO.md)
- Protótipo estático (dados fictícios): [`prototipo/index.html`](prototipo/index.html)

## Rodar localmente

Requisitos: Python 3.11+ e [uv](https://docs.astral.sh/uv/).

```bash
cd backend
cp .env.example .env          # defina SUBROGA_JWT_SECRET (e uma chave de IA, se quiser)
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

Abra http://localhost:8000, crie o escritório em **Cadastro** e um caso em **Casos → Novo caso**. Copie o link de envio, abra-o (é a tela do cliente, sem login) e suba PDFs. Para testar sem documentos reais, use os PDFs fictícios de `backend/tests/fixtures/{auto,eletrico,transporte}/`.

### Provedor de IA

| `SUBROGA_LLM_PROVIDER` | Uso | Requisito |
|---|---|---|
| `openai` | classificação `gpt-4.1-mini`, extração e narrativa `gpt-4.1` | `OPENAI_API_KEY` |
| `anthropic` | `claude-haiku-5-5` / `claude-sonnet-5-5` | `ANTHROPIC_API_KEY` |
| `heuristic` | palavras-chave e regex no texto do PDF; sem rede e sem custo (demo offline) | — |

Sem valor, o sistema usa `openai` se houver `OPENAI_API_KEY`, senão `anthropic`, senão `heuristic`. Modelos podem ser trocados por `SUBROGA_OPENAI_CLASSIFY_MODEL`, `SUBROGA_OPENAI_EXTRACT_MODEL`, `SUBROGA_CLASSIFY_MODEL`, `SUBROGA_EXTRACT_MODEL`.

### Docker

```bash
docker build -t sub-roga .
docker run -p 8000:8000 -v sub-roga-data:/app/backend/storage \
  -e SUBROGA_JWT_SECRET=troque-por-um-segredo-longo -e OPENAI_API_KEY=... sub-roga
```

## Testes e avaliação

```bash
cd backend
uv run pytest -q                                        # 85 testes, sem rede
uv run python scripts/make_fixtures.py                  # regera os PDFs fictícios
uv run python scripts/eval_fixtures.py --provider heuristic
OPENAI_API_KEY=... uv run python scripts/eval_fixtures.py --provider openai
```

Frontend sem backend: `python3 frontend/dev/mock_server.py` → http://localhost:8077 (login `ana@prado.adv.br` / `senha1234`).

## Arquitetura

```
frontend/            SPA em JS puro (sem build), servida pelo próprio FastAPI
backend/app/
  main.py            app FastAPI, CORS, frontend estático, startup
  config.py auth.py  .env, JWT HS256, bcrypt
  models.py db.py    SQLModel/SQLite: Organization, User, Case, UploadedFile, Document, CaseFactsRow
  routers/           auth, cases (escopo por escritório), public (link do cliente)
  pipeline/
    run.py           orquestra: dedupe → classifica → segmenta → extrai → status
    normalize.py     hashes, dedupe por página, miniaturas
    llm.py           OpenAI / Anthropic / heurístico, saída estruturada, retry
    classify.py extract.py heuristic.py
    rules.py         checklist, prescrição (3 anos do pagamento), conflitos
    assemble_pdf.py  dossiê: capa, índice, separadores, carimbo, marcadores
    draft_petition.py minuta .docx (docxtpl) com [[REVISAR]]
```

**Multitenant:** cada usuário pertence a um escritório; toda rota autenticada filtra pelo escritório do token e responde 404 para casos de outro escritório. Arquivos ficam em `storage/orgs/{org}/cases/{case}/`. Papéis: `admin` (gerencia a equipe) e `advogado`.

## Limitações conhecidas

- SQLite e processamento em threads do próprio servidor (sem fila externa); adequado para MVP, não para alto volume.
- Sem migrações: mudanças de schema exigem apagar `backend/storage/app.db`.
- Sem limite de tentativas de login, recuperação de senha ou verificação de e-mail.
- PDFs escaneados sem texto embutido só foram testados no caminho via LLM com documentos sintéticos; o modo `heuristic` os manda para revisão manual.
- A minuta é sempre um rascunho: revisão do advogado é obrigatória.
