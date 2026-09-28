"""Turn Tempo's logs into a Laya fine-tuning dataset, and compare Laya with the rules.

Every logged decision point (plan before stage 1, assess after a check, pick before a stage)
becomes one row in the format of ``LocalLLaMA/typed-decisions``, which Laya's official
notebook (notebooks/laya_finetune_typed_decisions_2xT4_kaggle.ipynb) trains on:

    id, workflow, split, state, questions, gold, factors, label_agreement, n_questions

``state``, ``questions`` and ``gold`` are JSON strings; ``state`` and ``questions`` are exactly
what Tempo sent (or would have sent) to Laya. Gold labels come from what happened, never from
the rules' own decision, so `compare` is not circular:

    difficulty / strategy / stage_budget  how the question went: did the first draft pass,
                                          how many improvements it needed
    quality        the LLM judge's grade (distillation: Laya learns to grade like the judge),
                   moved by the user's thumbs up/down on the final answer
    should_stop    whether later stages still improved the answer
    next_model     the best-judged model among several that answered, or the model that
                   finally passed
    task_type      the analyzer's label (exported, but not used to compare: no independent truth)
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tempo.checks import PASS_THRESHOLD
from tempo.laya_decider import ASSESS_QUESTIONS, PLAN_QUESTIONS, budget_level
from tempo.registry import Registry
from tempo.store import Store

IMPROVING_JOBS = {"fix", "merge", "polish", "draft"}
PICK_JOBS = {"draft", "fix", "merge", "polish", "combine", "parts"}
COMPARED = ("difficulty", "strategy", "stage_budget", "quality", "should_stop", "next_model")


def _loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _smooth(label_index: int, size: int, peak: float = 0.85) -> list[float]:
    if size == 1:
        return [1.0]
    rest = (1 - peak) / (size - 1)
    return [peak if i == label_index else rest for i in range(size)]


def _soft_level(value: float, size: int) -> list[float]:
    """A continuous level (e.g. 2.8 of 0..4) split between its two nearest levels."""
    value = max(0.0, min(size - 1.0, value))
    low = math.floor(value)
    high = min(size - 1, low + 1)
    probs = [0.0] * size
    probs[low] += 1 - (value - low)
    probs[high] += value - low
    return probs


def choice_gold(label: str, keys: list[str]) -> dict[str, Any]:
    probs = _smooth(keys.index(label), len(keys))
    return {
        "type": "choice",
        "label": label,
        "probabilities": {k: round(p, 6) for k, p in zip(keys, probs, strict=True)},
        "confidence": round(max(probs), 6),
    }


def score_gold(level: float, size: int) -> dict[str, Any]:
    probs = _soft_level(level, size) if level != int(level) else _smooth(int(level), size)
    label = max(range(size), key=lambda i: probs[i])
    return {
        "type": "score",
        "label": str(label),
        "probabilities": {str(i): round(p, 6) for i, p in enumerate(probs)},
        "score": round(sum(i * p for i, p in enumerate(probs)), 6),
        "confidence": round(max(probs), 6),
    }


@dataclass
class Outcome:
    """What happened to one question, reconstructed from its stages."""

    passed: bool = False
    first_check_passed: bool = False
    improvements_before_pass: int = 0
    stages_used: int = 0
    strategy: str | None = None
    checks: list[dict[str, Any]] = field(default_factory=list)  # per check stage
    stage_models: dict[int, list[str]] = field(default_factory=dict)
    judged: dict[int, dict[str, float]] = field(default_factory=dict)  # stage -> model -> score
    feedback: int | None = None
    final_stage: int | None = None
    threshold: float = 0.7


def outcome_of(question: dict[str, Any], stages: list[dict[str, Any]]) -> Outcome:
    out = Outcome(
        stages_used=question.get("stages_used") or 0,
        feedback=question.get("feedback"),
        threshold=PASS_THRESHOLD.get(question.get("mode") or "auto", 0.7),
    )
    plan = _loads(question.get("plan"), {}) or {}
    out.strategy = plan.get("strategy")
    improvements = 0
    seen_check = False
    for stage in sorted(stages, key=lambda s: s["idx"]):
        job = stage["job"]
        models = [o["model"] for o in _loads(stage["outputs"], []) or []]
        out.stage_models[stage["idx"]] = models
        if job == "check":
            results = _loads(stage["check_result"], []) or []
            best = max((r.get("score") or 0.0 for r in results), default=0.0)
            passed = any(r.get("passed") for r in results)
            judged = any(r.get("judge_score") is not None for r in results)
            out.checks.append(
                {"stage": stage["idx"], "best": best, "passed": passed, "judged": judged}
            )
            for result in results:
                if result.get("judge_score") is not None:
                    by_model = out.judged.setdefault(result["stage"], {})
                    by_model[result["model"]] = max(
                        by_model.get(result["model"], 0.0), result.get("score") or 0.0
                    )
            if not seen_check:
                out.first_check_passed = passed
                seen_check = True
            if passed and not out.passed:
                out.passed = True
                out.improvements_before_pass = improvements
        elif seen_check and job in IMPROVING_JOBS and not out.passed:
            improvements += 1
        if models:
            out.final_stage = stage["idx"]
    return out


# --- gold labels per decision ----------------------------------------------------------------


def gold_for(
    name: str,
    decision: dict[str, Any],
    question: dict[str, Any],
    outcome: Outcome,
    registry: Registry | None,
) -> tuple[dict[str, Any], str] | None:
    """(gold answer, source) for one decision, or None when the outcome does not say."""
    stage = decision["stage"]
    context = _loads(decision["context"], {}) or {}
    if name == "task_type":
        value = _loads(decision["final_value"])
        keys = list(PLAN_QUESTIONS["task_type"]["criteria"])
        return (choice_gold(value, keys), "analyzer") if value in keys else None
    if name == "difficulty":
        if not outcome.checks:
            return None
        if outcome.first_check_passed:
            level = 0 if (question.get("stages_used") or 0) <= 2 else 1
        elif outcome.passed:
            level = 2 if outcome.improvements_before_pass <= 1 else 3
        else:
            level = 3
        return score_gold(level, 4), "outcome"
    if name == "strategy":
        if not outcome.passed:
            return None
        keys = list(PLAN_QUESTIONS["strategy"]["criteria"])
        if outcome.strategy == "decompose":
            label = "decompose"
        elif outcome.first_check_passed:
            label = "single"
        elif outcome.improvements_before_pass <= 1 and outcome.strategy != "mixture":
            label = "cascade"
        else:
            label = "mixture"
        return choice_gold(label, keys), "outcome"
    if name == "stage_budget":
        if not outcome.passed:
            return None
        return score_gold(budget_level(outcome.stages_used), 4), "outcome"
    if name == "quality":
        check = next((c for c in outcome.checks if c["stage"] == stage), None)
        if check is None or not check["judged"]:
            return None
        level = check["best"] * 4
        if outcome.feedback is not None and stage == outcome.checks[-1]["stage"]:
            level = max(level, 3.0) if outcome.feedback > 0 else min(level, 1.0)
        return score_gold(level, 5), "judge" if outcome.feedback is None else "judge+feedback"
    if name == "should_stop":
        check = next((c for c in outcome.checks if c["stage"] == stage), None)
        if check is None or not check["judged"]:
            return None
        later = [c["best"] for c in outcome.checks if c["stage"] > stage]
        improved_later = bool(later) and max(later) > check["best"] + 0.1
        stop = check["passed"] and not improved_later
        if outcome.feedback is not None and stage == outcome.checks[-1]["stage"]:
            stop = outcome.feedback > 0
        keys = list(ASSESS_QUESTIONS["should_stop"]["criteria"])
        return choice_gold("A" if stop else "B", keys), "outcome"
    if name == "next_model":
        mapping: dict[str, str] = context.get("mapping") or {}
        if context.get("job") not in PICK_JOBS or not mapping:
            return None
        by_model = {m: letter for letter, m in mapping.items()}
        judged = {m: s for m, s in outcome.judged.get(stage, {}).items() if m in by_model}
        if len(judged) >= 2:  # several shortlisted models answered and were judged
            best = max(judged, key=judged.get)
            return choice_gold(by_model[best], list(mapping)), "compared"
        if len(judged) == 1:
            model, score = next(iter(judged.items()))
            if score >= outcome.threshold:
                return choice_gold(by_model[model], list(mapping)), "outcome"
            for later_stage in sorted(s for s in outcome.judged if s > stage):
                for later_model, later_score in outcome.judged[later_stage].items():
                    if later_score >= outcome.threshold and later_model in by_model:
                        return choice_gold(by_model[later_model], list(mapping)), "hindsight"
        return None
    return None


def _value_of(name: str, raw: Any, context: dict[str, Any]) -> Any:
    """Normalize a logged value to the gold label's form (for comparison)."""
    if raw is None:
        return None
    if name == "stage_budget":
        return str(budget_level(int(raw)))
    if name in ("difficulty", "quality"):
        return str(int(raw))
    if name == "should_stop":
        return "A" if raw else "B"
    if name == "next_model":
        by_model = {m: letter for letter, m in (context.get("mapping") or {}).items()}
        return by_model.get(raw)
    return raw


