from datetime import timedelta
from pathlib import Path

import pytest

from _api_helpers import Env, make_env
from _api_helpers import SECRET
from app import auth
from app.config import load_dotenv


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return make_env(tmp_path)


def test_signup_login_me(env: Env):
    c = env.client
    data = env.signup(email="Ana@Escritorio.com")
    assert data["user"]["role"] == "admin" and data["user"]["email"] == "ana@escritorio.com"
    assert data["org"]["name"] == "Escritório A" and data["access_token"]

    r = c.post("/api/auth/login", json={"email": "ANA@escritorio.com ", "password": "segredo123"})
    assert r.status_code == 200
    token = r.json()["access_token"]
    me = c.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["user"]["id"] == data["user"]["id"] and me["org"]["id"] == data["org"]["id"]
    payload = auth.decode_token(token, SECRET)
    assert payload["sub"] == data["user"]["id"] and payload["org"] == data["org"]["id"]
    assert payload["exp"] - payload["iat"] == 12 * 3600


def test_wrong_password_and_unknown_email(env: Env):
    env.signup()
    for body in ({"email": "ana@escritorio.com", "password": "errada123"},
                 {"email": "ninguem@x.com", "password": "segredo123"}):
        r = env.client.post("/api/auth/login", json=body)
        assert r.status_code == 401 and r.json()["detail"] == "Email ou senha incorretos."


def test_duplicate_email_and_short_password(env: Env):
    env.signup()
    r = env.client.post("/api/auth/signup", json={"org_name": "B", "name": "X", "email": "ANA@escritorio.com",
                                                  "password": "segredo123"})
    assert r.status_code == 409
    r = env.client.post("/api/auth/signup", json={"org_name": "B", "name": "X", "email": "b@b.com",
                                                  "password": "curta"})
    assert r.status_code == 422 and "8 caracteres" in r.json()["detail"]
    r = env.client.post("/api/auth/signup", json={"org_name": "B", "name": "X", "email": "b@b.com"})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)


def test_missing_invalid_expired_token(env: Env):
    data = env.signup()
    c = env.client
    assert c.get("/api/auth/me").status_code == 401
    assert c.get("/api/cases").status_code == 401
    assert c.get("/api/auth/me", headers={"Authorization": "Bearer lixo"}).status_code == 401
    expired = auth.create_token(data["user"]["id"], data["org"]["id"], SECRET, ttl=timedelta(seconds=-5))
    r = c.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401 and "expirada" in r.json()["detail"]
    forged = auth.create_token(data["user"]["id"], data["org"]["id"], "outro-segredo-tambem-com-32-bytes-ou-mais")
    assert c.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    wrong_org = auth.create_token(data["user"]["id"], "outra-org", SECRET)
    assert c.get("/api/auth/me", headers={"Authorization": f"Bearer {wrong_org}"}).status_code == 401


def test_team_admin_vs_advogado(env: Env):
    c = env.client
    admin = env.signup()
    ha = {"Authorization": f"Bearer {admin['access_token']}"}
    r = c.post("/api/org/users", headers=ha, json={"name": "Beto", "email": "beto@escritorio.com",
                                                   "password": "senhaforte", "role": "advogado"})
    assert r.status_code == 201 and r.json()["role"] == "advogado"
    beto_id = r.json()["id"]
    assert c.post("/api/org/users", headers=ha, json={"name": "X", "email": "beto@escritorio.com",
                                                      "password": "senhaforte"}).status_code == 409
    assert c.post("/api/org/users", headers=ha, json={"name": "X", "email": "x@escritorio.com",
                                                      "password": "senhaforte", "role": "chefe"}).status_code == 422
    assert [u["email"] for u in c.get("/api/org/users", headers=ha).json()] == [
        "ana@escritorio.com", "beto@escritorio.com"]

    login = c.post("/api/auth/login", json={"email": "beto@escritorio.com", "password": "senhaforte"}).json()
    hb = {"Authorization": f"Bearer {login['access_token']}"}
    assert login["org"]["id"] == admin["org"]["id"]
    assert c.get("/api/org/users", headers=hb).status_code == 403
    assert c.post("/api/org/users", headers=hb, json={"name": "Y", "email": "y@y.com",
                                                      "password": "senhaforte"}).status_code == 403
    assert c.delete(f"/api/org/users/{admin['user']['id']}", headers=hb).status_code == 403
    # advogado opera casos normalmente
    assert c.post("/api/cases", headers=hb, json={"scenario": "AUTO", "name": "x"}).status_code == 201

    assert c.delete(f"/api/org/users/{admin['user']['id']}", headers=ha).status_code == 409  # a si mesmo
    other = env.signup(email="outra@b.com", org="Escritório B")
    assert c.delete(f"/api/org/users/{other['user']['id']}", headers=ha).status_code == 404
    assert c.delete(f"/api/org/users/{beto_id}", headers=ha).status_code == 204
    assert c.get("/api/auth/me", headers=hb).status_code == 401  # usuário removido


def test_load_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    env_file = tmp_path / ".env"
    env_file.write_text("# comentário\nSUBROGA_X_NOVA='valor 1'\nSUBROGA_X_EXISTE=do_arquivo\n\nlixo\n")
    monkeypatch.setenv("SUBROGA_X_EXISTE", "do_ambiente")
    monkeypatch.delenv("SUBROGA_X_NOVA", raising=False)
    load_dotenv(env_file)
    import os

    assert os.environ["SUBROGA_X_NOVA"] == "valor 1"
    assert os.environ["SUBROGA_X_EXISTE"] == "do_ambiente"
    monkeypatch.delenv("SUBROGA_X_NOVA")
