"""Training data for Tempo's own writing model (Tempo-Core): `tempo-server export-sft` and
`tempo-server export-pairs`.

- **SFT rows**: the conversation, then the checked final answer. Only questions whose final
  answer passed its check (stop reason ``passed``) and got no 👎.
- **Preference pairs** (for DPO): the same prompt, ``chosen`` = the answer that passed, and
  ``rejected`` = an earlier answer to the same question that failed its check (a hard failure
  first, else the lowest score).

Only "yes" rows: every model whose text or grade shaped the question (drafts, fixes, judges)
must be "yes" (`tempo-server terms`), and the question itself must come from an openly
licensed dataset (`tempo-server collect`) or from the owner. Every row carries its source and
licences. The train/test split is by question, with the same hash as `tempo-server export-laya`,
so a question is in the held-out set of every export.

Nothing here trains anything; the plan is in docs/TEMPO_MODELS.md.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tempo.datasets import dataset_info
from tempo.privacy import scrub, scrub_value
from tempo.registry import Registry
from tempo.store import Store
from tempo.tuning import OWN_TRAFFIC, _licence, _loads, _split, _terms

# Whose questions may become training data without asking: `tempo-server collect` (public datasets)
# and the owner (the local user and the TEMPO_API_KEY admin). Other users' questions are used
# only if that user opted in (`tempo-server users consent`, off by default, can be withdrawn);
# text from Tempo's own traffic is scrubbed of personal data first (tempo/privacy.py).
DEFAULT_USERS = frozenset({"collect", "local", "admin"})
ANSWER_JOBS = frozenset({"draft", "fix", "merge", "polish", "combine"})


@dataclass
class SftStats:
    questions: int = 0  # questions looked at
    rows: int = 0
    pairs: int = 0
    skipped_not_passed: int = 0
    skipped_feedback: int = 0
    skipped_terms: int = 0
    skipped_user: int = 0
    skipped_mcp: int = 0  # asked by an AI assistant over MCP (TEMPO_TRAIN_ON_MCP to include)
    with_execution: int = 0  # rows whose answer was run in the sandbox (a reward to train on)
    skipped_no_rejected: int = 0
    unverified_providers: set[str] = field(default_factory=set)
    sources: Counter[str] = field(default_factory=Counter)  # dataset (or tempo-traffic) -> rows
    splits: Counter[str] = field(default_factory=Counter)
    repetition: dict[str, float] = field(default_factory=dict)
    public_share: float = 0.0  # training rows whose question came from a public dataset
    self_share: float = 0.0  # training rows whose answer an earlier Tempo-Core wrote


@dataclass
class _Answer:
    stage: int
    job: str
    model: str
    text: str
    check: dict[str, Any] | None = None


def _answers(stages: list[dict[str, Any]]) -> list[_Answer]:
    """Every answer written for a question, with the check result it got (if any)."""
    answers: list[_Answer] = []
    for stage in stages:
        if stage["job"] in ANSWER_JOBS:
            for out in _loads(stage["outputs"], []) or []:
                if out.get("text"):
                    answers.append(_Answer(stage["idx"], stage["job"], out["model"], out["text"]))
    for stage in stages:
        if stage["job"] != "check":
            continue
        for result in _loads(stage["check_result"], []) or []:
            for answer in answers:
                if answer.model == result.get("model") and answer.stage == result.get("stage"):
                    answer.check = result
    return answers


def _prompt(question: dict[str, Any]) -> list[dict[str, Any]]:
    """The conversation up to the final question, without system prompts (Tempo adds its own
    when training and serving)."""
    messages = _loads(question["messages"], []) or []
    return [
        {"role": m.get("role"), "content": m.get("content")}
        for m in messages
        if m.get("role") in ("user", "assistant")
    ]


def repetition(texts: list[str]) -> dict[str, float]:
    """How repetitive a set of answers is (the model-collapse check compares versions):
    ``distinct_2`` is the share of distinct word pairs (lower = more repetitive), and
    ``repeated_4`` the share of 4-word sequences seen more than once within an answer."""
    pairs: Counter[tuple[str, str]] = Counter()
    repeated = total4 = 0
    for text in texts:
        words = re.findall(r"\w+", text.lower())
        pairs.update(zip(words, words[1:], strict=False))
        grams = Counter(tuple(words[i : i + 4]) for i in range(len(words) - 3))
        total4 += sum(grams.values())
        repeated += sum(n for n in grams.values() if n > 1)
    all_pairs = sum(pairs.values())
    return {
        "distinct_2": round(len(pairs) / all_pairs, 4) if all_pairs else 1.0,
        "repeated_4": round(repeated / total4, 4) if total4 else 0.0,
    }


def build(
    store: Store,
    registry: Registry | None,
    *,
    test_percent: int = 10,
    users: frozenset[str] | set[str] = DEFAULT_USERS,
    unverified: frozenset[str] | set[str] = frozenset(),
    include_mcp: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], SftStats]:
    """(SFT rows, preference pairs, stats) from the question log."""
    stats = SftStats(unverified_providers=set(unverified))
    questions = store.query(
        "SELECT * FROM questions WHERE error IS NULL AND final_answer IS NOT NULL "
        "AND COALESCE(cache_hit, 0) = 0 ORDER BY created_at"
    )
    stages_by: dict[str, list[dict[str, Any]]] = {}
    for stage in store.query("SELECT * FROM stages ORDER BY question_id, idx, id"):
        stages_by.setdefault(stage["question_id"], []).append(stage)
    calls: dict[str, dict[str, tuple[str | None, str | None]]] = {}
    for call in store.query(
        "SELECT question_id, model, licence, training_verdict FROM calls WHERE status = 'ok'"
    ):
        calls.setdefault(call["question_id"], {})[call["model"]] = (
            call["licence"],
            call["training_verdict"],
        )
    origins = store.collect_sources()
    # Sandbox runs per answer (tempo/execute.py): the reward for code and maths answers.
    runs: dict[tuple[str, int, str], dict[str, Any]] = {}
    for run in store.query("SELECT * FROM executions ORDER BY id"):
        runs[(run["question_id"], run["stage"], run["model"])] = {
            "kind": run["kind"],
            "language": run["language"],
            "status": run["status"],
            "reward": run["reward"],
            "tests_passed": run["tests_passed"],
            "tests_total": run["tests_total"],
            "method": run["method"],
        }

    sft: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []
    for q in questions:
        stats.questions += 1
        qid = q["id"]
        if q["user_id"] not in users:
            stats.skipped_user += 1
            continue
        if q["source"] == "mcp" and not include_mcp:
            stats.skipped_mcp += 1
            continue
        if q["stop_reason"] != "passed":
            stats.skipped_not_passed += 1
            continue
        if (q["feedback"] or 0) < 0:
            stats.skipped_feedback += 1
            continue
        answers = _answers(stages_by.get(qid, []))
        chosen = next(
            (
                a
                for a in reversed(answers)
                if a.model == q["final_model"] and a.text == q["final_answer"]
            ),
            None,
        )
        if chosen is None or not (chosen.check and chosen.check.get("passed")):
            stats.skipped_not_passed += 1
            continue
        recorded = calls.get(qid, {})
        # Every model that wrote or graded anything for this question shaped the answer.
        shaping = set(recorded) | {a.model for a in answers}
        terms = {
            m: {
                "verdict": _terms(registry, m, recorded.get(m, (None, None))[1], unverified),
                "licence": recorded.get(m, (None, None))[0] or _licence(registry, m),
            }
            for m in sorted(shaping)
        }
        if registry is not None and any(t["verdict"] != "yes" for t in terms.values()):
            stats.skipped_terms += 1
            continue
        source = dataset_info(origins.get(qid)) or dict(OWN_TRAFFIC)
        # Datasets kept out of training (Dolly, CC-BY-SA) only ever go to the test split.
        split = _split(qid, test_percent) if source.get("training", True) else "test"
        own = source["dataset"] == OWN_TRAFFIC["dataset"]
        prompt = scrub_value(_prompt(q)) if own else _prompt(q)
        chosen_text = scrub(chosen.text) if own else chosen.text
        common = {
            "question_id": qid,
            "split": split,
            "source": source,
            "output_terms": terms,
            "judge_model": chosen.check.get("judge_model"),
            "feedback": q["feedback"],
        }
        sft.append(
            {
                "id": f"{qid}_sft",
                "messages": [*prompt, {"role": "assistant", "content": chosen_text}],
                "answer_model": chosen.model,
                "score": chosen.check.get("score"),
                "execution": runs.get((qid, chosen.stage, chosen.model)),
                **common,
            }
        )
        stats.rows += 1
        if sft[-1]["execution"]:
            stats.with_execution += 1
        stats.sources[source["dataset"]] += 1
        stats.splits[split] += 1

        failed = [
            a
            for a in answers
            if a is not chosen
            and a.check is not None
            and not a.check.get("passed")
            and a.text.strip() != chosen.text.strip()
        ]
        if not failed:
            stats.skipped_no_rejected += 1
            continue
        rejected = min(
            failed, key=lambda a: (not a.check.get("hard_fail"), a.check.get("score") or 0.0)
        )
        pairs.append(
            {
                "id": f"{qid}_pair",
                "prompt": prompt,
                "chosen": [{"role": "assistant", "content": chosen_text}],
                "rejected": [
                    {"role": "assistant", "content": scrub(rejected.text) if own else rejected.text}
                ],
                "chosen_model": chosen.model,
                "rejected_model": rejected.model,
                "chosen_score": chosen.check.get("score"),
                "rejected_score": rejected.check.get("score"),
                "rejected_issues": rejected.check.get("issues") or [],
                "chosen_execution": runs.get((qid, chosen.stage, chosen.model)),
                "rejected_execution": runs.get((qid, rejected.stage, rejected.model)),
                **common,
            }
        )
        stats.pairs += 1
    stats.repetition = repetition([r["messages"][-1]["content"] for r in sft])
    train = [r for r in sft if r["split"] == "train"]
    if train:
        stats.public_share = round(
            sum(r["source"]["dataset"] != OWN_TRAFFIC["dataset"] for r in train) / len(train), 3
        )
        stats.self_share = round(
            sum("tempo-core" in (r["answer_model"] or "") for r in train) / len(train), 3
        )
    return sft, pairs, stats


def mix_warnings(stats: SftStats, min_public: float, max_self: float) -> list[str]:
    """The data-mix settings (TEMPO_MIN_PUBLIC_SHARE, TEMPO_MAX_SELF_SHARE) as warnings: an
    export is still written, but training on it should wait until the mix is right."""
    notes = []
    if stats.rows and stats.public_share < min_public:
        notes.append(
            f"public or human data is {stats.public_share:.0%} of training rows, below "
            f"{min_public:.0%}: add public data (tempo-server collect) before training"
        )
    if stats.self_share > max_self:
        notes.append(
            f"answers written by an earlier Tempo-Core are {stats.self_share:.0%} of training "
            f"rows, above {max_self:.0%}: keep more answers from other models"
        )
    return notes


README = """# Tempo {kind} dataset

