"""Isolamento entre organizações: caso de outra org responde 404 em todas as rotas."""
from pathlib import Path

from _api_helpers import make_env, make_pdf, new_case


def test_cases_are_isolated_between_orgs(tmp_path: Path):
    env = make_env(tmp_path)
    c = env.client
    ha = env.headers("a@a.com", "Escritório A")
    hb = env.headers("b@b.com", "Escritório B")

    case_id, token = new_case(env, ha)
    c.post(f"/api/public/upload/{token}", files=[("files", ("misto.pdf", make_pdf(["a", "b", "c"]),
                                                            "application/pdf"))])
    doc_id = c.get(f"/api/cases/{case_id}", headers=ha).json()["documents"][0]["id"]

    assert c.get("/api/cases", headers=hb).json() == []
    assert [x["id"] for x in c.get("/api/cases", headers=ha).json()] == [case_id]

    routes = [
        ("get", f"/api/cases/{case_id}", None),
        ("post", f"/api/cases/{case_id}/token", None),
        ("patch", f"/api/cases/{case_id}/documents/{doc_id}", {"title": "x"}),
        ("get", f"/api/cases/{case_id}/documents/{doc_id}/pages/0.png", None),
        ("patch", f"/api/cases/{case_id}/facts", {"valor_pago": "1.00"}),
        ("post", f"/api/cases/{case_id}/approve", {"force": True}),
        ("post", f"/api/cases/{case_id}/reopen", None),
        ("post", f"/api/cases/{case_id}/dossie", None),
        ("post", f"/api/cases/{case_id}/peticao", None),
        ("delete", f"/api/cases/{case_id}", None),
    ]
    for method, url, body in routes:
        kwargs = {"headers": hb} | ({"json": body} if body is not None else {})
        r = getattr(c, method)(url, **kwargs)
        assert r.status_code == 404, (method, url, r.status_code)
        assert r.json()["detail"] == "Caso não encontrado."

    # nada mudou para a org A
    detail = c.get(f"/api/cases/{case_id}", headers=ha).json()
    assert detail["upload_token"] == token and detail["facts"]["valor_pago"]["value"] == "48320.00"
    assert all(d["title"] != "x" for d in detail["documents"])
    assert c.get(f"/api/cases/{case_id}/documents/{doc_id}/pages/0.png", headers=ha).status_code == 200


def test_storage_is_per_org(tmp_path: Path):
    env = make_env(tmp_path)
    ha = env.headers("a@a.com", "Escritório A")
    hb = env.headers("b@b.com", "Escritório B")
    for h in (ha, hb):
        _, token = new_case(env, h)
        env.client.post(f"/api/public/upload/{token}",
                        files=[("files", ("bo.pdf", make_pdf(["x"]), "application/pdf"))])
    orgs = sorted(p.name for p in (tmp_path / "storage" / "orgs").iterdir())
    me = sorted(env.client.get("/api/auth/me", headers=h).json()["org"]["id"] for h in (ha, hb))
    assert orgs == me
