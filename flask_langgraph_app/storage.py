from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent / "app.db"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
              id TEXT PRIMARY KEY,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
              id TEXT PRIMARY KEY,
              conversation_id TEXT NOT NULL,
              role TEXT NOT NULL,
              content TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS context_vars (
              conversation_id TEXT PRIMARY KEY,
              selected_index TEXT,
              time_range_hours INTEGER,
              max_rows INTEGER,
              baseline_window_hours INTEGER,
              mode TEXT,
              baseline_first INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS traces (
              trace_id TEXT PRIMARY KEY,
              conversation_id TEXT,
              created_at TEXT NOT NULL,
              node_path_json TEXT,
              node_timings_json TEXT,
              tool_trace_json TEXT,
              plan_json TEXT,
              queries_json TEXT,
              validation_json TEXT,
              evidence_json TEXT,
              answer_text TEXT,
              blocked_bool INTEGER,
              blocked_reason TEXT
            );
            CREATE TABLE IF NOT EXISTS snapshots (
              trace_id TEXT NOT NULL,
              node_name TEXT NOT NULL,
              state_json TEXT NOT NULL,
              PRIMARY KEY(trace_id, node_name)
            );
            """
        )


def ensure_conversation(conversation_id: str | None = None) -> str:
    cid = conversation_id or str(uuid.uuid4())
    now = utcnow_iso()
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO conversations(id, created_at, updated_at) VALUES (?, ?, ?)",
            (cid, now, now),
        )
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, cid))
    return cid


def save_message(conversation_id: str, role: str, content: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO messages(id, conversation_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), conversation_id, role, content, utcnow_iso()),
        )


def get_context_vars(conversation_id: str) -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM context_vars WHERE conversation_id = ?", (conversation_id,)).fetchone()
    return dict(row) if row else {}


def upsert_context_vars(conversation_id: str, values: dict[str, Any]) -> None:
    existing = get_context_vars(conversation_id)
    merged = {
        "selected_index": existing.get("selected_index"),
        "time_range_hours": existing.get("time_range_hours"),
        "max_rows": existing.get("max_rows"),
        "baseline_window_hours": existing.get("baseline_window_hours"),
        "mode": existing.get("mode"),
        "baseline_first": existing.get("baseline_first", 0),
    }
    merged.update({k: v for k, v in values.items() if v is not None})
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO context_vars(conversation_id, selected_index, time_range_hours, max_rows, baseline_window_hours, mode, baseline_first)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(conversation_id) DO UPDATE SET
             selected_index=excluded.selected_index,
             time_range_hours=excluded.time_range_hours,
             max_rows=excluded.max_rows,
             baseline_window_hours=excluded.baseline_window_hours,
             mode=excluded.mode,
             baseline_first=excluded.baseline_first
            """,
            (
                conversation_id,
                merged["selected_index"],
                merged["time_range_hours"],
                merged["max_rows"],
                merged["baseline_window_hours"],
                merged["mode"],
                int(bool(merged["baseline_first"])),
            ),
        )


def save_trace(trace: dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO traces(trace_id, conversation_id, created_at, node_path_json, node_timings_json, tool_trace_json,
             plan_json, queries_json, validation_json, evidence_json, answer_text, blocked_bool, blocked_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace["trace_id"],
                trace.get("conversation_id"),
                utcnow_iso(),
                json.dumps(trace.get("node_path", [])),
                json.dumps(trace.get("node_timings", {})),
                json.dumps(trace.get("tool_trace", [])),
                json.dumps(trace.get("plan", {})),
                json.dumps(trace.get("queries", {})),
                json.dumps(trace.get("validation_report", {})),
                json.dumps(trace.get("evidence_cards", [])),
                trace.get("final_answer", ""),
                int(bool(trace.get("blocked"))),
                trace.get("blocked_reason"),
            ),
        )


def save_snapshot(trace_id: str, node_name: str, state: dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO snapshots(trace_id, node_name, state_json) VALUES (?, ?, ?)",
            (trace_id, node_name, json.dumps(state)),
        )


def get_trace(trace_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM traces WHERE trace_id = ?", (trace_id,)).fetchone()
    return dict(row) if row else None


def get_snapshot(trace_id: str, node_name: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT state_json FROM snapshots WHERE trace_id = ? AND node_name = ?",
            (trace_id, node_name),
        ).fetchone()
    if not row:
        return None
    return json.loads(row["state_json"])