Exported by `tempo export-{kind}` on {date}. Nothing in it was trained yet.

- `train.jsonl`: {train} rows · `test.jsonl`: {test} rows (split by question, the same split as
  every Tempo export, so the test questions stay held out for the promotion gate)
- Format: {format}
- Only "yes" rows: every model that wrote or graded text for a question is marked
  `training_on_outputs: yes` (or is a local Apache-2.0/MIT model), and its terms were checked
  just before this export where they rest on a provider's terms. Each row lists them in
  `output_terms` (verdict and licence per model) and its question's origin in `source`.
- Answers passed Tempo's checks (heuristics plus a judge from another model family); answers
  with a 👎 are left out.
- Code and maths answers that were run in the sandbox carry the result in `execution`
  (`chosen_execution` / `rejected_execution` in pairs): `status` passed, failed or
  inconclusive, and `reward` (tests passed / tests run for code with tests, 1 or 0 otherwise,
  null when nothing could be checked), usable as a reward for reinforcement learning. Rows with
  one: {with_execution}.
- Repetition of the answers (compare between versions to catch model collapse):
  distinct word pairs {distinct_2}, repeated 4-word sequences {repeated_4}.
- Data mix of the training rows: {public_share} from public datasets (target at least
  {min_public}), {self_share} written by an earlier Tempo-Core (at most {max_self}).
