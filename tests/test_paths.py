"""Data folder per system, and the safe one-time move from ~/.tempo."""

from pathlib import Path

from tempo import paths
from tempo.config import Settings


def test_default_data_dir_per_system(tmp_path):
    home = tmp_path
    win = paths.default_data_dir({"LOCALAPPDATA": r"C:\Users\a\AppData\Local"}, "win32", home)
    assert win == Path(r"C:\Users\a\AppData\Local") / "tempo-server"
    assert paths.default_data_dir({}, "win32", home) == home / "AppData" / "Local" / "tempo-server"
    mac = paths.default_data_dir({}, "darwin", home)
    assert mac == home / "Library" / "Application Support" / "tempo-server"
    assert paths.default_data_dir({}, "linux", home) == home / ".local/share/tempo-server"
    xdg = paths.default_data_dir({"XDG_DATA_HOME": "/x"}, "linux", home)
    assert xdg == Path("/x/tempo-server")


def test_old_folder_is_moved_once(tmp_path):
    legacy = tmp_path / ".tempo"
    (legacy / "laya").mkdir(parents=True)
    (legacy / "tempo.db").write_bytes(b"db")
    (legacy / "laya" / "x.json").write_text("{}", encoding="utf-8")
    target = tmp_path / "data" / "tempo-server"
    assert "Moved your Tempo data" in paths.migrate(target, legacy)
    assert not legacy.exists()
    assert (target / "tempo.db").read_bytes() == b"db" and (target / "laya" / "x.json").exists()
    assert paths.migrate(target, legacy) is None  # nothing left to move


def test_move_across_drives_copies_before_deleting(tmp_path, monkeypatch):
    legacy = tmp_path / ".tempo"
    legacy.mkdir()
    (legacy / "secret.key").write_bytes(b"k")
    target = tmp_path / "other" / "tempo-server"
    real_replace = paths.os.replace
    calls = []

    def replace(src, dst):
        calls.append((Path(src).name, Path(dst).name))
        if Path(src) == legacy:
            raise OSError(18, "Invalid cross-device link")
        return real_replace(src, dst)

    monkeypatch.setattr(paths.os, "replace", replace)
    paths.migrate(target, legacy)
    assert calls[-1] == ("tempo-server.moving", "tempo-server")
    assert (target / "secret.key").read_bytes() == b"k" and not legacy.exists()


def test_an_old_folder_never_overwrites_data_in_use(tmp_path):
    legacy = tmp_path / ".tempo"
    legacy.mkdir()
    (legacy / "tempo.db").write_bytes(b"old")
    target = tmp_path / "tempo-server"
    target.mkdir()
    (target / "tempo.db").write_bytes(b"new")
    assert "left as is" in paths.migrate(target, legacy)
    assert (target / "tempo.db").read_bytes() == b"new" and legacy.exists()


def test_settings_use_the_system_folder_and_move_the_old_one(_private_home, monkeypatch):
    monkeypatch.delenv("TEMPO_DATA_DIR", raising=False)
    old = _private_home / ".tempo"
    old.mkdir()
    (old / "settings.env").write_text("TEMPO_MAX_STAGES=7\n", encoding="utf-8")
    monkeypatch.delenv("TEMPO_MAX_STAGES", raising=False)
    settings = Settings.from_env()
    assert settings.data_dir == paths.default_data_dir()
    assert (settings.data_dir / "settings.env").exists() and not old.exists()
    assert settings.max_stages == 7
    monkeypatch.delenv("TEMPO_MAX_STAGES", raising=False)  # load_dotenv set it
