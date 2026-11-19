"""Mock da API do Sub-Roga para desenvolver o frontend sem o backend real.

Uso:  python3 frontend/dev/mock_server.py   ->  http://localhost:8077
Login de exemplo: ana@prado.adv.br / senha1234 (admin)

Segue docs/API.md. Dados em memória; reiniciar apaga tudo.
"""
from __future__ import annotations

import base64
import copy
import datetime as dt
import io
import json
import re
import secrets
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # frontend/
PORT = 8077

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
PDF_MIN = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
DOCX_FAKE = b"PK\x05\x06" + b"\x00" * 18  # zip vazio (abre como .docx inválido, suficiente p/ download)

LABELS = {
    "PROCURACAO": "Procuração", "DOCS_SEGURADORA": "Documentos da seguradora", "APOLICE": "Apólice",
    "AVISO_SINISTRO": "Aviso de sinistro", "BOLETIM_OCORRENCIA": "Boletim de ocorrência",
    "LAUDO_TECNICO": "Laudo técnico", "ORCAMENTO_NF_REPARO": "Orçamentos e notas fiscais",
    "FOTOS": "Registro fotográfico", "COMPROVANTE_PAGAMENTO": "Comprovante de pagamento da indenização",
    "TERMO_QUITACAO_SUBROGACAO": "Termo de quitação e sub-rogação", "DOCS_TRANSPORTE": "Documentos de transporte",
    "DOCS_VEICULO_CONDUTOR": "Documentos do veículo e do condutor",
    "RECLAMACAO_ADMINISTRATIVA": "Reclamação administrativa", "OUTROS": "Outros documentos",
}
ORDER = list(LABELS)
COMMON = ["PROCURACAO", "APOLICE", "AVISO_SINISTRO", "COMPROVANTE_PAGAMENTO", "TERMO_QUITACAO_SUBROGACAO"]
REQUIRED = {
    "AUTO": COMMON + ["BOLETIM_OCORRENCIA", "ORCAMENTO_NF_REPARO"],
    "ELETRICO": COMMON + ["LAUDO_TECNICO", "ORCAMENTO_NF_REPARO"],
    "TRANSPORTE": COMMON + ["DOCS_TRANSPORTE"],
}
FACT_FIELDS = [
    "seguradora_nome", "seguradora_cnpj", "segurado_nome", "segurado_cpf_cnpj", "apolice_numero",
    "apolice_vigencia_inicio", "apolice_vigencia_fim", "apolice_ramo", "sinistro_numero", "sinistro_data",
    "sinistro_local", "sinistro_descricao", "causador_nome", "causador_cpf_cnpj", "causador_endereco",
    "causador_placa", "valor_pago", "data_pagamento", "franquia",
]

LOCK = threading.Lock()
ORGS: dict[int, dict] = {}
USERS: dict[int, dict] = {}
TOKENS: dict[str, int] = {}  # jwt fake -> user id
CASES: dict[str, dict] = {}
SEQ = {"org": 0, "user": 0, "file": 0}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def add_years(d: dt.date, n: int) -> dt.date:
    try:
        return d.replace(year=d.year + n)
    except ValueError:
        return d.replace(year=d.year + n, day=28)


def new_org(name: str) -> dict:
    SEQ["org"] += 1
    org = {"id": SEQ["org"], "name": name}
    ORGS[org["id"]] = org
    return org


def new_user(org_id: int, name: str, email: str, password: str, role: str) -> dict:
    SEQ["user"] += 1
    u = {"id": SEQ["user"], "name": name, "email": email.lower(), "role": role, "_pw": password, "_org": org_id}
    USERS[u["id"]] = u
    return u


def pub_user(u: dict) -> dict:
    return {k: v for k, v in u.items() if not k.startswith("_")}


def issue(u: dict) -> str:
    tok = "mock." + secrets.token_urlsafe(18)
    TOKENS[tok] = u["id"]
    return tok


def ff(value, doc=None, page=None):
    return {"value": value, "source_doc_id": doc, "page": page}


def new_case(org_id: int, name: str, scenario: str) -> dict:
    cid = secrets.token_hex(3)
    c = {
        "id": cid, "_org": org_id, "name": name, "scenario": scenario, "status": "RECEBENDO",
        "created_at": now_iso(), "upload_token": secrets.token_urlsafe(16),
        "token_expires_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "error": None, "files": [], "documents": [], "facts": None, "edited_fields": [], "_approved": False,
        "_proc_until": 0.0,
    }
    CASES[cid] = c
    return c


