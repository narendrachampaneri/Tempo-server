"""The step-2 providers, built and tested with mocks (no keys exist yet: "needs key")."""

import httpx

from tempo.registry import Registry
from tempo.sync import RegistrySync

KEYS = {
    "CLOUDFLARE_API_TOKEN": "placeholder",
    "CLOUDFLARE_ACCOUNT_ID": "acct123",
    "COHERE_API_KEY": "placeholder",
    "MISTRAL_API_KEY": "placeholder",
    "NVIDIA_API_KEY": "placeholder",
    "TEMPO_ENABLE_PROVIDERS": "nvidia",
}

CLOUDFLARE = {
    "success": True,
    "result": [
        {
            "name": "@cf/meta/llama-4-scout-17b-16e-instruct",
            "task": {"name": "Text Generation"},
            "properties": [
                {"property_id": "context_window", "value": "131000"},
                {"property_id": "function_calling", "value": "true"},
            ],
        },
        {
            "name": "@cf/qwen/qwen2.5-coder-32b-instruct",
            "task": {"name": "Text Generation"},
            "properties": [{"property_id": "beta", "value": "true"}],
        },
        {"name": "@cf/zai-org/glm-5.3", "task": {"name": "Text Generation"}},  # paid only
        {"name": "@cf/openai/whisper", "task": {"name": "Automatic Speech Recognition"}},
        {"name": "@cf/baai/bge-m3", "task": {"name": "Text Embeddings"}},
        {"name": "@cf/baai/bge-reranker-base", "task": {"name": "Text Classification"}},
        {"name": "@cf/meta/llama-guard-3-8b", "task": {"name": "Text Generation"}},
        {"name": "@cf/black-forest-labs/flux-1-schnell", "task": {"name": "Text-to-Image"}},
        {
            "name": "@cf/old/model",
            "task": {"name": "Text Generation"},
            "properties": [{"property_id": "planned_deprecation_date", "value": "2020-01-01"}],
        },
    ],
}
COHERE = {
    "models": [
        {
            "name": "command-a-03-2025",
            "endpoints": ["chat"],
            "context_length": 256000,
            "features": ["tools"],
        },
        {"name": "command-a-vision-07-2025", "endpoints": ["chat"], "context_length": 128000},
        {"name": "embed-v4.0", "endpoints": ["embed"]},
        {"name": "rerank-v3.5", "endpoints": ["rerank"]},
        {"name": "command-old", "endpoints": ["chat"], "is_deprecated": True},
    ]
}
MISTRAL = {
    "data": [
        {
            "id": "mistral-small-latest",
            "max_context_length": 131072,
            "capabilities": {"completion_chat": True, "function_calling": True, "vision": True},
        },
        {
            "id": "codestral-latest",
            "max_context_length": 256000,
            "capabilities": {"completion_chat": True},
        },
        {"id": "mistral-embed", "capabilities": {"completion_chat": False}},
        {"id": "mistral-moderation-latest", "capabilities": {"classification": True}},
        {
            "id": "magistral-medium-2507",
            "deprecation": "2020-02-01T00:00:00Z",
            "capabilities": {"completion_chat": True},
        },
    ]
}
NVIDIA = {
    "data": [
        {"id": "nvidia/nemotron-3-super-120b-a12b"},
        {"id": "nvidia/llama-3.1-nemoguard-8b-content-safety"},
        {"id": "nvidia/nv-embedqa-mistral-7b-v2"},
        {"id": "writer/palmyra-med-70b"},
        {"id": "writer/palmyra-fin-70b-32k"},
        {"id": "nvidia/nemotron-4-340b-reward"},
        {"id": "meta/llama-3.2-90b-vision-instruct"},
    ]
}
ZEN = {
    "data": [
        {"id": "big-pickle"},
        {"id": "nemotron-3.5-lightning-free"},
        {"id": "longcat-2.5-preview-free"},
        {"id": "space-bunny-free"},
        {"id": "jev-1.13-free"},
        {"id": "muse-spark-1.3-contributor-free"},
        {"id": "claude-sonnet-5"},  # paid: never added
    ]
}


def handler(seen: list[httpx.Request]):
    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        url = str(request.url)
        if "api.cloudflare.com" in url:
            assert "/accounts/acct123/ai/models/search" in url
            return httpx.Response(200, json=CLOUDFLARE)
        if "api.cohere.com" in url:
            return httpx.Response(200, json=COHERE)
        if "api.mistral.ai" in url:
            return httpx.Response(200, json=MISTRAL)
        if "integrate.api.nvidia.com" in url:
            return httpx.Response(200, json=NVIDIA)
        if "opencode.ai" in url:
            assert "authorization" not in request.headers  # public list, no key sent
            return httpx.Response(200, json=ZEN)
        if "openrouter.ai" in url:
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404)

    return respond