def baseline_of(name: str, decision: dict[str, Any], context: dict[str, Any]) -> Any:
    """The rules' answer to compare with Laya. Quality and stopping use the heuristics-only
    check, since the full check already includes the judge the gold comes from."""
    if name == "quality":
        return str(context["heuristic_level"]) if "heuristic_level" in context else None
    if name == "should_stop":
        if "heuristic_passed" not in context:
            return None
        return "A" if context["heuristic_passed"] else "B"
    return _value_of(name, _loads(decision["rules_value"]), context)


# --- building the dataset ---------------------------------------------------------------------


def _split(question_id: str, test_percent: int) -> str:
    bucket = int(hashlib.sha1(question_id.encode()).hexdigest()[:8], 16) % 100
    return "test" if bucket < test_percent else "train"


@dataclass
class ExportStats:
    questions: int = 0
    rows: int = 0
    decisions: int = 0
    by_workflow: dict[str, int] = field(default_factory=dict)
    skipped_terms: int = 0
    unknown_terms_providers: set[str] = field(default_factory=set)


def _terms(registry: Registry | None, model_id: str | None) -> str:
    if registry is None or not model_id:
        return "allowed"
    model = registry.get(model_id)
    provider_id = model.provider if model else model_id.split("/")[0]
    provider = registry.providers.get(provider_id)
    return provider.training_on_outputs if provider else "unknown"