def seed() -> None:
    org = new_org("Prado & Rocha Advogados")
    new_user(org["id"], "Ana Prado", "ana@prado.adv.br", "senha1234", "admin")
    new_user(org["id"], "Bruno Rocha", "bruno@prado.adv.br", "senha1234", "advogado")
    other = new_org("Outro Escritório")
    new_user(other["id"], "Carla", "carla@outro.adv.br", "senha1234", "admin")

    c = new_case(org["id"], "Atlântica × Rodaviva", "AUTO")
    files = [("scan001.pdf", 6), ("apolice.pdf", 6), ("procuracao_assinada.pdf", 1), ("doc final (2).pdf", 4),
             ("fotos_carro.pdf", 8), ("comprovante TED.pdf", 1)]
    for fn, _ in files:
        SEQ["file"] += 1
        c["files"].append({"id": SEQ["file"], "filename": fn, "sha256": secrets.token_hex(32), "processed": True, "duplicate_pages": 0})
    docs = [
        ("d1", "PROCURACAO", "Procuração ad judicia", "procuracao_assinada.pdf", [0], .98, False),
        ("d2", "APOLICE", "Apólice 1031.22.004871 + condições gerais", "apolice.pdf", [0, 1, 2, 3, 4, 5], .97, False),
        ("d3", "AVISO_SINISTRO", "Aviso de sinistro nº 2025-0418", "scan001.pdf", [0, 1], .93, False),
        ("d4", "BOLETIM_OCORRENCIA", "B.O. 4471/2025, 3º DP de Campinas", "scan001.pdf", [2, 3, 4], .91, False),
        ("d5", "ORCAMENTO_NF_REPARO", "Orçamento Oficina Centro + NF 8812", "doc final (2).pdf", [0, 1, 2, 3], .88, False),
        ("d6", "FOTOS", "Registro fotográfico do veículo", "fotos_carro.pdf", list(range(8)), .95, False),
        ("d7", "COMPROVANTE_PAGAMENTO", "TED da indenização", "comprovante TED.pdf", [0], .96, False),
        ("d8", "OUTROS", "Recibo com texto de quitação (ilegível em parte)", "scan001.pdf", [5], .41, True),
    ]
    c["documents"] = [
        {"id": i, "doc_type": t, "title": ti, "source_file": f"storage/{c['id']}/{fn}", "pages": p, "confidence": conf, "needs_review": nr}
        for i, t, ti, fn, p, conf, nr in docs
    ]
    pay = (dt.date.today() - dt.timedelta(days=3 * 365 - 46)).isoformat()
    facts = {k: ff(None) for k in FACT_FIELDS}
    facts.update({
        "scenario": "AUTO",
        "seguradora_nome": ff("Atlântica Seguros S.A.", "d1", 0),
        "seguradora_cnpj": ff("11.222.333/0001-44", "d1", 0),
        "segurado_nome": ff("Marcos Vieira Lopes", "d2", 0),
        "apolice_numero": ff("1031.22.004871", "d2", 0),
        "sinistro_numero": ff("2025-0418", "d3", 0),
        "sinistro_data": ff("2023-11-28", "d4", 2),
        "sinistro_local": ff("Av. Norte-Sul, 1200, Campinas/SP", "d4", 2),
        "sinistro_descricao": ff("Colisão traseira em cruzamento semaforizado.", "d4", 3),
        "causador_nome": ff("Transportes Rodaviva Ltda.", "d4", 3),
        "causador_cpf_cnpj": ff("00.000.000/0001-00", "d4", 3),
        "causador_placa": ff("ABC1D23", "d4", 3),
        "valor_pago": ff("48320.00", "d7", 0),
        "data_pagamento": ff(pay, "d7", 0),
    })
    facts["evidencias_culpa"] = [{"fato": "B.O. registra colisão traseira", "doc_id": "d4"}]
    facts["valores_alternativos"] = {"valor_pago": [ff("48230.00", "d5", 3)]}
    c["facts"] = facts
    c["status"] = "PENDENTE"

    c2 = new_case(org["id"], "Mareal × Expresso Serra Azul", "TRANSPORTE")
    c2["status"] = "RECEBENDO"
    c3 = new_case(other["id"], "Caso de outro escritório", "ELETRICO")
    c3["status"] = "RECEBENDO"