async def synced(env=KEYS):
    seen: list[httpx.Request] = []
    registry = Registry.load(env=env)
    status = await RegistrySync(registry, transport=httpx.MockTransport(handler(seen))).run()
    return registry, status, seen


async def test_cloudflare_lists_free_models_by_type_and_skips_paid_ones():
    registry, status, _ = await synced()
    assert status["cloudflare"].ok
    get = registry.get
    scout = get("cloudflare/@cf/meta/llama-4-scout-17b-16e-instruct")
    assert scout.type == "chat" and scout.context_window == 131000 and scout.tools
    assert get("cloudflare/@cf/qwen/qwen2.5-coder-32b-instruct").type == "code"
    assert get("cloudflare/@cf/qwen/qwen2.5-coder-32b-instruct").preview  # beta
    assert get("cloudflare/@cf/zai-org/glm-5.3") is None  # needs a paid plan
    assert get("cloudflare/@cf/openai/whisper").type == "speech-to-text"
    assert get("cloudflare/@cf/baai/bge-m3").type == "embedding"
    assert get("cloudflare/@cf/baai/bge-reranker-base").type == "reranker"
    assert get("cloudflare/@cf/meta/llama-guard-3-8b").type == "safety"
    assert get("cloudflare/@cf/black-forest-labs/flux-1-schnell") is None
    assert get("cloudflare/@cf/old/model").expires == "2020-01-01"
    # Calls go to Cloudflare's OpenAI-compatible endpoint for this account.
    creds = registry.credentials("cloudflare")
    assert creds["api_base"] == "https://api.cloudflare.com/client/v4/accounts/acct123/ai/v1"
    assert registry.litellm_model(scout) == "openai/@cf/meta/llama-4-scout-17b-16e-instruct"


async def test_cloudflare_needs_the_account_id():
    env = {k: v for k, v in KEYS.items() if k != "CLOUDFLARE_ACCOUNT_ID"}
    registry, status, _ = await synced(env)
    assert "cloudflare" not in status and not registry.is_configured("cloudflare")


async def test_cohere_mistral_and_nvidia_lists():
    registry, status, _ = await synced()
    get = registry.get
    command = get("cohere/command-a-03-2025")
    assert command.type == "chat" and command.free_rpm == 20 and command.tools
    assert get("cohere/command-a-vision-07-2025").type == "vision"
    assert get("cohere/embed-v4.0").type == "embedding"
    assert get("cohere/rerank-v3.5").type == "reranker"
    assert get("cohere/command-old") is None
    assert registry.providers["cohere"].shared_rpmonth == 1000

    small = get("mistral/mistral-small-latest")
    assert small.type == "chat" and small.vision and small.context_window == 131072
    assert get("mistral/codestral-latest").type == "code"
    assert get("mistral/mistral-embed").type == "embedding"
    assert get("mistral/mistral-moderation-latest").type == "safety"
    assert get("mistral/magistral-medium-2507").expires == "2020-02-01"

    assert get("nvidia/nvidia/llama-3.1-nemoguard-8b-content-safety").type == "safety"
    assert get("nvidia/nvidia/nv-embedqa-mistral-7b-v2").type == "embedding"
    assert get("nvidia/writer/palmyra-med-70b").domain == "health"
    assert get("nvidia/writer/palmyra-fin-70b-32k").domain == "finance"
    assert get("nvidia/nvidia/nemotron-4-340b-reward").type == "decision"
    assert get("nvidia/meta/llama-3.2-90b-vision-instruct").type == "vision"
    assert registry.data_policy(get("nvidia/nvidia/nemotron-3-super-120b-a12b")) == "may-train"


async def test_nvidia_list_is_public_and_read_even_while_nvidia_is_off():
    registry, status, seen = await synced(env={})
    assert status["nvidia"].ok and not registry.is_configured("nvidia")
    nvidia = [r for r in seen if "nvidia" in str(r.url)]
    assert nvidia and all("authorization" not in r.headers for r in nvidia)


