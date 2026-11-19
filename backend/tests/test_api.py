import io
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest
from sqlmodel import select

from _api_helpers import (
    CLASSIFY_CALLS, PDF, Env, fake_dossie, fake_extract, fake_petition, fake_segment, fake_steps, make_env,
    make_pdf, new_case, upload,
)
from app import db
from app.models import Case
from app.pipeline.assemble_pdf import build_dossie
from app.pipeline.run import Steps, case_lock, process_case


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return make_env(tmp_path)


@pytest.fixture
def h(env: Env) -> dict[str, str]:
    return env.headers()


def test_health(env: Env):
    r = env.client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert {"llm_provider", "llm_ready"} <= set(r.json())


def test_full_flow_pending_then_review(env: Env, h):
    client = env.client
    case_id, token = new_case(env, h)

    r = upload(env, token, "misto.pdf", "pagto.pdf", "bo.pdf")
    assert r.status_code == 200 and len(r.json()["accepted"]) == 3

    st = client.get(f"/api/public/upload/{token}").json()
    assert st["status"] == "PENDENTE" and st["org_name"] == "Escritório A" and st["case_name"] == "Sinistro X"
    assert "Termo de quitação e sub-rogação" in st["missing"]
    assert "facts" not in st and "documents" not in st
    assert len(st["files"]) == 3

    detail = client.get(f"/api/cases/{case_id}", headers=h).json()
    assert [d["doc_type"] for d in detail["documents"]] == [
        "PROCURACAO", "APOLICE", "AVISO_SINISTRO", "BOLETIM_OCORRENCIA", "COMPROVANTE_PAGAMENTO"]
    assert [d["number"] for d in detail["documents"]] == [1, 2, 3, 4, 5]
    assert all(d["page_count"] == 1 and d["label"] for d in detail["documents"])
    assert len({d["id"] for d in detail["documents"]}) == 5  # ids únicos mesmo com segment repetindo "d0"
    kinds = {(a["kind"], a["severity"]) for a in detail["alerts"]}
    assert ("PRESCRIPTION", "critical") in kinds
    assert ("CONFLICT", "warn") in kinds
    assert ("LOW_CONFIDENCE", "warn") in kinds
    assert ("MISSING_DOC", "critical") in kinds
    assert next(a for a in detail["alerts"] if a["kind"] == "CONFLICT")["field"] == "valor_pago"
    assert detail["prescription"]["deadline"] == "2027-01-20" and detail["prescription"]["days_left"] == 74
    assert detail["upload_url"] == f"/enviar/{token}" and detail["created_at"].endswith("Z")
    assert detail["missing_count"] == len(detail["missing"]) == 2
    assert detail["files"][0]["duplicate_pages"] == 0
    org_id = client.get("/api/auth/me", headers=h).json()["org"]["id"]
    assert (env.tmp / "storage" / "orgs" / org_id / "cases" / case_id / "uploads").is_dir()

    # completa pelo mesmo link: arquivos antigos não são reclassificados
    r = upload(env, token, "termo.pdf", "nf.pdf")
    assert r.json()["accepted"] == ["termo.pdf", "nf.pdf"]
    detail = client.get(f"/api/cases/{case_id}", headers=h).json()
    assert detail["status"] == "REVISAR" and detail["missing"] == []
    assert len(detail["documents"]) == 7

    listing = client.get("/api/cases", headers=h).json()
    assert listing[0]["id"] == case_id and listing[0]["prescription"]["severity"] == "critical"


def test_list_sorted_by_prescription(env: Env, h):
    sem_data, _ = new_case(env, h, name="sem data")
    perto, t1 = new_case(env, h, name="perto")
    upload(env, t1, "bo.pdf")
    ids = [c["id"] for c in env.client.get("/api/cases", headers=h).json()]
    assert ids == [perto, sem_data]


def test_upload_dedup_and_rejects(env: Env, h):
    client = env.client
    _, token = new_case(env, h)
    assert upload(env, token, "bo.pdf").json()["accepted"] == ["bo.pdf"]
    r = upload(env, token, "bo.pdf")
    assert r.json()["duplicates"] == ["bo.pdf"] and r.json()["accepted"] == []
    url = f"/api/public/upload/{token}"
    r = client.post(url, files=[("files", ("x.pdf", b"MZ not a pdf", "application/pdf"))])
    assert r.status_code == 415 and "PDF" in r.json()["detail"]
    r = client.post(url, files=[("files", ("x.pdf", b"MZ", "application/pdf")),
                                ("files", ("nf.pdf", PDF + b"nf", "application/pdf"))])
    assert r.json()["rejected"] == ["x.pdf"] and r.json()["accepted"] == ["nf.pdf"]


