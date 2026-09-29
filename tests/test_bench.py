"""`tempo-server bench`: time to the first token, to the answer being ready, and to done, per
mode. Fake providers here (fast, slow and failing); on a laptop it runs with real keys."""

import json

from conftest import judge_reply, make_engine, sleep
from typer.testing import CliRunner

from tempo.bench import run_bench
from tempo.cli import app
from tempo.providers import ProviderError

FAST = [("answer", "Canberra is the capital.")]
SLOW = [sleep(0.3), ("answer", "Canberra, after a long think.")]


async def test_bench_times_each_mode_and_the_background_check():
    scripts = {
        "beta/mid": FAST,
        "alpha/small": FAST,
        "alpha/strong": SLOW,
        "local/tiny": [ProviderError("unavailable", "down")],
        "*:judge": lambda m: [sleep(0.2), *judge_reply([9])(m)],
    }
    engine, _ = make_engine(scripts)
    question = "Write a Python function that checks whether a number is prime"  # judged
    results = await run_bench(engine, ["auto", "best"], [question])
    auto, best = (r.summary() for r in results)
    assert auto["errors"] == 0 and best["errors"] == 0
    # the answer is ready before the background check (a 0.2 s judge) has finished
    assert auto["first_token_s"] <= auto["ready_s"] < auto["total_s"]
    # Best waits for several models; Auto is done first
    assert auto["total_s"] < best["total_s"]
    # benchmark questions are not logged and skip the cache; their speed is recorded
    assert engine.store.query("SELECT 1 FROM questions") == []
    assert engine.speed.speed(engine.registry.get("beta/mid")).n >= 1


async def test_bench_counts_errors_when_every_model_fails():
    down = [ProviderError("unavailable", "down")]
    scripts = {m: down for m in ("beta/mid", "alpha/small", "alpha/strong", "local/tiny")}
    engine, _ = make_engine(scripts)
    (result,) = await run_bench(engine, ["auto"], ["hi"])
    summary = result.summary()
    assert summary["errors"] == 1 and summary["total_s"] is None
    assert result.runs[0].error


def test_bench_command_in_demo_mode(monkeypatch):
    for name, value in {
        "TEMPO_DATA_DIR": "memory",
        "TEMPO_EMBEDDINGS": "off",
        "TEMPO_ENABLE_MOCK": "1",
        "TEMPO_LAYA": "off",
        "TEMPO_SYNC_INTERVAL": "0",
    }.items():
        monkeypatch.setenv(name, value)
    result = CliRunner().invoke(app, ["bench", "--modes", "fast", "-n", "1", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    (row,) = data["modes"]
    assert row["mode"] == "fast" and row["questions"] == 1 and row["errors"] == 0
    assert row["first_token_s"] is not None and row["total_s"] >= row["ready_s"]
    assert data["speed"] and "ttft_s" in data["speed"][0]
    assert CliRunner().invoke(app, ["bench", "--modes", "slowest"]).exit_code != 0
