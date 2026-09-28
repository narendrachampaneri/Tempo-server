"""tempo collect: licensed public questions, run slowly within free limits, resumable."""

import gzip
import json

import httpx
from conftest import make_engine
from test_laya import FakeLaya
from typer.testing import CliRunner

from tempo import collect as col
from tempo.config import Settings
from tempo.datasets import DATASETS, Item, interleave, usable
from tempo.engine import RunResult
from tempo.laya_decider import LayaDecider
from tempo.tuning import build_rows, export


class FakeTime:
    """A clock that only moves when the collector sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def items(n: int = 4) -> dict[str, list[Item]]:
    return {
        "gsm8k": [Item(str(i), f"What is {i} + {i}?") for i in range(n)],
        "mbpp": [Item(str(i), f"Write a Python function that returns {i}.") for i in range(n)],
    }


def test_every_dataset_records_its_licence_and_parses():
    samples = {
        "gsm8k": b'{"question": "Tom has 3 apples.", "answer": "3"}\n',
        "mbpp": b'{"task_id": 11, "text": "Reverse a list.", '
        b'"test_list": ["assert f([1]) == [1]"]}\n',
        "humaneval": gzip.compress(b'{"task_id": "HumanEval/0", "prompt": "def f():\\n"}\n'),
        "dolly": b'{"instruction": "Summarize this.", "context": "Some text.", "category": "x"}\n',
        "dolly-translate": b'{"instruction": "Where is the nearest train station?", '
        b'"context": ""}\n',
    }
    for name, dataset in DATASETS.items():
        assert dataset.license and dataset.license_url.startswith("https://")
        assert dataset.personal_data
        parsed = list(dataset.parse(samples[name]))
        assert len(parsed) == 1 and parsed[0].text, name
    assert "tests" in next(DATASETS["mbpp"].parse(samples["mbpp"])).text
    assert next(DATASETS["dolly-translate"].parse(samples["dolly-translate"])).text.startswith(
        "Translate into"
    )


def test_items_that_may_hold_personal_data_are_skipped():
    assert usable("What is 17% of 2,340?") is None
    assert usable("Email me at asha.rao@example.com") == "may contain personal data"
    assert usable("Call +91 98765 43210 today") == "may contain personal data"
    assert usable("Server 192.168.1.20 is down") == "may contain personal data"
    assert usable("Ring (555) 123-4567 or pay with 4111 1111 1111 1111") is not None
    # Numbers in maths and code are not phone numbers.
    assert usable("assert volume_sphere(10)==4188.790204786391") is None
    assert usable("Population: 1 234 567 in 2020, up from 1 100 000") is None
    assert usable("assert f(1234567890) == 10") is None
    assert usable("x" * 3000) == "too long"


def test_order_is_fixed_and_interleaved():
    first = [(n, i.item_id) for n, i in interleave(items())]
    assert first == [(n, i.item_id) for n, i in interleave(items())]
    assert {n for n, _ in first[:2]} == {"gsm8k", "mbpp"}  # both datasets from the start
    assert len(first) == 8


def test_datasets_are_downloaded_once(tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=b'{"question": "Q?"}\n')

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert col.load_items(["gsm8k"], tmp_path, client)["gsm8k"] == [Item("0", "Q?")]
    col.load_items(["gsm8k"], tmp_path, client)
    assert len(calls) == 1 and (tmp_path / DATASETS["gsm8k"].filename).exists()


async def test_collect_is_paced_and_resumes(tmp_path):
    engine, backend = make_engine(data_dir=tmp_path)
    t = FakeTime()
    run = dict(
        per_minute=2, providers=["alpha", "beta"], say=lambda _: None, sleep=t.sleep, clock=t.clock
    )
    stats = await col.run(engine, items(), limit=3, **run)
    assert stats.done == 3 and stats.stopped == "limit"
    assert t.sleeps == [30.0, 30.0]  # two questions a minute, one at a time
    assert len(backend.called_for("judge")) >= 3  # every question was checked by a judge

    stats = await col.run(engine, items(), **run)
    assert stats.already == 3 and stats.done == 5 and stats.stopped == "finished"
    counts = engine.store.collect_counts()
    assert counts == {"gsm8k": {"done": 4}, "mbpp": {"done": 4}}
    questions = engine.store.query("SELECT DISTINCT user_id FROM questions")
    assert questions == [{"user_id": col.COLLECT_USER}]  # kept apart from real traffic


async def test_collect_waits_or_stops_when_free_quota_is_used_up(tmp_path):
    engine, _ = make_engine(data_dir=tmp_path)
    t = FakeTime()
    answers = iter([RunResult(error="No model available", error_kind="unavailable")] * 2)
    real = engine.complete

    async def complete(messages, options):
        nxt = next(answers, None)
        return nxt if nxt is not None else await real(messages, options)

    engine.complete = complete
    stats = await col.run(
        engine, items(1), wait=False, say=lambda _: None, sleep=t.sleep, clock=t.clock
    )
    assert stats.stopped == "exhausted" and stats.done == 0
    assert engine.store.collect_counts() == {}  # nothing marked: the next run retries it

    stats = await col.run(engine, items(1), say=lambda _: None, sleep=t.sleep, clock=t.clock)
    assert stats.done == 2 and stats.waited_s == col.WAIT_S  # waited once, then went on


async def test_failed_questions_are_retried_a_few_times(tmp_path):
    engine, _ = make_engine(data_dir=tmp_path)
    t = FakeTime()

    async def broken(messages, options):
        return RunResult(error="Internal error", error_kind="internal")

    engine.complete = broken
    one = {"gsm8k": [Item("0", "What is 1 + 1?")]}
    for _ in range(col.MAX_ATTEMPTS + 1):
        await col.run(engine, one, say=lambda _: None, sleep=t.sleep, clock=t.clock)
    row = engine.store.collect_item("gsm8k", "0")
    assert row["status"] == "error" and row["attempts"] == col.MAX_ATTEMPTS


async def test_personal_data_items_are_marked_skipped(tmp_path):
    engine, _ = make_engine(data_dir=tmp_path)
    t = FakeTime()
    mixed = {"gsm8k": [Item("0", "Mail bob@example.com the total"), Item("1", "What is 2+2?")]}
    stats = await col.run(engine, mixed, say=lambda _: None, sleep=t.sleep, clock=t.clock)
    assert stats.skipped == 1 and stats.done == 1
    assert engine.store.collect_item("gsm8k", "0")["note"] == "may contain personal data"


async def test_export_records_each_rows_dataset_and_licence(tmp_path):
    engine, _ = make_engine(data_dir=tmp_path)
    engine.laya = LayaDecider(Settings(laya_timeout_ms=1000), engine.store, loader=FakeLaya)
    engine.laya._load()
    t = FakeTime()
    await col.run(engine, items(2), say=lambda _: None, sleep=t.sleep, clock=t.clock)
    rows, stats = build_rows(engine.store, engine.registry, include_unclear=True, test_percent=0)
    sources = [json.loads(r["factors"]).get("source") for r in rows]
    assert sources and all(s and s["license"] in ("MIT", "CC-BY-4.0") for s in sources)
    assert set(stats.sources) == {"gsm8k", "mbpp"}

    # Test providers have no recorded terms (unclear): their outputs shaped every label.
    strict, strict_stats = build_rows(engine.store, engine.registry)
    assert strict == [] and strict_stats.skipped_terms > 0

    export(engine.store, engine.registry, tmp_path / "ds", include_unclear=True)
    readme = (tmp_path / "ds" / "README.md").read_text()
    assert "MIT (https://github.com/openai/grade-school-math" in readme
    assert "CC-BY-4.0" in readme


def test_default_providers_leave_out_terms_that_say_no():
    engine, _ = make_engine()
    engine.registry.providers["beta"].training_on_outputs = "no"
    assert col.default_providers(engine.registry) == ["alpha", "local"]


def test_estimate_uses_free_limits_and_the_reserve():
    engine, _ = make_engine()
    est = col.estimate(engine.registry, reserve=0.5, per_minute=100)
    by_provider = {p.provider: p for p in est.providers}
    # alpha: 20 + 14,400 requests/day; beta: 14,400; half kept for users. Local models have no
    # quota: only the pace (100 questions/min x 4 requests each) limits them.
    assert by_provider["alpha"].requests_per_day == (20 + 14400) / 2
    assert by_provider["local"].requests_per_day == 100 * 60 * 24 * 4
    assert est.questions_needed == 778  # 7,000 decisions / 9 per question
    assert est.days() is not None and est.days() < 1
    assert any("days" in line for line in col.describe(est))


def test_cli_list_estimate_and_status(tmp_path, monkeypatch):
    from tempo.cli import app

    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TEMPO_EMBEDDINGS", "off")
    runner = CliRunner()
    listed = runner.invoke(app, ["collect", "--list"])
    assert listed.exit_code == 0 and "CC-BY-SA-3.0" in listed.stdout
    est = runner.invoke(app, ["collect", "--estimate"])
    assert est.exit_code == 0 and "labelled decisions" in est.stdout
    status = runner.invoke(app, ["collect", "--status"])
    assert status.exit_code == 0 and "Nothing collected yet" in status.stdout


async def test_local_only_yes_models_collect_and_export(tmp_path):
    """Owner's rule: local Apache-2.0/MIT models count as "yes"; collect --yes-only uses only
    them (judge included), and every exported row names its source and licences."""
    from tempo.tuning import build_rows

    engine, backend = make_engine(data_dir=tmp_path)
    engine.laya = LayaDecider(Settings(laya_timeout_ms=1000), engine.store, loader=FakeLaya)
    engine.laya._load()
    engine.registry.get("local/tiny").licence = "Apache-2.0"
    engine.registry.add(
        engine.registry.get("local/tiny").model_copy(
            update={"id": "local/judge", "family": "other", "licence": "MIT"}
        )
    )
    t = FakeTime()
    stats = await col.run(
        engine,
        items(2),
        yes_only=True,
        providers=["alpha", "beta", "local"],
        say=lambda _: None,
        sleep=t.sleep,
        clock=t.clock,
    )
    assert stats.done == 4  # two per dataset
    assert set(backend.called) <= {"local/tiny", "local/judge"}
    assert col.has_yes_models(engine.registry, "local")
    assert not col.has_yes_models(engine.registry, "alpha")
    rows, row_stats = build_rows(engine.store, engine.registry)  # no --include-unclear
    assert rows and row_stats.skipped_terms == 0
    for row in rows:
        factors = json.loads(row["factors"])
        assert factors["source"]["license"] and factors["source"]["dataset"] in ("gsm8k", "mbpp")
        assert all(t["verdict"] == "yes" and t["licence"] for t in factors["output_terms"].values())

    est = col.estimate(engine.registry, yes_only=True, per_minute=2)
    assert [p.provider for p in est.providers] == ["local"]
