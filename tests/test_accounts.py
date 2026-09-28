"""Users, the encrypted key vault, and bring-your-own-key routing."""

import os
import stat

import pytest
from conftest import make_engine
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tempo import api as api_module
from tempo.accounts import Accounts, load_vault_key
from tempo.api import create_app
from tempo.config import Settings
from tempo.store import Store

HELLO = [{"role": "user", "content": "hi"}]


def test_vault_encrypts_and_file_key_is_private(tmp_path):
    vault = load_vault_key(None, tmp_path)
    key_file = tmp_path / "secret.key"
    assert stat.S_IMODE(os.stat(key_file).st_mode) == 0o600
    token = vault.encrypt(b"gsk_supersecret")
    assert b"supersecret" not in token and vault.decrypt(token) == b"gsk_supersecret"
    assert load_vault_key(None, tmp_path).decrypt(token) == b"gsk_supersecret"  # same file
    passphrase = load_vault_key("correct horse battery staple", None)
    assert passphrase.decrypt(passphrase.encrypt(b"x")) == b"x"


def test_accounts_users_and_keys(tmp_path):
    store = Store(tmp_path / "t.db")
    accounts = Accounts(store, load_vault_key(None, tmp_path))
    assert not accounts.has_users()
    user, api_key = accounts.create_user("asha")
    assert api_key.startswith("tempo_") and accounts.authenticate(api_key).name == "asha"
    assert accounts.authenticate("tempo_wrong") is None
    assert api_key not in str(store.query("SELECT * FROM users"))  # only the hash is stored
    for bad in ("local", "admin", "asha", ""):
        with pytest.raises(ValueError):
            accounts.create_user(bad)

    accounts.set_key(user.id, "groq", "gsk_abcdef123456", verified=True)
    assert accounts.keys(user.id) == {"groq": "gsk_abcdef123456"}
    raw = str(store.query("SELECT * FROM user_keys"))
    assert "abcdef123456" not in raw
    assert accounts.key_info(user.id)[0]["last4"] == "3456"
    assert accounts.list_users()[0]["keys"] == 1
    assert accounts.delete_user("asha") and not store.query("SELECT * FROM user_keys")


def test_keys_from_another_secret_are_skipped(tmp_path):
    store = Store()
    Accounts(store, load_vault_key("first", None)).set_key("u", "groq", "gsk_12345678")
    assert Accounts(store, load_vault_key("second", None)).keys("u") == {}


def client_for(env=None, api_key=None, **settings):
    engine, backend = make_engine(env=env, **settings)
    app = create_app(engine=engine, settings=Settings(api_key=api_key))
    return TestClient(app), engine, backend


def test_local_mode_needs_no_auth_until_a_user_exists():
    client, engine, _ = client_for()
    assert client.get("/api/me").json() == {"user": "local", "mode": "local"}
    user, key = engine.accounts.create_user("ravi")
    assert client.get("/api/me").status_code == 401
    me = client.get("/api/me", headers={"Authorization": f"Bearer {key}"}).json()
    assert me == {"user": "ravi", "mode": "user"}


def test_admin_key_still_works():
    client, _, _ = client_for(api_key="s3cret")
    assert client.get("/api/me").status_code == 401
    assert (
        client.get("/api/me", headers={"Authorization": "Bearer s3cret"}).json()["mode"] == "admin"
    )


