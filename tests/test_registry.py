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


def _ollama_transport(tags: list[dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
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
