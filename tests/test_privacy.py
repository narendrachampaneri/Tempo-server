"""Consent (opt-in, withdrawable), scrubbing, deleting one's data, and Dolly as test-only."""

import json

from conftest import judge_reply, make_engine, user
from fastapi.testclient import TestClient

from tempo import sft
from tempo.api import create_app
from tempo.privacy import scrub
from tempo.tuning import build_rows


def test_scrub_replaces_personal_data():
    text = (
        "Mail asha@example.com or call +91 98765 43210 / 9876543210, card 4111 1111 1111 1111, "
        "Aadhaar 1234 5678 9012, PAN ABCDE1234F, server 10.0.0.7."
    )
    clean = scrub(text)
    for secret in ("asha@", "98765", "4111", "5678 9012", "ABCDE1234F", "10.0.0.7"):
        assert secret not in clean, secret
    for tag in ("[email]", "[phone]", "[card]", "[aadhaar]", "[pan]", "[ip]"):
        assert tag in clean
    assert scrub("The answer is 42, in 2026.") == "The answer is 42, in 2026."


async def consenting_engine():
    scores = lambda cands: [9 if c.startswith("Fix") else 4 for c in cands]  # noqa: E731
    engine, _ = make_engine({"*:judge": judge_reply(scores)})
    for provider in engine.registry.providers.values():
        provider.training_on_outputs = "yes"
    asha, _ = engine.accounts.create_user("asha")
    question = "Write a Python function that emails asha@example.com a report"
    await engine.complete(user(question), engine.options(access=engine.access_for(asha.id)))
    return engine, asha


async def test_other_users_need_consent_which_can_be_withdrawn():
    engine, asha = await consenting_engine()
    owner = sft.DEFAULT_USERS
    assert sft.build(engine.store, engine.registry, users=owner)[0] == []
    assert not engine.accounts.consent(asha.id)  # off by default

    engine.accounts.set_consent(asha.id, True)
    allowed = set(owner) | engine.accounts.consented_users()
    rows, _, _ = sft.build(engine.store, engine.registry, users=allowed, test_percent=0)
    assert rows and "asha@example.com" not in json.dumps(rows)  # scrubbed
    assert "[email]" in rows[0]["messages"][0]["content"]
    laya_rows, _ = build_rows(engine.store, engine.registry, users=allowed)
    assert "asha@example.com" not in json.dumps(laya_rows)

    engine.accounts.set_consent(asha.id, False)  # withdrawn
    assert asha.id not in engine.accounts.consented_users()
    log = engine.store.query("SELECT consent FROM consent_log WHERE user_id = ?", (asha.id,))
    assert [r["consent"] for r in log] == [1, 0]


async def test_users_can_delete_their_data():
    engine, asha = await consenting_engine()
    assert engine.store.query("SELECT id FROM questions WHERE user_id = ?", (asha.id,))
    assert engine.accounts.delete_data(asha.id) == 1
    for table in ("questions",):
        assert not engine.store.query(f"SELECT * FROM {table} WHERE user_id = ?", (asha.id,))
    assert not engine.store.query("SELECT * FROM stages") or all(
        s["question_id"] != asha.id for s in engine.store.query("SELECT * FROM stages")
    )


def test_consent_and_delete_through_the_api():
    engine, _ = make_engine()
    asha, key = engine.accounts.create_user("asha")
    client = TestClient(create_app(engine))
    auth = {"Authorization": f"Bearer {key}"}
    assert client.get("/api/consent", headers=auth).json() == {"consent": False, "owner": False}
    assert client.put("/api/consent", json={"consent": True}, headers=auth).json()["consent"]
    assert engine.accounts.consent(asha.id)
    assert client.delete("/api/data", headers=auth).json() == {"deleted_questions": 0}


async def test_dolly_rows_go_only_to_the_test_split():
    engine, _ = await consenting_engine()
    qid = engine.store.query("SELECT id FROM questions")[0]["id"]
    engine.store.execute(
        "INSERT INTO collect_items (dataset, item_id, status, question_id) VALUES (?,?,?,?)",
        ("dolly", "1", "done", qid),
    )
    engine.accounts.set_consent(
        engine.store.query("SELECT user_id FROM questions")[0]["user_id"], True
    )
    users = set(sft.DEFAULT_USERS) | engine.accounts.consented_users()
    rows, pairs, _ = sft.build(engine.store, engine.registry, users=users, test_percent=0)
    assert rows and all(r["split"] == "test" for r in rows if r["source"]["dataset"] == "dolly")
    assert all(p["split"] == "test" for p in pairs if p["source"]["dataset"] == "dolly")


def test_data_mix_warnings():
    stats = sft.SftStats(rows=10, public_share=0.1, self_share=0.5)
    notes = sft.mix_warnings(stats, 0.3, 0.3)
    assert len(notes) == 2 and "below 30%" in notes[0] and "above 30%" in notes[1]
    assert sft.mix_warnings(sft.SftStats(rows=10, public_share=0.5), 0.3, 0.3) == []