def build_rows(
    store: Store,
    registry: Registry | None = None,
    *,
    test_percent: int = 10,
    strict: bool = False,
) -> tuple[list[dict[str, Any]], ExportStats]:
    stats = ExportStats()
    questions = {
        q["id"]: q
        for q in store.query(
            "SELECT * FROM questions WHERE error IS NULL AND final_answer IS NOT NULL"
        )
    }
    decisions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in store.query("SELECT * FROM decisions ORDER BY id"):
        if d["question_id"] in questions:
            decisions[d["question_id"]].append(d)
    stages: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in store.query("SELECT * FROM stages ORDER BY id"):
        if s["question_id"] in questions:
            stages[s["question_id"]].append(s)

    rows: list[dict[str, Any]] = []
    for question_id, question_decisions in decisions.items():
        question = questions[question_id]
        outcome = outcome_of(question, stages[question_id])
        groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        for d in question_decisions:
            context = _loads(d["context"], {}) or {}
            group = {"next_model": "pick", "quality": "assess", "should_stop": "assess"}.get(
                d["name"], "plan"
            )
            groups[(group, d["stage"], context.get("job"))].append(d)
        used = False
        for (group, stage, job), items in groups.items():
            state = _loads(items[0]["laya_state"])
            if state is None:
                continue
            q_defs: dict[str, Any] = {}
            gold: dict[str, Any] = {}
            factors: dict[str, Any] = {
                "question_id": question_id,
                "stage": stage,
                "job": job,
                "feedback": outcome.feedback,
                "decisions": {},
            }
            sources = []
            for d in items:
                context = _loads(d["context"], {}) or {}
                if group == "assess":
                    sources += [context.get("answer_model"), context.get("judge_model")]
                labelled = gold_for(d["name"], d, question, outcome, registry)
                if labelled is None:
                    continue
                answer, source = labelled
                q_defs[d["name"]] = _loads(d["laya_question"])
                gold[d["name"]] = answer
                factors["decisions"][d["name"]] = {
                    "gold_source": source,
                    "rules": _value_of(d["name"], _loads(d["rules_value"]), context),
                    "baseline": baseline_of(d["name"], d, context),
                    "laya": _value_of(d["name"], _loads(d["laya_value"]), context),
                    "laya_status": d["laya_status"],
                    "laya_confidence": d["laya_confidence"],
                }
            if not gold:
                continue
            verdicts = {_terms(registry, m) for m in sources if m}
            if "disallowed" in verdicts or (strict and "unknown" in verdicts):
                stats.skipped_terms += 1
                continue
            for model_id in sources:
                if model_id and _terms(registry, model_id) == "unknown":
                    stats.unknown_terms_providers.add(model_id.split("/")[0])
            factors["output_models"] = sorted({m for m in sources if m})
            row_id = f"{question_id}_{group}_{stage}" + (f"_{job}" if job else "")
            workflow = f"tempo_{group}"
            rows.append(
                {
                    "id": row_id,
                    "workflow": workflow,
                    "split": _split(question_id, test_percent),
                    "state": json.dumps(state, ensure_ascii=False),
                    "questions": json.dumps(q_defs, ensure_ascii=False),
                    "gold": json.dumps(gold, ensure_ascii=False),
                    "factors": json.dumps(factors, ensure_ascii=False),
                    "label_agreement": "{}",
                    "n_questions": len(gold),
                }
            )
            stats.rows += 1
            stats.decisions += len(gold)
            stats.by_workflow[workflow] = stats.by_workflow.get(workflow, 0) + 1
            used = True
        stats.questions += int(used)
    return rows, stats