# ---------- regras derivadas ----------

def missing(c: dict) -> list[dict]:
    have = {d["doc_type"] for d in c["documents"]}
    if c["status"] in ("RECEBENDO", "PROCESSANDO") and not c["documents"]:
        return [{"doc_type": t, "label": LABELS[t]} for t in REQUIRED[c["scenario"]]]
    return [{"doc_type": t, "label": LABELS[t]} for t in REQUIRED[c["scenario"]] if t not in have]


def prescription(c: dict) -> dict | None:
    f = c.get("facts") or {}
    v = (f.get("data_pagamento") or {}).get("value")
    if not v:
        return None
    try:
        d = dt.date.fromisoformat(v)
    except ValueError:
        return None
    lim = add_years(d, 3)
    left = (lim - dt.date.today()).days
    sev = "critical" if left < 90 else "warn" if left < 365 else "info"
    msg = "Prescrição vencida." if left < 0 else f"Prescrição em {left} dias (3 anos do pagamento, art. 206 §3º V CC)."
    return {"kind": "PRESCRIPTION", "severity": sev, "message": msg, "deadline": lim.isoformat(), "days_left": left}


def alerts(c: dict) -> list[dict]:
    out = []
    p = prescription(c)
    if p:
        out.append({"kind": "PRESCRIPTION", "severity": p["severity"], "message": p["message"], "field": "data_pagamento"})
    for m in missing(c):
        sev = "critical" if m["doc_type"] in ("TERMO_QUITACAO_SUBROGACAO", "COMPROVANTE_PAGAMENTO") else "warn"
        out.append({"kind": "MISSING_DOC", "severity": sev, "message": f"Falta: {m['label']}.", "field": None})
    f = c.get("facts") or {}
    for field, alts in (f.get("valores_alternativos") or {}).items():
        cur = (f.get(field) or {}).get("value")
        vals = ", ".join(a["value"] for a in alts if a.get("value"))
        out.append({"kind": "CONFLICT", "severity": "warn", "field": field,
                    "message": f"Valores divergentes em {field}: {cur} (usado) × {vals} em outro documento."})
    for d in c["documents"]:
        if d["needs_review"]:
            out.append({"kind": "LOW_CONFIDENCE", "severity": "warn", "field": None,
                        "message": f"Classificação a revisar: \"{d['title']}\" ({LABELS[d['doc_type']]}, confiança {round(d['confidence'] * 100)}%)."})
    return out


def recompute_status(c: dict) -> None:
    if c["status"] in ("RECEBENDO", "PROCESSANDO"):
        return
    if c["_approved"]:
        c["status"] = "PRONTO"
        return
    al = alerts(c)
    if missing(c):
        c["status"] = "PENDENTE"
    elif any(a["kind"] in ("CONFLICT", "LOW_CONFIDENCE") for a in al):
        c["status"] = "REVISAR"
    else:
        c["status"] = "REVISAR"


def tick(c: dict) -> None:
    """Simula fim do processamento depois de alguns segundos."""
    if c["status"] == "PROCESSANDO" and time.time() >= c["_proc_until"]:
        for f in c["files"]:
            f["processed"] = True
        for f in c["files"]:
            if not any(d["source_file"].endswith("/" + f["filename"]) for d in c["documents"]):
                n = len(c["documents"]) + 1
                c["documents"].append({"id": f"n{f['id']}", "doc_type": "OUTROS", "title": f["filename"].rsplit(".", 1)[0],
                                       "source_file": f"storage/{c['id']}/{f['filename']}", "pages": [0, 1],
                                       "confidence": .5, "needs_review": True})
        if c["facts"] is None:
            c["facts"] = {k: ff(None) for k in FACT_FIELDS} | {"scenario": c["scenario"], "evidencias_culpa": [], "valores_alternativos": {}}
        c["status"] = "PENDENTE"
        recompute_status(c)


def summary(c: dict) -> dict:
    tick(c)
    return {
        "id": c["id"], "name": c["name"], "scenario": c["scenario"], "status": c["status"], "created_at": c["created_at"],
        "upload_token": c["upload_token"], "upload_url": f"/enviar/{c['upload_token']}", "token_expires_at": c["token_expires_at"],
        "n_files": len(c["files"]), "n_docs": len(c["documents"]), "prescription": prescription(c),
        "critical_alerts": sum(1 for a in alerts(c) if a["severity"] == "critical"), "missing_count": len(missing(c)),
    }