def test_key_endpoints_store_verify_and_never_return_the_key(monkeypatch):
    client, engine, _ = client_for()
    verdicts = {"alpha": True, "beta": False}

    async def fake_verify(registry, provider, key, transport=None):
        return verdicts.get(provider)

    monkeypatch.setattr(api_module, "verify_key", fake_verify)
    r = client.put("/api/keys/alpha", json={"api_key": "alpha-key-00001234"})
    assert r.status_code == 200 and r.json()["verified"] is True
    r = client.put("/api/keys/beta", json={"api_key": "beta-key-00005678"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "key_rejected"
    assert client.put("/api/keys/local", json={"api_key": "whatever-123"}).status_code == 404
    assert client.put("/api/keys/alpha", json={"api_key": "short"}).status_code == 422

    listing = client.get("/api/keys").json()
    assert "alpha-key-00001234" not in str(listing)
    alpha = next(p for p in listing["providers"] if p["provider"] == "alpha")
    assert alpha["has_key"] and alpha["last4"] == "1234" and alpha["verified"] is True
    assert [p["provider"] for p in listing["providers"]] == ["alpha", "beta"]

    assert client.delete("/api/keys/alpha").status_code == 200
    assert client.delete("/api/keys/alpha").status_code == 404


def test_own_keys_unlock_providers_and_use_their_own_quota():
    client, engine, backend = client_for(env={"BETA_KEY": "server-beta"})
    user, token = engine.accounts.create_user("mei")
    auth = {"Authorization": f"Bearer {token}"}
    statuses = {
        m["id"]: m["status"] for m in client.get("/api/models", headers=auth).json()["models"]
    }
    assert statuses["alpha/small"] == "not configured"

    engine.accounts.set_key(user.id, "alpha", "mei-alpha-key-1")
    models = client.get("/api/models", headers=auth).json()
    statuses = {m["id"]: m["status"] for m in models["models"]}
    assert statuses["alpha/small"] == "ready"
    assert next(p for p in models["providers"] if p["id"] == "alpha")["own_key"]

    body = {"model": "alpha/small", "messages": HELLO, "tempo": {"max_stages": 1}}
    r = client.post("/v1/chat/completions", json=body, headers=auth)
    assert r.status_code == 200 and r.json()["model"] == "alpha/small"
    access = backend.accesses[-1]
    assert access.user_id == user.id and access.user_keys == {"alpha": "mei-alpha-key-1"}
    small = engine.registry.get("alpha/small")
    assert engine.quota.left(small, f"user:{user.id}").rpd == 14399
    assert engine.quota.left(small, "server").rpd == 14400

    # Another user without the key cannot use it.
    _, other = engine.accounts.create_user("sam")
    r = client.post("/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {other}"})
    assert r.status_code == 503


def test_feedback_only_from_the_questions_owner():
    client, engine, _ = client_for()
    _, a = engine.accounts.create_user("a")
    _, b = engine.accounts.create_user("b")
    events = client.post(
        "/api/ask", json={"prompt": "hi"}, headers={"Authorization": f"Bearer {a}"}
    ).text
    import json as _json

    done = [_json.loads(line[6:]) for line in events.splitlines() if line.startswith("data: ")]
    question_id = next(e for e in done if e["type"] == "done")["question_id"]
    payload = {"question_id": question_id, "rating": -1}
    assert (
        client.post(
            "/api/feedback", json=payload, headers={"Authorization": f"Bearer {b}"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/feedback", json=payload, headers={"Authorization": f"Bearer {a}"}
        ).status_code
        == 200
    )


def test_cli_users_and_keys(tmp_path, monkeypatch):
    from tempo.cli import app

    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TEMPO_EMBEDDINGS", "off")
    runner = CliRunner()
    added = runner.invoke(app, ["users", "add", "lina"])
    assert added.exit_code == 0 and "tempo_" in added.stdout
    stored = runner.invoke(app, ["keys", "add", "groq", "--no-verify"], input="gsk_test_98765\n")
    assert stored.exit_code == 0, stored.output
    listed = runner.invoke(app, ["keys", "list"])
    assert "…8765" in listed.stdout and "gsk_test_98765" not in listed.stdout
    assert runner.invoke(app, ["keys", "remove", "groq"]).exit_code == 0
    assert runner.invoke(app, ["keys", "add", "nope", "--no-verify"], input="x\n").exit_code != 0
    assert "lina" in runner.invoke(app, ["users", "list"]).stdout