def test_upload_too_large(tmp_path: Path):
    env = make_env(tmp_path, max_upload_mb=0.001)  # ~1 KB
    h = env.headers()
    _, token = new_case(env, h)
    r = env.client.post(f"/api/public/upload/{token}",
                        files=[("files", ("bo.pdf", PDF + b"x" * 2000, "application/pdf"))])
    assert r.status_code == 413 and "limite" in r.json()["detail"]
    assert env.client.get(f"/api/public/upload/{token}").json()["files"] == []


def test_invalid_and_expired_token(env: Env, h):
    client = env.client
    assert client.get("/api/public/upload/nao-existe").status_code == 404
    assert upload(env, "nao-existe", "bo.pdf").status_code == 404
    case_id, token = new_case(env, h)
    with db.session_factory(env.engine)() as s:
        case = s.get(Case, case_id)
        case.token_expires_at = case.token_expires_at - timedelta(days=365)
        s.add(case)
        s.commit()
    assert client.get(f"/api/public/upload/{token}").status_code == 410
    assert upload(env, token, "bo.pdf").status_code == 410


def test_regenerate_token(env: Env, h):
    case_id, token = new_case(env, h)
    r = env.client.post(f"/api/cases/{case_id}/token", headers=h)
    new = r.json()["upload_token"]
    assert r.status_code == 200 and new != token
    assert env.client.get(f"/api/public/upload/{token}").status_code == 404
    assert env.client.get(f"/api/public/upload/{new}").status_code == 200


def test_patch_document_and_facts(env: Env, h):
    client = env.client
    case_id, token = new_case(env, h)
    upload(env, token, "misto.pdf")
    detail = client.get(f"/api/cases/{case_id}", headers=h).json()
    apolice = next(d for d in detail["documents"] if d["doc_type"] == "APOLICE")
    assert apolice["needs_review"] is True

    r = client.patch(f"/api/cases/{case_id}/documents/{apolice['id']}", headers=h,
                     json={"doc_type": "TERMO_QUITACAO_SUBROGACAO", "title": "Recibo de sub-rogação"})
    assert r.status_code == 200
    doc = next(d for d in r.json()["documents"] if d["id"] == apolice["id"])
    assert doc["doc_type"] == "TERMO_QUITACAO_SUBROGACAO" and doc["needs_review"] is False
    assert doc["title"] == "Recibo de sub-rogação"
    assert client.patch(f"/api/cases/{case_id}/documents/nope", json={"title": "x"}, headers=h).status_code == 404

    r = client.patch(f"/api/cases/{case_id}/facts", headers=h,
                     json={"valor_pago": "48.230,00", "causador_nome": {"value": "João", "page": 2}})
    assert r.status_code == 200
    body = r.json()
    facts = body["facts"]
    assert facts["valor_pago"]["value"] == "48.230,00"
    assert facts["causador_nome"] == {"value": "João", "source_doc_id": None, "page": 2}
    assert facts["segurado_nome"]["value"] == "Maria Silva"  # merge parcial preserva o resto
    assert "valor_pago" not in facts["valores_alternativos"]  # conflito conferido
    assert not any(a["kind"] == "CONFLICT" for a in body["alerts"])
    assert body["edited_fields"] == ["causador_nome", "valor_pago"]
    r = client.patch(f"/api/cases/{case_id}/facts", json={"inexistente": 1}, headers=h)
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)


def test_edited_facts_survive_reprocessing(env: Env, h):
    client = env.client
    case_id, token = new_case(env, h)
    upload(env, token, "misto.pdf")
    client.patch(f"/api/cases/{case_id}/facts", json={"valor_pago": "50000.00"}, headers=h)
    upload(env, token, "pagto.pdf")  # dispara nova extração (fake devolve 48320.00 + conflito)
    detail = client.get(f"/api/cases/{case_id}", headers=h).json()
    assert detail["facts"]["valor_pago"]["value"] == "50000.00"
    assert detail["facts"]["segurado_nome"]["value"] == "Maria Silva"
    assert "valor_pago" not in detail["facts"]["valores_alternativos"]
    assert detail["edited_fields"] == ["valor_pago"]


