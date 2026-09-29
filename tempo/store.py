"""SQLite storage: question logs for tuning, quota counters, measured scores, users and keys.

Writes are small and synchronous behind one lock; SQLite in WAL mode handles them in well
under a millisecond, so they are called directly from async code.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    user_id TEXT,
    mode TEXT,
    messages TEXT,
    profile TEXT,
    plan TEXT,
    final_answer TEXT,
    final_model TEXT,
    final_score REAL,
    stop_reason TEXT,
    stages_used INTEGER,
    requests_used INTEGER,
    total_ms INTEGER,
    cache_hit INTEGER DEFAULT 0,
    source TEXT,
    error TEXT,
    feedback INTEGER,
    feedback_comment TEXT,
    feedback_at REAL
);
CREATE TABLE IF NOT EXISTS executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id TEXT NOT NULL,
    stage INTEGER,
    model TEXT,
    kind TEXT,
    language TEXT,
    status TEXT,
    method TEXT,
    tests_total INTEGER,
    tests_passed INTEGER,
    reward REAL,
    error TEXT,
    stopped TEXT,
    computed TEXT,
    claimed TEXT,
    duration_ms INTEGER,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS executions_question ON executions (question_id);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id TEXT NOT NULL,
    stage INTEGER,
    name TEXT NOT NULL,
    rules_value TEXT,
    laya_value TEXT,
    laya_probs TEXT,
    laya_confidence REAL,
    laya_status TEXT,
    laya_ms REAL,
    used TEXT,
    final_value TEXT,
    laya_state TEXT,
    laya_question TEXT,
    context TEXT,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id TEXT NOT NULL,
    idx INTEGER,
    job TEXT,
    reason TEXT,
    models TEXT,
    outputs TEXT,
    check_result TEXT,
    ms INTEGER,
    requests INTEGER,
    quota_left TEXT,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id TEXT,
    stage INTEGER,
    job TEXT,
    model TEXT,
    provider TEXT,
    task TEXT,
    status TEXT,
    error_kind TEXT,
    ms INTEGER,
    ttft_ms INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    score REAL,
    licence TEXT,
    training_verdict TEXT,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS quota_days (
    bucket TEXT NOT NULL,
    day TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (bucket, day)
);
CREATE TABLE IF NOT EXISTS eval_results (
    model TEXT NOT NULL,
    task TEXT NOT NULL,
    n INTEGER NOT NULL,
    score REAL NOT NULL,
    updated_at REAL,
    PRIMARY KEY (model, task)
);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    api_key_hash TEXT UNIQUE NOT NULL,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS training_consent (
    user_id TEXT PRIMARY KEY,
    consent INTEGER NOT NULL DEFAULT 0,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS consent_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    consent INTEGER NOT NULL,
    at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS user_keys (
    user_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    ciphertext BLOB NOT NULL,
    last4 TEXT,
    verified INTEGER,
    created_at REAL,
    PRIMARY KEY (user_id, provider)
);
CREATE TABLE IF NOT EXISTS laya_compare (
    decision TEXT PRIMARY KEY,
    n INTEGER,
    laya_accuracy REAL,
    rules_accuracy REAL,
    updated_at REAL,
    laya_model TEXT
);
CREATE TABLE IF NOT EXISTS collect_items (
    dataset TEXT NOT NULL,
    item_id TEXT NOT NULL,
    status TEXT NOT NULL,
    question_id TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    updated_at REAL,
    PRIMARY KEY (dataset, item_id)
);
CREATE INDEX IF NOT EXISTS idx_decisions_question ON decisions (question_id);
CREATE INDEX IF NOT EXISTS idx_stages_question ON stages (question_id);
CREATE INDEX IF NOT EXISTS idx_calls_question ON calls (question_id);
CREATE INDEX IF NOT EXISTS idx_calls_model ON calls (model, task);
CREATE INDEX IF NOT EXISTS idx_questions_created ON questions (created_at);
"""


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, default=str)


_COLUMNS: dict[str, set[str]] = {}


def _encode(table: str, fields: dict[str, Any]) -> dict[str, Any]:
    """JSON-encode dict/list values and refuse column names the table does not have."""
    unknown = set(fields) - _COLUMNS[table]
    if unknown:
        raise ValueError(f"Unknown {table} columns: {sorted(unknown)}")
    return {k: (_json(v) if isinstance(v, (dict, list)) else v) for k, v in fields.items()}