NOTEBOOK_README = """# Tempo decisions for fine-tuning Laya

Exported by `tempo export-laya`. Same columns as `LocalLLaMA/typed-decisions`, which the official
notebook `notebooks/laya_finetune_typed_decisions_2xT4_kaggle.ipynb` trains on.

- `train.jsonl`: {train} rows · `test.jsonl`: {test} rows (split by question, so no leakage)
- workflows: {workflows}
- decisions labelled: {decisions}

## Use with the notebook

Upload this folder to Kaggle (Add data -> Upload), then in section 3 replace

    ds_train = load_dataset("LocalLLaMA/typed-decisions", "all", split="train")

with

    ds_train = load_dataset("json", data_files="/kaggle/input/<your-dataset>/train.jsonl",
                            split="train")

and in section 6 load `test.jsonl` the same way. The per-workflow report in section 9 lists the
typed-decisions workflows; change that list to {workflow_list}.

Check Laya's head budget for choice questions: Tempo keeps shortlists at 10 options or fewer.

## Where the labels come from

`factors.decisions.<name>.gold_source` says how each label was made: `outcome` (how the
question went), `judge` / `judge+feedback` (the LLM judge's grade, moved by thumbs up/down),
`compared` (several models were judged on the same stage), `hindsight` (a later model passed),
`analyzer` (task type; not an independent truth).

## Terms of use

Rows whose text was written by a provider marked `training_on_outputs: disallowed` in the
registry were left out{strict_note}. Providers still marked `unknown`: {unknown}.
Check their terms before training on this data.
"""


def export(
    store: Store,
    registry: Registry | None,
    out_dir: Path,
    *,
    test_percent: int = 10,
    strict: bool = False,
) -> ExportStats:
    rows, stats = build_rows(store, registry, test_percent=test_percent, strict=strict)
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {"train": 0, "test": 0}
    for split in ("train", "test"):
        with (out_dir / f"{split}.jsonl").open("w", encoding="utf-8") as handle:
            for row in rows:
                if row["split"] == split:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                    counts[split] += 1
    workflows = sorted(stats.by_workflow)
    (out_dir / "README.md").write_text(
        NOTEBOOK_README.format(
            train=counts["train"],
            test=counts["test"],
            workflows=", ".join(f"{w} ({stats.by_workflow[w]})" for w in workflows) or "none",
            decisions=stats.decisions,
            workflow_list=workflows,
            strict_note=" (and, with --strict, those marked unknown)" if strict else "",
            unknown=", ".join(sorted(stats.unknown_terms_providers)) or "none",
        ),
        encoding="utf-8",
    )
    return stats


# --- Laya vs rules on held-out rows -----------------------------------------------------------


@dataclass
class Comparison:
    decision: str
    n: int
    laya_accuracy: float
    rules_accuracy: float

    @property
    def laya_wins(self) -> bool:
        return self.n > 0 and self.laya_accuracy > self.rules_accuracy


def compare(
    store: Store,
    registry: Registry | None = None,
    *,
    test_percent: int = 10,
    save: bool = True,
) -> list[Comparison]:
    """Accuracy of Laya and of the rules against gold, on the held-out split only."""
    rows, _ = build_rows(store, registry, test_percent=test_percent)
    tallies: dict[str, list[int]] = {name: [0, 0, 0] for name in COMPARED}  # n, laya, rules
    for row in rows:
        if row["split"] != "test":
            continue
        gold = json.loads(row["gold"])
        for name, info in json.loads(row["factors"])["decisions"].items():
            if name not in tallies or info["gold_source"] == "analyzer":
                continue
            if info["laya_status"] not in ("ok", "late") or info["laya"] is None:
                continue
            if info["baseline"] is None:
                continue
            label = gold[name]["label"]
            tally = tallies[name]
            tally[0] += 1
            tally[1] += int(info["laya"] == label)
            tally[2] += int(info["baseline"] == label)
    results = []
    for name, (n, laya_right, rules_right) in tallies.items():
        result = Comparison(name, n, laya_right / n if n else 0.0, rules_right / n if n else 0.0)
        results.append(result)
        if save and n:
            store.save_laya_compare(name, n, result.laya_accuracy, result.rules_accuracy)
    return results
