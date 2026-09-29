"""Close the training loop: pack the data for Kaggle, import what comes back, compare old and new
on held-out questions, and promote per task type.

- ``prepare``: runs the three exports (Laya decisions, SFT rows, DPO pairs) into one folder,
  checks the data mix and every training row's licence, writes a manifest, adds the training
  kit (``tempo/trainkit.py``) and the two notebooks, and packs one zip to upload to Kaggle.
- ``import_model`` / ``compare`` / ``promote``: see tempo/model_import.py and tempo/gate.py.

Nothing here needs a GPU or the training libraries; the training itself happens in the
notebooks (docs/TRAINING.md).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import zipfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tempo import __version__

if TYPE_CHECKING:
    from tempo.engine import Engine

PACK_MANIFEST = "tempo-pack.json"
PACK_FORMAT = 1
KIT_NAME = "tempo_trainkit.py"
NOTEBOOKS = ("tempo_core.ipynb", "tempo_router_judge.ipynb")
# Below these the run still works, but the result is unlikely to beat the old version
# (docs/TEMPO_MODELS.md §6).
RECOMMENDED = {"sft": 2000, "pairs": 1000, "laya_decisions": 3000}
MIN_HELDOUT_PER_TYPE = 30  # the promotion gate's minimum (owner's decision, step 4)
# Licences that may not be in a training split: share-alike and non-commercial.
_BLOCKED_LICENCE_MARKS = ("-SA", "SA-", "NC", "NONCOMMERCIAL")


@dataclass
class Finding:
    level: str  # "error" (blocks packing), "mix" (blocks unless --force), "warning"
    text: str


@dataclass
class PackReport:
    folder: Path
    zip_path: Path | None
    manifest: dict[str, Any]
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def mix_problems(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "mix"]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _blocked_licence(licence: str | None) -> bool:
    upper = (licence or "").upper()
    return any(mark in upper for mark in _BLOCKED_LICENCE_MARKS)


def check_rows(kind: str, rows: list[dict[str, Any]]) -> tuple[list[Finding], dict[str, Any]]:
    """Licence checks on one export's training rows (rule 6): every row has a source with a
    licence, no share-alike or non-commercial source, and every model that wrote or graded its
    text is "yes". Returns findings and a licence summary."""
    findings: list[Finding] = []
    sources: Counter[str] = Counter()
    source_licences: dict[str, str] = {}
    model_licences: Counter[str] = Counter()
    problems: Counter[str] = Counter()
    for row in rows:
        if row.get("split") != "train":
            continue
        factors = row.get("factors")
        if isinstance(factors, str):  # Laya rows keep their details in a JSON string
            factors = json.loads(factors)
        meta = factors if isinstance(factors, dict) else row
        source = meta.get("source") or {}
        name = source.get("dataset") or "?"
        sources[name] += 1
        source_licences[name] = source.get("license") or "?"
        if not source.get("license"):
            problems["a training row has no source licence"] += 1
        elif source.get("training") is False or _blocked_licence(source.get("license")):
            problems[f"{name} ({source.get('license')}) may not be in a training split"] += 1
        for model, terms in (meta.get("output_terms") or {}).items():
            model_licences[terms.get("licence") or "licence unknown"] += 1
            if terms.get("verdict") != "yes":
                problems[f"text from {model} is not 'yes' ({terms.get('verdict')})"] += 1
    for text, count in problems.items():
        findings.append(Finding("error", f"{kind}: {text} ({count} rows)"))
    summary = {
        "sources": {
            name: {"rows": count, "license": source_licences[name]}
            for name, count in sorted(sources.items())
        },
        "answer_model_licences": dict(sorted(model_licences.items())),
    }
    return findings, summary


def _splits(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(row.get("split") for row in rows)
    return {"train": counts.get("train", 0), "test": counts.get("test", 0)}


def heldout_task_types(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(
        sorted(Counter(r.get("task_type") or "?" for r in rows if r["split"] == "test").items())
    )


def pack_findings(
    counts: dict[str, dict[str, int]],
    mix: dict[str, float],
    min_public: float,
    max_self: float,
    heldout: dict[str, int],
    laya_decisions: int,
) -> list[Finding]:
    """Size, data-mix and held-out checks (licence checks are in ``check_rows``)."""
    findings: list[Finding] = []
    if not counts["sft"]["train"] and not counts["laya"]["train"]:
        findings.append(
            Finding(
                "error",
                "no training rows at all: collect first (tempo-server collect --yes-only), "
                "then run this again",
            )
        )
    if counts["sft"]["train"] and mix["public_share"] < min_public:
        findings.append(
            Finding(
                "mix",
                f"public or human data is {mix['public_share']:.0%} of the SFT training rows, "
                f"below TEMPO_MIN_PUBLIC_SHARE={min_public:.0%}: collect more public questions",
            )
        )
    if mix["self_share"] > max_self:
        findings.append(
            Finding(
                "mix",
                f"answers written by an earlier Tempo-Core are {mix['self_share']:.0%} of the "
                f"SFT training rows, above TEMPO_MAX_SELF_SHARE={max_self:.0%}",
            )
        )
    for kind, key in (("sft", "sft"), ("pairs", "pairs")):
        have = counts[kind]["train"]
        if have and have < RECOMMENDED[key]:
            findings.append(
                Finding(
                    "warning",
                    f"{kind}: {have} training rows; {RECOMMENDED[key]}+ are recommended before "
                    "the result can beat the old version",
                )
            )
    if not counts["pairs"]["train"] and counts["sft"]["train"]:
        findings.append(
            Finding("warning", "pairs: none, so the Tempo-Core notebook skips DPO (SFT only)")
        )
    if laya_decisions and laya_decisions < RECOMMENDED["laya_decisions"]:
        findings.append(
            Finding(
                "warning",
                f"laya: {laya_decisions} labelled decisions; about "
                f"{RECOMMENDED['laya_decisions']}+ are recommended",
            )
        )
    small = {t: n for t, n in heldout.items() if n < MIN_HELDOUT_PER_TYPE}
    if counts["sft"]["test"] and small:
        listed = ", ".join(f"{t} {n}" for t, n in small.items())
        findings.append(
            Finding(
                "warning",
                f"held-out questions per task type below {MIN_HELDOUT_PER_TYPE} ({listed}): the "
                "promotion gate keeps the old version for those types",
            )
        )
    return findings


def kit_source() -> Path:
    return Path(__file__).with_name("trainkit.py")


def write_notebooks(folder: Path) -> list[Path]:
    from tempo.notebooks import write_all

    return write_all(folder)


UPLOAD_STEPS = """\
Upload to Kaggle (free account; GPUs and Internet need a phone-verified account):

  1. kaggle.com -> Create -> New Dataset. Drag in {zip}
     Title: tempo-training (any name). Keep it Private. Create.
     (Kaggle unpacks the zip; the notebooks also accept it packed.)
  2. Create -> New Notebook -> File -> Import Notebook -> upload
     {notebooks}/tempo_core.ipynb      (Tempo-Core: SFT, DPO, GGUF)
     and, in a second notebook, {notebooks}/tempo_router_judge.ipynb  (Laya)
  3. In each notebook's right panel: Session options -> Accelerator: GPU T4 x2,
     Internet: On. Add Input -> Datasets -> Your Work -> tempo-training.
  4. Save Version -> Save & Run All (Commit). It runs in the background (at most 12 hours
     per session); you can close the browser.
  5. When the version finishes: open it -> Output -> Download (a zip), then
       tempo-server models import <the downloaded zip>
       tempo-server models compare
       tempo-server models promote
     If REPORT.md says "incomplete" (Kaggle's time limit): Add Input -> Notebook Output ->
     the version that stopped, then Save & Run All again; it resumes from its checkpoints.

docs/TRAINING.md has the whole path, times and what "good enough" looks like."""


def prepare(
    engine: Engine,
    out_dir: Path,
    *,
    test_percent: int = 10,
    users: set[str] | frozenset[str],
    unverified: set[str] | frozenset[str] = frozenset(),
    force: bool = False,
    include_mcp: bool = False,
) -> PackReport:
    """Export, check, and (when the checks pass) pack ``out_dir`` into ``out_dir.zip``."""
    from tempo import sft, tuning

    settings = engine.settings
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    laya_stats = tuning.export(
        engine.store,
        engine.registry,
        out_dir / "laya",
        test_percent=test_percent,
        unverified=unverified,
        users=users,
        include_mcp=include_mcp,
    )
    sft_stats = None
    for kind in ("sft", "pairs"):
        sft_stats = sft.export(
            engine.store,
            engine.registry,
            out_dir / kind,
            kind,
            test_percent=test_percent,
            users=users,
            unverified=unverified,
            min_public=settings.min_public_share,
            max_self=settings.max_self_share,
            include_mcp=include_mcp,
        )
    assert sft_stats is not None
    rows = {kind: _read_jsonl(out_dir / kind / "train.jsonl") for kind in ("laya", "sft", "pairs")}
    tests = {kind: _read_jsonl(out_dir / kind / "test.jsonl") for kind in ("laya", "sft", "pairs")}
    counts = {kind: _splits(rows[kind] + tests[kind]) for kind in rows}
    findings: list[Finding] = []
    licences: dict[str, Any] = {}
    for kind in rows:
        found, licences[kind] = check_rows(kind, rows[kind])
        findings += found
    mix = {"public_share": sft_stats.public_share, "self_share": sft_stats.self_share}
    heldout = heldout_task_types(tests["sft"])
    findings += pack_findings(
        counts,
        mix,
        settings.min_public_share,
        settings.max_self_share,
        heldout,
        laya_stats.decisions,
    )
    shutil.copyfile(kit_source(), out_dir / KIT_NAME)
    manifest: dict[str, Any] = {
        "format": PACK_FORMAT,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tempo_server": __version__,
        "test_percent": test_percent,
        "counts": counts,
        "laya": {"decisions": laya_stats.decisions, "workflows": laya_stats.by_workflow},
        "heldout_task_types": heldout,
        "mix": {
            **mix,
            "min_public_share": settings.min_public_share,
            "max_self_share": settings.max_self_share,
        },
        "repetition": sft_stats.repetition,
        "licences": licences,
        "terms_not_verified": sorted(unverified),
        "findings": [asdict(f) for f in findings],
        "forced": bool(force and any(f.level == "mix" for f in findings)),
        "files": {},
    }
    for path in sorted(out_dir.rglob("*")):
        if path.is_file():
            manifest["files"][path.relative_to(out_dir).as_posix()] = _sha256(path)
    (out_dir / PACK_MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    report = PackReport(out_dir, None, manifest, findings)
    write_notebooks(out_dir.parent / "notebooks")
    if report.errors or (report.mix_problems and not force):
        return report
    zip_path = out_dir.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(out_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(out_dir).as_posix())
    report.zip_path = zip_path
    return report


def upload_steps(report: PackReport) -> str:
    return UPLOAD_STEPS.format(zip=report.zip_path, notebooks=report.folder.parent / "notebooks")


def default_out(engine: Engine) -> Path:
    base = engine.settings.data_dir or Path.cwd()
    return Path(base) / "training" / time.strftime("tempo-training-%Y%m%d-%H%M")


def latest_pack(engine: Engine) -> Path | None:
    """The newest pack folder `prepare` wrote in the data directory."""
    base = engine.settings.data_dir
    if base is None:
        return None
    packs = sorted(
        p.parent for p in (Path(base) / "training").glob(f"*/{PACK_MANIFEST}") if p.is_file()
    )
    return packs[-1] if packs else None