class Store:
    def __init__(self, path: Path | str | None = None) -> None:
        if path is None:
            target = ":memory:"
        else:
            Path(path).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target = str(path)
        self._conn = sqlite3.connect(target, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            if target != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            # Databases created before a column existed get it added.
            for table, column in (
                ("laya_compare", "laya_model"),
                ("calls", "licence"),
                ("calls", "training_verdict"),
                ("questions", "source"),
            ):
                info = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
                if column not in {row["name"] for row in info}:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
            if not _COLUMNS:
                for table in ("questions", "decisions", "stages", "calls"):
                    info = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
                    _COLUMNS[table] = {row["name"] for row in info}

    # --- low level -------------------------------------------------------------

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._conn.execute(sql, tuple(params)).fetchall()]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- question logs ---------------------------------------------------------

    def start_question(
        self,
        question_id: str,
        *,
        user_id: str | None,
        mode: str,
        messages: list[dict[str, Any]],
        source: str | None = None,
    ) -> None:
        self.execute(
            "INSERT INTO questions (id, created_at, user_id, mode, messages, source) "
            "VALUES (?,?,?,?,?,?)",
            (question_id, time.time(), user_id, mode, _json(messages), source),
        )

    def record_execution(
        self, question_id: str, stage: int, model: str | None, execution: Any
    ) -> None:
        """A sandbox run for one answer (tempo/execute.py), kept as a training reward."""
        e = execution
        self.execute(
            "INSERT INTO executions (question_id, stage, model, kind, language, status, method, "
            "tests_total, tests_passed, reward, error, stopped, computed, claimed, duration_ms, "
            "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                question_id,
                stage,
                model,
                e.kind,
                e.language,
                e.status,
                e.method,
                e.tests_total,
                e.tests_passed,
                e.reward,
                e.error,
                e.stopped,
                e.computed,
                e.claimed,
                e.duration_ms,
                time.time(),
            ),
        )

    def executions(self, question_id: str) -> list[dict[str, Any]]:
        return self.query(
            "SELECT * FROM executions WHERE question_id = ? ORDER BY id", (question_id,)
        )

    def update_question(self, question_id: str, **fields: Any) -> None:
        if not fields:
            return
        encoded = _encode("questions", fields)
        columns = ", ".join(f"{name} = ?" for name in encoded)
        self.execute(
            f"UPDATE questions SET {columns} WHERE id = ?", (*encoded.values(), question_id)
        )

    def add_decision(self, question_id: str, **fields: Any) -> int:
        encoded = _encode("decisions", fields)
        encoded["created_at"] = time.time()
        columns = ", ".join(["question_id", *encoded])
        marks = ", ".join("?" for _ in range(len(encoded) + 1))
        cursor = self.execute(
            f"INSERT INTO decisions ({columns}) VALUES ({marks})",
            (question_id, *encoded.values()),
        )
        return int(cursor.lastrowid or 0)

    def update_decision(self, decision_id: int, **fields: Any) -> None:
        encoded = _encode("decisions", fields)
        columns = ", ".join(f"{name} = ?" for name in encoded)
        self.execute(
            f"UPDATE decisions SET {columns} WHERE id = ?", (*encoded.values(), decision_id)
        )

    def add_stage(self, question_id: str, **fields: Any) -> None:
        encoded = _encode("stages", fields)
        encoded["created_at"] = time.time()
        columns = ", ".join(["question_id", *encoded])
        marks = ", ".join("?" for _ in range(len(encoded) + 1))
        self.execute(
            f"INSERT INTO stages ({columns}) VALUES ({marks})", (question_id, *encoded.values())
        )

    def add_call(self, **fields: Any) -> int:
        encoded = _encode("calls", {**fields, "created_at": time.time()})
        columns = ", ".join(encoded)
        marks = ", ".join("?" for _ in encoded)
        cursor = self.execute(
            f"INSERT INTO calls ({columns}) VALUES ({marks})", tuple(encoded.values())
        )
        return int(cursor.lastrowid or 0)

    def set_call_score(self, call_id: int, score: float) -> None:
        self.execute("UPDATE calls SET score = ? WHERE id = ?", (score, call_id))

    def set_feedback(self, question_id: str, rating: int, comment: str | None = None) -> bool:
        cursor = self.execute(
            "UPDATE questions SET feedback = ?, feedback_comment = ?, feedback_at = ? WHERE id = ?",
            (rating, comment, time.time(), question_id),
        )
        return cursor.rowcount > 0

    def question(self, question_id: str) -> dict[str, Any] | None:
        rows = self.query("SELECT * FROM questions WHERE id = ?", (question_id,))
        return rows[0] if rows else None

    # --- quota counters ----------------------------------------------------------

    def quota_day(self, bucket: str, day: str) -> tuple[int, int]:
        rows = self.query(
            "SELECT requests, tokens FROM quota_days WHERE bucket = ? AND day = ?", (bucket, day)
        )
        return (rows[0]["requests"], rows[0]["tokens"]) if rows else (0, 0)

    def quota_add(self, bucket: str, day: str, requests: int, tokens: int) -> None:
        self.execute(
            "INSERT INTO quota_days (bucket, day, requests, tokens) VALUES (?,?,?,?) "
            "ON CONFLICT(bucket, day) DO UPDATE SET requests = requests + excluded.requests, "
            "tokens = tokens + excluded.tokens",
            (bucket, day, requests, tokens),
        )

    # --- measured scores -----------------------------------------------------------

    def save_eval(self, model: str, task: str, n: int, score: float) -> None:
        self.execute(
            "INSERT INTO eval_results (model, task, n, score, updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(model, task) DO UPDATE SET n = excluded.n, score = excluded.score, "
            "updated_at = excluded.updated_at",
            (model, task, n, score, time.time()),
        )

    def eval_results(self) -> list[dict[str, Any]]:
        return self.query("SELECT model, task, n, score FROM eval_results")

    def live_scores(self, since: float = 0.0) -> list[dict[str, Any]]:
        """Mean judged score per (model, task) from real traffic."""
        return self.query(
            "SELECT model, task, COUNT(*) AS n, AVG(score) AS score FROM calls "
            "WHERE score IS NOT NULL AND task IS NOT NULL AND created_at >= ? "
            "GROUP BY model, task",
            (since,),
        )

    def live_latency(self, since: float = 0.0) -> list[dict[str, Any]]:
        return self.query(
            "SELECT model, COUNT(*) AS n, AVG(ttft_ms) AS ttft_ms, "
            "SUM(output_tokens) AS out_tokens, SUM(ms - COALESCE(ttft_ms, 0)) AS gen_ms "
            "FROM calls WHERE status = 'ok' AND created_at >= ? GROUP BY model",
            (since,),
        )

    # --- Laya comparison -------------------------------------------------------------

    def save_laya_compare(
        self,
        decision: str,
        n: int,
        laya_accuracy: float,
        rules_accuracy: float,
        laya_model: str = "stock",
    ) -> None:
        self.execute(
            "INSERT INTO laya_compare "
            "(decision, n, laya_accuracy, rules_accuracy, updated_at, laya_model) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(decision) DO UPDATE SET n = excluded.n, "
            "laya_accuracy = excluded.laya_accuracy, rules_accuracy = excluded.rules_accuracy, "
            "updated_at = excluded.updated_at, laya_model = excluded.laya_model",
            (decision, n, laya_accuracy, rules_accuracy, time.time(), laya_model),
        )

    # --- tempo-server collect ------------------------------------------------------------

    def collect_item(self, dataset: str, item_id: str) -> dict[str, Any] | None:
        rows = self.query(
            "SELECT * FROM collect_items WHERE dataset = ? AND item_id = ?", (dataset, item_id)
        )
        return rows[0] if rows else None

    def collect_mark(
        self,
        dataset: str,
        item_id: str,
        status: str,
        *,
        question_id: str | None = None,
        attempt: bool = False,
        note: str | None = None,
    ) -> None:
        self.execute(
            "INSERT INTO collect_items (dataset, item_id, status, question_id, attempts, note, "
            "updated_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(dataset, item_id) DO UPDATE SET "
            "status = excluded.status, question_id = COALESCE(excluded.question_id, "
            "collect_items.question_id), attempts = collect_items.attempts + ?, "
            "note = excluded.note, updated_at = excluded.updated_at",
            (dataset, item_id, status, question_id, int(attempt), note, time.time(), int(attempt)),
        )

    def collect_counts(self) -> dict[str, dict[str, int]]:
        counts: dict[str, dict[str, int]] = {}
        for row in self.query(
            "SELECT dataset, status, COUNT(*) AS n FROM collect_items GROUP BY dataset, status"
        ):
            counts.setdefault(row["dataset"], {})[row["status"]] = row["n"]
        return counts

    def collect_sources(self) -> dict[str, str]:
        """question id -> the dataset it came from."""
        rows = self.query(
            "SELECT question_id, dataset FROM collect_items WHERE question_id IS NOT NULL"
        )
        return {row["question_id"]: row["dataset"] for row in rows}

    def laya_compare(self) -> dict[str, dict[str, Any]]:
        return {row["decision"]: row for row in self.query("SELECT * FROM laya_compare")}