async def test_opencode_zen_free_models_only_with_a_users_own_key():
    registry, status, _ = await synced(env={"OPENCODE_API_KEY": "server-key-never-used"})
    assert status["opencode"].ok
    zen = {m.id: m for m in registry.all() if m.provider == "opencode"}
    assert set(zen) == {
        "opencode/big-pickle",
        "opencode/nemotron-3.5-lightning-free",
        "opencode/longcat-2.5-preview-free",
        "opencode/space-bunny-free",
        "opencode/jev-1.13-free",
    }
    assert zen["opencode/jev-1.13-free"].type == "decision"
    assert zen["opencode/big-pickle"].preview and zen["opencode/space-bunny-free"].preview
    policy = {m: registry.data_policy(zen[m]) for m in zen}
    assert policy["opencode/big-pickle"] == "may-train"
    assert policy["opencode/nemotron-3.5-lightning-free"] == "may-train"
    assert policy["opencode/space-bunny-free"] == "may-log"
    assert policy["opencode/longcat-2.5-preview-free"] == "ok"
    # A server-wide key is never used; only a user's own.
    assert not registry.is_configured("opencode")
    from tempo.types import Access

    own = Access(user_id="u", user_keys={"opencode": "users-own"})
    assert registry.is_configured("opencode", own)
    assert registry.credentials("opencode", own)["api_key"] == "users-own"
    assert registry.credentials("opencode", own)["api_base"] == "https://opencode.ai/zen/v1"


def test_every_provider_records_terms_and_data_policy():
    registry = Registry.load(env={})
    for provider in registry.providers.values():
        if provider.local:
            continue
        assert provider.training_terms_url and provider.training_terms_quote, provider.id
        assert provider.training_terms_checked, provider.id
    for pid in ("gemini", "nvidia", "cohere", "mistral", "cloudflare", "opencode"):
        provider = registry.providers[pid]
        assert provider.data_policy != "unknown" and provider.data_policy_url, pid


async def test_synced_catalog_is_saved_and_reloaded(tmp_path):
    from tempo.sync import load_catalog, save_catalog

    registry, status, _ = await synced()
    registry.get("groq/openai/gpt-oss-120b").free_rpd = 777  # e.g. learned from headers
    path = tmp_path / "catalog.json"
    save_catalog(registry, status, path)
    fresh = Registry.load(env=KEYS)
    assert fresh.get("cohere/command-a-03-2025") is None
    assert load_catalog(fresh, path) is not None
    assert fresh.get("cohere/command-a-03-2025").free_rpm == 20
    assert fresh.get("groq/openai/gpt-oss-120b").free_rpd == 777
    assert load_catalog(fresh, tmp_path / "missing.json") is None


async def test_terms_check_finds_quotes_and_reports_changes():
    from tempo.terms import check, quotes
    from tempo.types import ProviderInfo

    provider = ProviderInfo(
        id="p",
        label="P",
        training_terms_url="https://example.test/terms",
        training_terms_quote=(
            'Summary line:\n"You may not use the Output to develop competing models."\n'
            '"Customer owns all Output generated through the Services at all times."'
        ),
    )
    assert len(quotes(provider.training_terms_quote)) == 2
    page = "<html><p>You may not use the Output to develop <b>competing</b> models.</p></html>"
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text=page))
    registry = Registry({"p": provider}, [], env={})
    [result] = await check(registry, transport)
    assert result.status == "changed" and result.found == 1
    assert result.missing == [
        "Customer owns all Output generated through the Services at all times."
    ]
    down = httpx.MockTransport(lambda r: httpx.Response(503))
    [result] = await check(registry, down)
    assert result.status == "unreachable"


async def test_free_catalog_rows():
    from tempo import catalog

    registry, status, _ = await synced()
    found = catalog.rows(registry, checked={p: s.checked_at for p, s in status.items()})
    by_id = {r["model"]: r for r in found}
    command = by_id["cohere/command-a-03-2025"]
    assert command["limits"] == "20/min · 1,000/month shared"
    assert command["data_policy"] == "may-train" and command["flagged"]
    assert command["status"] == "ready" and command["last_check"]
    assert by_id["nvidia/nvidia/nemotron-3-super-120b-a12b"]["context"] is None  # not listed
    assert by_id["cloudflare/@cf/openai/whisper"]["status"] == "ready (not chat)"
    assert "cloudflare/@cf/old/model" not in by_id  # expired
    assert by_id["opencode/big-pickle"]["status"] == "your own key"


def test_models_free_cli_reads_lists_and_saves_the_catalog(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from tempo.cli import app

    def offline(request):  # the CLI reads the lists live; here from the mocks
        return handler([])(request)

    real = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(offline)
        real(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)
    for key, value in KEYS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TEMPO_EMBEDDINGS", "off")
    result = CliRunner().invoke(app, ["models", "--free", "--json"])
    assert result.exit_code == 0, result.stdout
    assert '"cohere/command-a-03-2025"' in result.stdout
    assert (tmp_path / "catalog.json").exists()
