"""`tempo setup`: the provider walk-through, key checks, the vault and settings.env."""

import pytest
from typer.testing import CliRunner

from tempo import setup as wizard
from tempo.accounts import LOCAL_USER
from tempo.cli import app
from tempo.config import Settings
from tempo.engine import Engine


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TEMPO_EMBEDDINGS", "off")
    # set (then restored by monkeypatch) so the wizard's writes to os.environ don't leak
    for name in ("CLOUDFLARE_ACCOUNT_ID", "TEMPO_ENABLE_PROVIDERS", "OLLAMA_API_BASE"):
        monkeypatch.setenv(name, "")
    for name in ("GROQ_API_KEY", "CLOUDFLARE_API_TOKEN", "OPENCODE_API_KEY", "TEMPO_ENABLE_MOCK"):
        monkeypatch.delenv(name, raising=False)
    checked = []

    async def fake_verify(registry, provider, api_key, transport=None):
        checked.append(provider)
        return not api_key.startswith("bad")

    async def no_ollama(base, transport=None):
        return None

    monkeypatch.setattr(wizard, "verify_key", fake_verify)
    monkeypatch.setattr(wizard, "probe_ollama", no_ollama)
    return tmp_path, checked


def vault_keys():
    return Engine.from_settings(Settings.from_env()).accounts.keys(LOCAL_USER)


def test_walks_through_providers_and_stores_keys_encrypted(home):
    tmp_path, checked = home
    answers = "placeholder-groq-key\nacc123\nplaceholder-cf-key\nn\n"
    result = CliRunner().invoke(
        app,
        ["setup", "--only", "ollama,groq,cloudflare,nvidia,cerebras", "--no-sync"],
        input=answers,
    )
    assert result.exit_code == 0, result.output
    out = result.output
    assert "https://console.groq.com/keys" in out and "requests a day" in out
    assert "Training on its answers" in out and "Data policy" in out
    assert "Ollama is not running" in out
    assert "Cerebras" in out and "Off:" in out
    assert "private testing" in out and "Skipped (stays off)" in out
    assert "placeholder-groq-key" not in out and "placeholder-cf-key" not in out
    assert "fp:" in out and "Your free requests a day" in out
    assert checked == ["groq", "cloudflare"]
    assert set(vault_keys()) == {"groq", "cloudflare"}
    settings = (tmp_path / "settings.env").read_text(encoding="utf-8")
    assert "CLOUDFLARE_ACCOUNT_ID=acc123" in settings
    assert "placeholder" not in settings and "nvidia" not in settings
    assert (tmp_path / "tempo.db").read_bytes().find(b"placeholder-groq-key") == -1


def test_rejected_key_is_not_stored(home):
    result = CliRunner().invoke(app, ["setup", "--only", "groq", "--no-sync"], input="bad-key\n")
    assert result.exit_code == 0, result.output
    assert "rejected this key; nothing stored" in result.output
    assert vault_keys() == {}


def test_opt_in_provider_is_turned_on_only_with_the_users_own_key(home):
    tmp_path, _ = home
    result = CliRunner().invoke(
        app, ["setup", "--only", "opencode", "--no-sync"], input="y\nplaceholder-zen-key\n"
    )
    assert result.exit_code == 0, result.output
    assert "only your own key" in result.output
    assert "TEMPO_ENABLE_PROVIDERS=opencode" in (tmp_path / "settings.env").read_text(
        encoding="utf-8"
    )
    assert set(vault_keys()) == {"opencode"}


def test_settings_file_never_takes_a_secret(tmp_path):
    with pytest.raises(ValueError):
        wizard.write_settings(tmp_path / "settings.env", {"GROQ_API_KEY": "x"})
    assert wizard.enable_list("nvidia", "opencode") == "nvidia,opencode"
    assert wizard.enable_list("opencode", "opencode") == "opencode"


def test_owner_keys_from_setup_serve_the_admin_but_no_other_user(home):
    from tempo.accounts import ADMIN_USER

    engine = Engine.from_settings(Settings.from_env())
    engine.accounts.set_key(LOCAL_USER, "groq", "placeholder-groq-key", True)
    assert engine.access_for(ADMIN_USER).user_keys == {"groq": "placeholder-groq-key"}
    friend, _ = engine.accounts.create_user("friend")
    assert engine.access_for(friend.id).user_keys == {}