def test_approve_and_reopen(env: Env, h):
    client = env.client
    case_id, token = new_case(env, h)
    upload(env, token, "misto.pdf")
    r = client.post(f"/api/cases/{case_id}/approve", headers=h)
    assert r.status_code == 409 and "force" in r.json()["detail"]
    r = client.post(f"/api/cases/{case_id}/approve", json={"force": True}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "PRONTO"
    r = client.post(f"/api/cases/{case_id}/reopen", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "PENDENTE"
    upload(env, token, "pagto.pdf", "termo.pdf", "bo.pdf", "nf.pdf")
    r = client.post(f"/api/cases/{case_id}/approve", json={}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "PRONTO"


def test_generate_dossie_and_peticao(env: Env, h):
    client = env.client
    case_id, token = new_case(env, h, "TRANSPORTE")
    assert client.post(f"/api/cases/{case_id}/dossie", headers=h).status_code == 409  # sem documentos
    upload(env, token, "misto.pdf", "termo.pdf")

    r = client.post(f"/api/cases/{case_id}/dossie", headers=h)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF") and r.content.index(b"PROCURACAO") < r.content.index(b"TERMO")

    r = client.post(f"/api/cases/{case_id}/peticao", headers=h)
    assert r.status_code == 200 and "wordprocessingml" in r.headers["content-type"]
    assert r.text == "Maria Silva Doc. 01 TRANSPORTE -" and "x-narrative" not in r.headers
    assert client.get("/api/cases/nope", headers=h).status_code == 404


def test_peticao_narrative_llm_and_fallback(tmp_path: Path):
    def ok_steps() -> Steps:
        s = fake_steps()
        s.write_narrative = lambda facts, index, scenario, client=None: "NARRATIVA"
        return s

    def bad_steps() -> Steps:
        s = fake_steps()

        def boom(*a, **k):
            raise RuntimeError("sem LLM")
        s.write_narrative = boom
        return s

    for factory, header, text in ((ok_steps, "llm", "NARRATIVA"), (bad_steps, "fallback", "-")):
        env = make_env(tmp_path / header, steps_factory=factory)
        h = env.headers()
        case_id, token = new_case(env, h)
        upload(env, token, "misto.pdf")
        r = env.client.post(f"/api/cases/{case_id}/peticao?narrative=true", headers=h)
        assert r.status_code == 200 and r.headers["x-narrative"] == header
        assert r.text.endswith(text)


def real_dossie_steps() -> Steps:
    s = fake_steps()
    s.build_dossie = build_dossie
    return s


def test_dossie_zip_when_over_max_mb(tmp_path: Path):
    env = make_env(tmp_path, steps_factory=real_dossie_steps)
    h = env.headers()
    case_id, token = new_case(env, h)
    upload(env, token, "misto.pdf", "bo.pdf", real=True)
    r = env.client.post(f"/api/cases/{case_id}/dossie?max_mb=100", headers=h)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    r = env.client.post(f"/api/cases/{case_id}/dossie?max_mb=0.0001", headers=h)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert len(names) > 1 and all(n.endswith(".pdf") for n in names)
    assert env.client.post(f"/api/cases/{case_id}/dossie?max_mb=0", headers=h).status_code == 422


def test_thumbnail_png(env: Env, h):
    case_id, token = new_case(env, h)
    upload(env, token, "misto.pdf", real=True)
    doc = env.client.get(f"/api/cases/{case_id}", headers=h).json()["documents"][0]
    url = f"/api/cases/{case_id}/documents/{doc['id']}/pages/0.png?scale=0.2"
    r = env.client.get(url, headers=h)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    token_jwt = h["Authorization"].split()[1]
    assert env.client.get(f"{url}&access_token={token_jwt}").status_code == 200  # para <img src>
    assert env.client.get(url).status_code == 401
    assert env.client.get(f"/api/cases/{case_id}/documents/{doc['id']}/pages/9.png", headers=h).status_code == 404


def test_page_dedupe_skips_known_pages(env: Env, h):
    case_id, token = new_case(env, h)
    a = make_pdf(["Apólice", "Aviso", "Procuração"])
    CLASSIFY_CALLS.clear()
    env.client.post(f"/api/public/upload/{token}", files=[("files", ("misto.pdf", a, "application/pdf"))])
    # mesmo conteúdo + 1 página nova, em outro arquivo (bytes diferentes)
    b = make_pdf(["Apólice", "Aviso", "Procuração", "Boletim novo"])
    r = env.client.post(f"/api/public/upload/{token}", files=[("files", ("misto2.pdf", b, "application/pdf"))])
    assert r.json()["accepted"] == ["misto2.pdf"]
    calls = [(name.split("_", 1)[1], n) for name, n in CLASSIFY_CALLS]  # tira o prefixo sha
    assert calls == [("misto.pdf", 3), ("misto2__unicas.pdf", 1)]
    detail = env.client.get(f"/api/cases/{case_id}", headers=h).json()
    f2 = next(f for f in detail["files"] if f["filename"] == "misto2.pdf")
    assert f2["duplicate_pages"] == 3
    assert len(detail["documents"]) == 4
    new_doc = [d for d in detail["documents"] if d["source_file"].endswith("misto2.pdf")]
    assert len(new_doc) == 1 and new_doc[0]["pages"] == [3]


def test_delete_case(env: Env, h):
    case_id, token = new_case(env, h)
    upload(env, token, "bo.pdf")
    org_id = env.client.get("/api/auth/me", headers=h).json()["org"]["id"]
    folder = env.tmp / "storage" / "orgs" / org_id / "cases" / case_id
    assert folder.is_dir()
    assert env.client.delete(f"/api/cases/{case_id}", headers=h).status_code == 204
    assert not folder.exists()
    assert env.client.get(f"/api/cases/{case_id}", headers=h).status_code == 404
    assert env.client.get(f"/api/public/upload/{token}").status_code == 404


def test_process_case_failure_is_recorded(env: Env, h):
    case_id, token = new_case(env, h)

    def boom(path, client=None):
        raise RuntimeError("API fora do ar")

    env.client.app.state.ctx.steps_factory = lambda: Steps(boom, fake_segment, fake_extract, fake_dossie,
                                                           fake_petition)
    upload(env, token, "bo.pdf")
    detail = env.client.get(f"/api/cases/{case_id}", headers=h).json()
    assert detail["status"] == "REVISAR" and "API fora do ar" in detail["error"]

    # reprocessar com etapas boas pega o arquivo pendente
    process_case(case_id, db.session_factory(env.engine), env.tmp / "storage", steps=fake_steps())
    with db.session_factory(env.engine)() as s:
        case = s.exec(select(Case)).first()
        assert case.status == "PENDENTE" and case.error is None
    assert len(env.client.get(f"/api/cases/{case_id}", headers=h).json()["documents"]) == 1


def test_lock_prevents_concurrent_processing(env: Env, h):
    case_id, token = new_case(env, h)
    lock = case_lock(case_id)
    lock.acquire()
    try:
        upload(env, token, "bo.pdf")  # worker não consegue a trava: só registra o pedido
        with db.session_factory(env.engine)() as s:
            case = s.get(Case, case_id)
            assert case.run_requested is True and case.status == "PROCESSANDO"
        assert env.client.delete(f"/api/cases/{case_id}", headers=h).status_code == 409
    finally:
        lock.release()
    # quem liberar a trava (ou um novo pedido) atende o pedido pendente
    process_case(case_id, db.session_factory(env.engine), env.tmp / "storage", steps=fake_steps())
    detail = env.client.get(f"/api/cases/{case_id}", headers=h).json()
    assert detail["status"] == "PENDENTE" and len(detail["documents"]) == 1


def test_spa_fallback_with_frontend(tmp_path: Path):
    front = tmp_path / "frontend"
    (front / "assets").mkdir(parents=True)
    (front / "index.html").write_text("<html>SPA</html>")
    (front / "assets" / "app.js").write_text("console.log(1)")
    env = make_env(tmp_path, frontend_dir=front)
    c = env.client
    assert c.get("/").text == "<html>SPA</html>"
    assert c.get("/casos/abc").text == "<html>SPA</html>"
    assert c.get("/enviar/token123").text == "<html>SPA</html>"
    assert c.get("/assets/app.js").text == "console.log(1)"
    assert c.get("/../../etc/passwd").text == "<html>SPA</html>"
    r = c.get("/api/nao-existe")
    assert r.status_code == 404 and r.json()["detail"] == "Rota não encontrada."
    assert c.get("/api/health").json()["ok"] is True


def test_no_frontend_only_api(env: Env):
    assert env.client.get("/").status_code == 404
    assert env.client.get("/api/health").status_code == 200
