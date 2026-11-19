"""Popula um servidor Sub-Roga com dados de demonstração (fictícios), via API.

Uso:  uv run python scripts/seed.py [--base http://localhost:8000]

Cria o escritório "Prado & Rocha Advogados" com:
  ana@prado.adv.br   / senha1234  (admin)
  bruno@prado.adv.br / senha1234  (advogado)
e quatro casos: auto, elétrico e transporte (com os PDFs de tests/fixtures/)
e um caso novo aguardando envio do cliente. Idempotente: se a conta já existe,
só faz login e não duplica casos com o mesmo nome.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
ADMIN = {"org_name": "Prado & Rocha Advogados", "name": "Ana Prado",
         "email": "ana@prado.adv.br", "password": "senha1234"}
LAWYER = {"name": "Bruno Rocha", "email": "bruno@prado.adv.br", "password": "senha1234", "role": "advogado"}
CASES = [
    ("Aurora Seguros × Bruno C. Albuquerque (colisão)", "AUTO", "auto"),
    ("Danos elétricos — condomínio × concessionária", "ELETRICO", "eletrico"),
    ("Avaria de carga — transportadora", "TRANSPORTE", "transporte"),
    ("Novo sinistro — aguardando documentos", "AUTO", None),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    args = ap.parse_args()
    c = httpx.Client(base_url=args.base, timeout=120)

    r = c.post("/api/auth/signup", json=ADMIN)
    if r.status_code == 409:
        r = c.post("/api/auth/login", json={"email": ADMIN["email"], "password": ADMIN["password"]})
    r.raise_for_status()
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    print(f"conta: {ADMIN['email']} / {ADMIN['password']} (admin)")

    users = {u["email"] for u in c.get("/api/org/users", headers=h).json()}
    if LAWYER["email"] not in users:
        c.post("/api/org/users", json=LAWYER, headers=h).raise_for_status()
    print(f"conta: {LAWYER['email']} / {LAWYER['password']} (advogado)")

    existing = {x["name"] for x in c.get("/api/cases", headers=h).json()}
    pending: list[str] = []
    for name, scenario, folder in CASES:
        if name in existing:
            print(f"caso já existe: {name}")
            continue
        case = c.post("/api/cases", json={"scenario": scenario, "name": name}, headers=h).json()
        if folder:
            files = [("files", (p.name, p.read_bytes(), "application/pdf"))
                     for p in sorted((FIXTURES / folder).glob("*.pdf"))]
            c.post(f"/api/public/upload/{case['upload_token']}", files=files).raise_for_status()
            pending.append(case["id"])
        print(f"caso criado: {name} ({scenario})")

    t0 = time.time()
    while pending and time.time() - t0 < 600:
        time.sleep(3)
        for cid in list(pending):
            d = c.get(f"/api/cases/{cid}", headers=h).json()
            if d["status"] not in ("RECEBENDO", "PROCESSANDO"):
                pending.remove(cid)
                print(f"  processado: {d['name']} → {d['status']}, {len(d['documents'])} docs"
                      + (f", erro: {d['error']}" if d.get("error") else ""))
    if pending:
        print("aviso: alguns casos ainda estão processando", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
