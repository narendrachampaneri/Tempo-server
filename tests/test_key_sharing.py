"""The server's own keys (.env or the environment) are the owner's: other users use only their
own keys unless TEMPO_SHARE_SERVER_KEYS=1. One key is one quota bucket, whoever uses it."""

from conftest import ENV_ALL, make_registry

from tempo.types import Access


def test_server_keys_are_only_for_the_owner():
    registry = make_registry()
    friend = Access(user_id="u123")
    for owner in (
        None,
        Access(user_id="local"),
        Access(user_id="admin"),
        Access(user_id="collect"),
    ):
        assert registry.is_configured("alpha", owner)
        assert registry.credentials("alpha", owner)["api_key"] == "a"
    assert not registry.is_configured("alpha", friend)
    assert "api_key" not in registry.credentials("alpha", friend)
    assert registry.is_configured("local", friend)  # a local model is not a key

    own = Access(user_id="u123", user_keys={"alpha": "friend-key"})
    assert registry.is_configured("alpha", own)
    assert registry.credentials("alpha", own)["api_key"] == "friend-key"
    assert not registry.is_configured("beta", own)


def test_sharing_is_opt_in():
    registry = make_registry({**ENV_ALL, "TEMPO_SHARE_SERVER_KEYS": "1"})
    friend = Access(user_id="u123")
    assert registry.is_configured("alpha", friend)
    assert registry.credentials("alpha", friend)["api_key"] == "a"


def test_one_key_is_one_quota_bucket():
    same = {"alpha": "placeholder-key"}
    ids = {Access(user_id=u, user_keys=same).key_id("alpha") for u in ("local", "admin", "collect")}
    assert len(ids) == 1 and next(iter(ids)).startswith("key:")
    assert "placeholder" not in next(iter(ids))
    assert Access(user_id="local").key_id("alpha") == "server"
