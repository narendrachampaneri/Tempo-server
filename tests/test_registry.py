import json

import httpx

from tempo.registry import Registry
from tempo.types import TASKS


def test_bundled_registry_is_valid():
    registry = Registry.load(env={})
    models = registry.all()
    assert len(models) >= 10
    assert {"groq", "cerebras", "gemini", "openrouter", "ollama"} <= set(registry.providers)
    for m in models:
        assert set(m.skills) <= set(TASKS), m.id
        assert all(0 <= v <= 1 for v in m.skills.values()), m.id
        assert 0 <= m.strength <= 1
        assert m.id.split("/")[0] in ("groq", "cerebras", "gemini", "openrouter", "ollama_chat")


def test_configuration_comes_from_env():
    registry = Registry.load(
        env={"GROQ_API_KEY": " gsk_123 ", "OLLAMA_API_BASE": "http://box:11434/"}
    )
    assert registry.is_configured("groq")
    assert not registry.is_configured("gemini")
    assert registry.is_configured("ollama")
    assert registry.credentials("groq") == {"api_key": "gsk_123"}
    assert registry.credentials("ollama") == {"api_base": "http://box:11434"}


def test_mock_models_are_opt_in():
    assert Registry.load(env={}).get("mock/smart") is None
    registry = Registry.load(env={}, include_mock=True)
    assert registry.get("mock/smart") is not None
    assert registry.is_configured("mock")


LICENCES = {
    "qwen2.5:7b": "                                 Apache License\n  Version 2.0, January 2004",
    "phi4-mini:latest": "MIT License\n\nCopyright (c) Microsoft Corporation.",
    "llama3.2:latest": "LLAMA 3.2 COMMUNITY LICENSE AGREEMENT\nLlama 3.2 Version Release Date",
}


def _ollama_transport(tags: list[dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            name = json.loads(request.content)["model"]
            return httpx.Response(200, json={"license": LICENCES.get(name, "")})
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": tags})

    return httpx.MockTransport(handler)


async def test_ollama_discovery_registers_installed_models():
    registry = Registry.load(env={"OLLAMA_API_BASE": "http://ollama:11434"})
    transport = _ollama_transport(
        [
            {"name": "qwen2.5:7b", "details": {"parameter_size": "7.6B", "family": "qwen2"}},
            {"name": "nomic-embed-text:latest", "details": {"parameter_size": "137M"}},
        ]
    )
    found = await registry.discover_ollama(transport=transport)
    assert found == 1
    discovered = registry.get("ollama_chat/qwen2.5:7b")
    assert discovered is not None and discovered.installed is True
    assert 0.4 < discovered.strength < 0.6
    assert registry.get("ollama_chat/nomic-embed-text") is None
    # The seeded llama3.2 entry is not installed on this server.
    assert registry.get("ollama_chat/llama3.2").installed is False


async def test_ollama_discovery_matches_seeded_models_by_name():
    registry = Registry.load(env={"OLLAMA_API_BASE": "http://ollama:11434"})
    await registry.discover_ollama(transport=_ollama_transport([{"name": "llama3.2:latest"}]))
    assert registry.get("ollama_chat/llama3.2").installed is True


async def test_ollama_discovery_failure_leaves_registry_unchanged():
    registry = Registry.load(env={"OLLAMA_API_BASE": "http://ollama:11434"})

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert await registry.discover_ollama(transport=httpx.MockTransport(handler)) == 0
    assert registry.get("ollama_chat/llama3.2").installed is None


async def test_ollama_discovery_skipped_when_not_configured():
    registry = Registry.load(env={})

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not be called")

    assert await registry.discover_ollama(transport=httpx.MockTransport(handler)) == 0


def test_every_hand_entered_limit_has_a_source_and_a_date():
    from tempo.registry import Registry

    registry = Registry.load(env={})
    limit_fields = ("free_rpm", "free_rpd", "free_tpm", "free_tpd")
    for provider in registry.providers.values():
        if provider.shared_rpm or provider.shared_rpd or provider.shared_rpmonth:
            assert provider.limits_source and provider.limits_checked, provider.id
    for model in registry.all():
        if any(getattr(model, name) for name in limit_fields):
            provider = registry.providers[model.provider]
            assert model.limits_source or provider.limits_source, model.id
            assert model.limits_checked or provider.limits_checked, model.id


async def test_ollama_discovery_records_each_models_licence():
    registry = Registry.load(env={"OLLAMA_API_BASE": "http://ollama:11434"})
    tags = [{"name": "qwen2.5:7b"}, {"name": "phi4-mini:latest"}, {"name": "mystery:1b"}]
    await registry.discover_ollama(transport=_ollama_transport(tags))
    licences = {m.id: m.licence for m in registry.all() if m.provider == "ollama"}
    assert licences["ollama_chat/qwen2.5:7b"] == "Apache-2.0"
    assert licences["ollama_chat/phi4-mini"] == "MIT"
    assert licences["ollama_chat/mystery:1b"] is None  # no licence text: unknown
    assert licences["ollama_chat/llama3.2"] == "llama3.2"  # seeded
    verdicts = {
        m.id: registry.training_verdict(m) for m in registry.all() if m.provider == "ollama"
    }
    assert verdicts["ollama_chat/qwen2.5:7b"] == verdicts["ollama_chat/phi4-mini"] == "yes"
    assert verdicts["ollama_chat/llama3.2"] == verdicts["ollama_chat/mystery:1b"] == "unclear"
