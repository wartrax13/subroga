# Contrato da API — Sub-Roga

Backend e frontend seguem este arquivo. Mudou algo aqui? Avise o outro lado.

- Base: tudo sob `/api`. O mesmo servidor FastAPI serve o frontend estático (`frontend/`) em `/` com fallback SPA: qualquer GET fora de `/api` e `/assets` devolve `frontend/index.html`.
- JSON em UTF-8. Datas `AAAA-MM-DD`; timestamps ISO 8601 UTC. Valores monetários como string decimal `"48320.00"`.
- Erros: `{"detail": "mensagem em português"}` com status HTTP adequado (400, 401, 403, 404, 409, 410, 413, 415, 422).
- Autenticação: `Authorization: Bearer <jwt>` (HS256, expira em 12 h, segredo em `SUBROGA_JWT_SECRET`). 401 = token ausente/inválido/expirado.

## Multitenant

- `Organization` = escritório. Todo `User` pertence a exatamente uma organização. Todo `Case` pertence a uma organização.
- Toda rota autenticada só enxerga dados da organização do usuário. Caso de outra organização → **404** (nunca 403, para não vazar existência).
- Papéis: `admin` (gerencia equipe) e `advogado`. Ambos operam casos.
- Rotas públicas do cliente usam só o token do link do caso (sem login).

## Auth

| Método | Rota | Corpo | Resposta |
|---|---|---|---|
| POST | `/api/auth/signup` | `{org_name, name, email, password}` | `201 {access_token, user, org}` — cria escritório + usuário admin. Email já usado → 409. Senha < 8 → 422 |
| POST | `/api/auth/login` | `{email, password}` | `{access_token, user, org}`; credenciais erradas → 401 |
| GET | `/api/auth/me` | — | `{user, org}` |

`user = {id, name, email, role}` · `org = {id, name}`

## Equipe (somente admin; advogado → 403)

| Método | Rota | Corpo | Resposta |
|---|---|---|---|
| GET | `/api/org/users` | — | `[user]` |
| POST | `/api/org/users` | `{name, email, password, role}` | `201 user` |
| DELETE | `/api/org/users/{id}` | — | `204`; não pode remover a si mesmo (409) |

## Casos (autenticado, escopo da organização)

| Método | Rota | Corpo | Resposta |
|---|---|---|---|
| POST | `/api/cases` | `{scenario: "AUTO"\|"ELETRICO"\|"TRANSPORTE", name}` | `201 CaseSummary` |
| GET | `/api/cases` | — | `[CaseSummary]` ordenado por prescrição mais próxima (sem data por último) |
| GET | `/api/cases/{id}` | — | `CaseDetail` |
| DELETE | `/api/cases/{id}` | — | `204` (apaga arquivos também) |
| POST | `/api/cases/{id}/token` | — | `CaseSummary` com novo `upload_token` (o antigo deixa de funcionar) |
| PATCH | `/api/cases/{id}/documents/{doc_id}` | `{doc_type?, title?}` | `CaseDetail` — marca `needs_review=false` |
| GET | `/api/cases/{id}/documents/{doc_id}/pages/{n}.png?scale=0.4` | — | PNG da n-ésima página (0-based) **do documento** |
| PATCH | `/api/cases/{id}/facts` | `{campo: "valor" \| {value, source_doc_id?, page?}}` | `CaseDetail` — o campo vira "editado manualmente": reprocessamentos não o sobrescrevem; também limpa `valores_alternativos[campo]` (conflito conferido) |
| POST | `/api/cases/{id}/approve` | `{force?: bool}` | `CaseDetail` com status `PRONTO`; se faltar documento obrigatório e `force` não for true → 409 |
| POST | `/api/cases/{id}/reopen` | — | `CaseDetail` com status recalculado (`PENDENTE`/`REVISAR`) |
| POST | `/api/cases/{id}/dossie?max_mb=10` | — | `application/pdf`; se `max_mb` informado e o PDF exceder, devolve `application/zip` com as partes |
| POST | `/api/cases/{id}/peticao?narrative=true` | — | `.docx`; com `narrative=true` usa o LLM para a seção "Dos fatos" (se falhar ou não houver LLM, usa o texto padrão e envia header `X-Narrative: fallback`) |

### CaseSummary
```json
{"id": "a1b2c3", "name": "Atlântica × Rodaviva", "scenario": "AUTO", "status": "REVISAR",
 "created_at": "2026-11-08T12:00:00Z", "upload_token": "…", "upload_url": "/enviar/<token>",
 "token_expires_at": "…", "n_files": 10, "n_docs": 11,
 "prescription": {"kind": "PRESCRIPTION", "severity": "critical", "message": "…", "deadline": "2026-12-24", "days_left": 46} | null,
 "critical_alerts": 2, "missing_count": 1}
```

### CaseDetail
`CaseSummary` + :
```json
{"error": null,
 "files": [{"id": 1, "filename": "scan001.pdf", "sha256": "…", "processed": true, "duplicate_pages": 0}],
 "documents": [ LogicalDoc + {"label": "Apólice", "number": 3, "page_count": 6} ],
 "facts": CaseFacts | null,
 "edited_fields": ["valor_pago"],
 "missing": [{"doc_type": "TERMO_QUITACAO_SUBROGACAO", "label": "Termo de quitação e sub-rogação"}],
 "alerts": [{"kind": "...", "severity": "info|warn|critical", "message": "...", "field": "valor_pago" | null}]}
```
`documents` vem na ordem do dossiê; `number` = Doc. NN. Status: `RECEBENDO` → `PROCESSANDO` → `PENDENTE` | `REVISAR` → `PRONTO`.

## Público (cliente, sem login)

| Método | Rota | Resposta |
|---|---|---|
| GET | `/api/public/upload/{token}` | `{org_name, case_name, scenario, status, files: [{filename, processed}], missing: ["Termo de quitação e sub-rogação"]}` — sem dados extraídos. Token inválido 404, expirado 410 |
| POST | `/api/public/upload/{token}` | multipart `files` (vários PDFs, até 50 MB cada) → `{accepted, duplicates, rejected, status}`. Só PDFs (magic `%PDF`); nenhum aceito e todos rejeitados → 415; arquivo > 50 MB → 413 |

## Saúde

`GET /api/health` → `{"ok": true, "llm_provider": "openai"|"anthropic"|"heuristic", "llm_ready": true}`

## Provedor de IA (variáveis de ambiente)

- `SUBROGA_LLM_PROVIDER` = `openai` | `anthropic` | `heuristic`. Sem valor: `openai` se houver `OPENAI_API_KEY`, senão `anthropic` se houver `ANTHROPIC_API_KEY`, senão `heuristic`.
- `heuristic` = classificação por palavras-chave no texto do PDF e extração por regex; sem custo e sem rede. Serve para demo offline e testes.
- `backend/.env` é carregado na inicialização se existir (nunca versionado).
