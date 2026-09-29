"""TEMPO_CORS_ORIGINS: an explicit list of websites allowed to call the API from a browser."""

import pytest
from conftest import make_engine
from fastapi.testclient import TestClient

from tempo.api import create_app
from tempo.config import Settings, parse_origins


def client(origins):
    engine, _ = make_engine()
    return TestClient(create_app(engine=engine, settings=Settings(cors_origins=origins)))


def test_off_by_default():
    r = client([]).get("/health", headers={"Origin": "https://app.example.com"})
    assert "access-control-allow-origin" not in r.headers


def test_only_listed_websites_are_allowed():
    c = client(["https://app.example.com", "http://localhost:5173"])
    ok = c.get("/api/quota", headers={"Origin": "https://app.example.com"})
    assert ok.headers["access-control-allow-origin"] == "https://app.example.com"
    other = c.get("/api/quota", headers={"Origin": "https://evil.example.net"})
    assert "access-control-allow-origin" not in other.headers
    pre = c.options(
        "/api/ask",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert (
        pre.status_code == 200
        and "authorization" in pre.headers["access-control-allow-headers"].lower()
    )
    assert "access-control-allow-credentials" not in pre.headers


def test_setting_is_parsed_strictly():
    assert parse_origins(" https://a.example.com/ , http://localhost:3000") == [
        "https://a.example.com",
        "http://localhost:3000",
    ]
    assert parse_origins("") == [] and parse_origins(None) == []
    for bad in (
        "*",
        "https://*.example.com",
        "https://a.example.com/app",
        "a.example.com",
        "ftp://x.y",
    ):
        with pytest.raises(ValueError):
            parse_origins(bad)
    assert Settings.from_env(
        {"TEMPO_CORS_ORIGINS": "https://a.example.com", "TEMPO_DATA_DIR": "memory"}
    ).cors_origins == ["https://a.example.com"]