- Personal data (emails, phone numbers, card, Aadhaar and PAN numbers, IP addresses) is
  replaced by placeholders in text from Tempo's own traffic. Other users' questions appear only
  if they opted in.
- Dolly (CC-BY-SA) rows are in `test.jsonl` only, never in training.

## Where the questions came from

{sources}

Rows from CC-BY-SA datasets (Dolly) must be shared under the same licence if you publish the
dataset or a model trained on it.
"""


def _write(rows: list[dict[str, Any]], out_dir: Path) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {"train": 0, "test": 0}
    for split in counts:
        with (out_dir / f"{split}.jsonl").open("w", encoding="utf-8") as handle:
            for row in rows:
                if row["split"] == split:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                    counts[split] += 1
    return counts


def _sources(rows: list[dict[str, Any]]) -> str:
    seen: dict[str, dict[str, Any]] = {}
    counts: Counter[str] = Counter()
    for row in rows:
        seen[row["source"]["dataset"]] = row["source"]
        counts[row["source"]["dataset"]] += 1
    if not seen:
        return "No rows yet."
    return "\n".join(
        f"- {name}: {counts[name]} rows, licence {src.get('license')}"
        + (f" ({src.get('license_url')})" if src.get("license_url") else "")
        for name, src in sorted(seen.items())
    )


def export(
    store: Store,
    registry: Registry | None,
    out_dir: Path,
    kind: str,
    *,
    test_percent: int = 10,
    users: frozenset[str] | set[str] = DEFAULT_USERS,
    unverified: frozenset[str] | set[str] = frozenset(),
    min_public: float = 0.3,
    max_self: float = 0.3,
    include_mcp: bool = False,
) -> SftStats:
    """Write ``kind`` ("sft" or "pairs") rows to ``out_dir`` with a README."""
    import time

    sft, pairs, stats = build(
        store,
        registry,
        test_percent=test_percent,
        users=users,
        unverified=unverified,
        include_mcp=include_mcp,
    )
    rows = sft if kind == "sft" else pairs
    counts = _write(rows, out_dir)
    formats = {
        "sft": "chat `messages` (user/assistant turns ending with the checked answer), as TRL's "
        "SFTTrainer and Unsloth read them",
        "pairs": "`prompt`, `chosen`, `rejected` (conversational), as TRL's DPOTrainer reads them",
    }
    (out_dir / "README.md").write_text(
        README.format(
            kind=kind,
            date=time.strftime("%Y-%m-%d"),
            train=counts["train"],
            test=counts["test"],
            format=formats[kind],
            distinct_2=stats.repetition.get("distinct_2"),
            repeated_4=stats.repetition.get("repeated_4"),
            public_share=f"{stats.public_share:.0%}",
            self_share=f"{stats.self_share:.0%}",
            min_public=f"{min_public:.0%}",
            max_self=f"{max_self:.0%}",
            with_execution=stats.with_execution,
            sources=_sources(rows),
        ),
        encoding="utf-8",
    )
    return stats