def detail(c: dict) -> dict:
    s = summary(c)
    docs = sorted(c["documents"], key=lambda d: (ORDER.index(d["doc_type"]), d["id"]))
    out_docs = [dict(d, label=LABELS[d["doc_type"]], number=i + 1, page_count=len(d["pages"])) for i, d in enumerate(docs)]
    s.update({"error": c["error"], "files": copy.deepcopy(c["files"]), "documents": out_docs, "facts": copy.deepcopy(c["facts"]),
              "edited_fields": list(c["edited_fields"]), "missing": missing(c), "alerts": alerts(c)})
    return s


# ---------- HTTP ----------

class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        self.status, self.detail = status, detail


class Handler(BaseHTTPRequestHandler):
    server_version = "SubRogaMock/1"

    def log_message(self, fmt, *args):  # menos barulho
        print("[mock]", self.command, self.path, "->", args[1] if len(args) > 1 else "")

    # helpers
    def send_json(self, status: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, status: int, data: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def body_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except json.JSONDecodeError:
            raise ApiError(422, "JSON inválido.")

    def auth(self) -> dict:
        h = self.headers.get("Authorization", "")
        tok = h[7:] if h.startswith("Bearer ") else ""
        uid = TOKENS.get(tok)
        if not uid or uid not in USERS:
            raise ApiError(401, "Sessão expirada. Entre novamente.")
        return USERS[uid]

    def get_case(self, user: dict, cid: str) -> dict:
        c = CASES.get(cid)
        if not c or c["_org"] != user["_org"]:
            raise ApiError(404, "Caso não encontrado.")
        return c

    # static
    def serve_static(self, path: str) -> None:
        if path.startswith("/assets/"):
            fp = (ROOT / path.lstrip("/")).resolve()
            if ROOT not in fp.parents or not fp.is_file():
                self.send_bytes(404, b"not found", "text/plain")
                return
            ctype = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png"}.get(fp.suffix, "application/octet-stream")
            self.send_bytes(200, fp.read_bytes(), ctype + ("; charset=utf-8" if fp.suffix in (".js", ".css") else ""),
                            {"Cache-Control": "no-store"})
            return
        self.send_bytes(200, (ROOT / "index.html").read_bytes(), "text/html; charset=utf-8", {"Cache-Control": "no-store"})

    def handle_any(self, method: str) -> None:
        path, _, query = self.path.partition("?")
        qs = dict(p.split("=", 1) if "=" in p else (p, "") for p in query.split("&") if p)
        if not path.startswith("/api/"):
            if method == "GET":
                self.serve_static(path)
            else:
                self.send_bytes(405, b"", "text/plain")
            return
        time.sleep(0.15)  # latência fake
        try:
            with LOCK:
                self.route(method, path, qs)
        except ApiError as e:
            self.send_json(e.status, {"detail": e.detail})

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")

    def do_PATCH(self):
        self.handle_any("PATCH")

    def do_DELETE(self):
        self.handle_any("DELETE")

    def route(self, m: str, p: str, qs: dict) -> None:
        if p == "/api/health" and m == "GET":
            return self.send_json(200, {"ok": True, "llm_provider": "heuristic", "llm_ready": True})

        if p == "/api/auth/signup" and m == "POST":
            b = self.body_json()
            if not all(b.get(k) for k in ("org_name", "name", "email", "password")):
                raise ApiError(422, "Preencha todos os campos.")
            if len(b["password"]) < 8:
                raise ApiError(422, "A senha precisa ter pelo menos 8 caracteres.")
            if any(u["email"] == b["email"].lower() for u in USERS.values()):
                raise ApiError(409, "Este email já está em uso.")
            org = new_org(b["org_name"])
            u = new_user(org["id"], b["name"], b["email"], b["password"], "admin")
            return self.send_json(201, {"access_token": issue(u), "user": pub_user(u), "org": org})
        if p == "/api/auth/login" and m == "POST":
            b = self.body_json()
            u = next((u for u in USERS.values() if u["email"] == str(b.get("email", "")).lower()), None)
            if not u or u["_pw"] != b.get("password"):
                raise ApiError(401, "Email ou senha incorretos.")
            return self.send_json(200, {"access_token": issue(u), "user": pub_user(u), "org": ORGS[u["_org"]]})
        if p == "/api/auth/me" and m == "GET":
            u = self.auth()
            return self.send_json(200, {"user": pub_user(u), "org": ORGS[u["_org"]]})

        mt = re.fullmatch(r"/api/org/users(?:/(\d+))?", p)
        if mt:
            u = self.auth()
            if u["role"] != "admin":
                raise ApiError(403, "Somente administradores gerenciam a equipe.")
            uid = mt.group(1)
            if not uid and m == "GET":
                return self.send_json(200, [pub_user(x) for x in USERS.values() if x["_org"] == u["_org"]])
            if not uid and m == "POST":
                b = self.body_json()
                if not all(b.get(k) for k in ("name", "email", "password", "role")):
                    raise ApiError(422, "Preencha todos os campos.")
                if len(b["password"]) < 8:
                    raise ApiError(422, "A senha precisa ter pelo menos 8 caracteres.")
                if b["role"] not in ("admin", "advogado"):
                    raise ApiError(422, "Papel inválido.")
                if any(x["email"] == b["email"].lower() for x in USERS.values()):
                    raise ApiError(409, "Este email já está em uso.")
                return self.send_json(201, pub_user(new_user(u["_org"], b["name"], b["email"], b["password"], b["role"])))
            if uid and m == "DELETE":
                t = USERS.get(int(uid))
                if not t or t["_org"] != u["_org"]:
                    raise ApiError(404, "Usuário não encontrado.")
                if t["id"] == u["id"]:
                    raise ApiError(409, "Você não pode remover a si mesmo.")
                del USERS[t["id"]]
                return self.send_bytes(204, b"", "text/plain")

        if p == "/api/cases":
            u = self.auth()
            if m == "GET":
                lst = [summary(c) for c in CASES.values() if c["_org"] == u["_org"]]
                lst.sort(key=lambda s: (s["prescription"] is None, (s["prescription"] or {}).get("days_left", 0)))
                return self.send_json(200, lst)
            if m == "POST":
                b = self.body_json()
                if b.get("scenario") not in REQUIRED:
                    raise ApiError(422, "Cenário inválido.")
                if not str(b.get("name", "")).strip():
                    raise ApiError(422, "Informe o nome do caso.")
                return self.send_json(201, summary(new_case(u["_org"], b["name"].strip(), b["scenario"])))

        mt = re.fullmatch(r"/api/cases/([^/]+)(/.*)?", p)
        if mt:
            u = self.auth()
            c = self.get_case(u, mt.group(1))
            sub = mt.group(2) or ""
            if sub == "" and m == "GET":
                return self.send_json(200, detail(c))
            if sub == "" and m == "DELETE":
                del CASES[c["id"]]
                return self.send_bytes(204, b"", "text/plain")
            if sub == "/token" and m == "POST":
                c["upload_token"] = secrets.token_urlsafe(16)
                return self.send_json(200, summary(c))
            md = re.fullmatch(r"/documents/([^/]+)", sub)
            if md and m == "PATCH":
                d = next((d for d in c["documents"] if d["id"] == md.group(1)), None)
                if not d:
                    raise ApiError(404, "Documento não encontrado.")
                b = self.body_json()
                if "doc_type" in b:
                    if b["doc_type"] not in LABELS:
                        raise ApiError(422, "Tipo inválido.")
                    d["doc_type"] = b["doc_type"]
                if b.get("title"):
                    d["title"] = b["title"]
                d["needs_review"] = False
                recompute_status(c)
                return self.send_json(200, detail(c))
            mp = re.fullmatch(r"/documents/([^/]+)/pages/(\d+)\.png", sub)
            if mp and m == "GET":
                if not any(d["id"] == mp.group(1) for d in c["documents"]):
                    raise ApiError(404, "Documento não encontrado.")
                return self.send_bytes(200, PNG_1X1, "image/png")
            if sub == "/facts" and m == "PATCH":
                if c["facts"] is None:
                    raise ApiError(409, "Caso ainda sem dados extraídos.")
                b = self.body_json()
                for k, v in b.items():
                    if k not in FACT_FIELDS:
                        raise ApiError(422, f"Campo desconhecido: {k}.")
                    if isinstance(v, dict):
                        c["facts"][k] = ff(v.get("value"), v.get("source_doc_id"), v.get("page"))
                    else:
                        c["facts"][k]["value"] = v if v != "" else None
                    if k not in c["edited_fields"]:
                        c["edited_fields"].append(k)
                    c["facts"]["valores_alternativos"].pop(k, None)
                recompute_status(c)
                return self.send_json(200, detail(c))
            if sub == "/approve" and m == "POST":
                b = self.body_json()
                if missing(c) and not b.get("force"):
                    raise ApiError(409, f"Faltam {len(missing(c))} documento(s) obrigatório(s). Confirme para aprovar mesmo assim.")
                c["_approved"] = True
                c["status"] = "PRONTO"
                return self.send_json(200, detail(c))
            if sub == "/reopen" and m == "POST":
                c["_approved"] = False
                c["status"] = "REVISAR"
                recompute_status(c)
                return self.send_json(200, detail(c))
            if sub == "/dossie" and m == "POST":
                if qs.get("max_mb"):
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, "w") as z:
                        z.writestr("dossie_parte1.pdf", PDF_MIN)
                        z.writestr("dossie_parte2.pdf", PDF_MIN)
                    return self.send_bytes(200, buf.getvalue(), "application/zip",
                                           {"Content-Disposition": f'attachment; filename="dossie_{c["id"]}.zip"'})
                return self.send_bytes(200, PDF_MIN, "application/pdf",
                                       {"Content-Disposition": f'attachment; filename="dossie_{c["id"]}.pdf"'})
            if sub == "/peticao" and m == "POST":
                extra = {"Content-Disposition": f'attachment; filename="peticao_{c["id"]}.docx"'}
                if qs.get("narrative") == "true":
                    extra["X-Narrative"] = "fallback"
                return self.send_bytes(200, DOCX_FAKE,
                                       "application/vnd.openxmlformats-officedocument.wordprocessingml.document", extra)

        mt = re.fullmatch(r"/api/public/upload/([^/]+)", p)
        if mt:
            tok = mt.group(1)
            if tok == "expirado":
                raise ApiError(410, "Este link expirou. Peça um novo ao escritório.")
            c = next((c for c in CASES.values() if c["upload_token"] == tok), None)
            if not c:
                raise ApiError(404, "Link inválido.")
            tick(c)
            if m == "GET":
                return self.send_json(200, {
                    "org_name": ORGS[c["_org"]]["name"], "case_name": c["name"], "scenario": c["scenario"], "status": c["status"],
                    "files": [{"filename": f["filename"], "processed": f["processed"]} for f in c["files"]],
                    "missing": [x["label"] for x in missing(c)],
                })
            if m == "POST":
                return self.upload(c)

        raise ApiError(404, "Rota não encontrada.")

    def upload(self, c: dict) -> None:
        ctype = self.headers.get("Content-Type", "")
        mb = re.search(r"boundary=(.+)", ctype)
        n = int(self.headers.get("Content-Length") or 0)
        if n > 50 * 1024 * 1024:
            raise ApiError(413, "Arquivo maior que 50 MB.")
        raw = self.rfile.read(n)
        if not mb:
            raise ApiError(422, "Envie multipart/form-data.")
        boundary = mb.group(1).strip('"').encode()
        accepted, dups, rejected = [], [], []
        for part in raw.split(b"--" + boundary):
            head, _, data = part.partition(b"\r\n\r\n")
            fn = re.search(rb'filename="([^"]*)"', head)
            if not fn:
                continue
            name = fn.group(1).decode("utf-8", "replace")
            data = data[:-2] if data.endswith(b"\r\n") else data
            if not data.startswith(b"%PDF"):
                rejected.append(name)
                continue
            if any(f["filename"] == name for f in c["files"]):
                dups.append(name)
                continue
            SEQ["file"] += 1
            c["files"].append({"id": SEQ["file"], "filename": name, "sha256": "x", "processed": False, "duplicate_pages": 0})
            accepted.append(name)
        if not accepted and not dups and rejected:
            raise ApiError(415, "Só aceitamos arquivos PDF.")
        if accepted:
            c["status"] = "PROCESSANDO"
            c["_approved"] = False
            c["_proc_until"] = time.time() + 6
        self.send_json(200, {"accepted": accepted, "duplicates": dups, "rejected": rejected, "status": c["status"]})


if __name__ == "__main__":
    seed()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    tok = next(c["upload_token"] for c in CASES.values() if c["status"] == "RECEBENDO")
    print(f"Mock em http://localhost:{PORT}  ·  login ana@prado.adv.br / senha1234")
    print(f"Link de envio de exemplo: http://localhost:{PORT}/enviar/{tok}  ·  expirado: /enviar/expirado")
    srv.serve_forever()
