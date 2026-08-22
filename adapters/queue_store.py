"""SQLite (WAL) adapter for the human-review queue.

This is the only store the API server touches. The pipeline writes pending
items and reads back human decisions; the server serves and mutates them.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS review_queue (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    record_a_id   TEXT NOT NULL,
    record_b_id   TEXT NOT NULL,
    payload       TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    created_at    TEXT NOT NULL,
    reviewed_at   TEXT,
    ingested_at   TEXT,
    UNIQUE (record_a_id, record_b_id)
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(_SCHEMA)
    con.commit()
    return con


def upsert_pending(con: sqlite3.Connection, record_a_id: str, record_b_id: str, payload: dict) -> None:
    con.execute(
        """
        INSERT INTO review_queue (record_a_id, record_b_id, payload, status, created_at)
        VALUES (?, ?, ?, 'pending', ?)
        ON CONFLICT (record_a_id, record_b_id) DO NOTHING
        """,
        (record_a_id, record_b_id, json.dumps(payload), _now()),
    )
    con.commit()


def counts(con: sqlite3.Connection) -> dict[str, int]:
    rows = con.execute("SELECT status, COUNT(*) AS n FROM review_queue GROUP BY status").fetchall()
    result = {"pending": 0, "approved": 0, "rejected": 0, "skipped": 0}
    for row in rows:
        result[row["status"]] = row["n"]
    return result


def pending_ingestion_count(con: sqlite3.Connection) -> int:
    row = con.execute(
        "SELECT COUNT(*) AS n FROM review_queue "
        "WHERE status IN ('approved', 'rejected') AND ingested_at IS NULL"
    ).fetchone()
    return row["n"]


def next_pending(con: sqlite3.Connection) -> sqlite3.Row | None:
    # Skipped items stay reviewable: they resurface once the pending ones drain.
    return con.execute(
        "SELECT * FROM review_queue WHERE status IN ('pending', 'skipped') "
        "ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, id LIMIT 1"
    ).fetchone()


def get_item(con: sqlite3.Connection, item_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM review_queue WHERE id = ?", (item_id,)).fetchone()


def list_items(con: sqlite3.Connection, status: str, limit: int, offset: int) -> tuple[list[sqlite3.Row], int]:
    rows = con.execute(
        "SELECT * FROM review_queue WHERE status = ? ORDER BY id LIMIT ? OFFSET ?",
        (status, limit, offset),
    ).fetchall()
    total = con.execute(
        "SELECT COUNT(*) AS n FROM review_queue WHERE status = ?", (status,)
    ).fetchone()["n"]
    return rows, total


def decide(con: sqlite3.Connection, item_id: int, decision: str) -> sqlite3.Row | None:
    """Apply a decision atomically; returns None if the item was no longer reviewable
    (already approved/rejected), so concurrent decisions can't both win."""
    status = {"approve": "approved", "reject": "rejected", "skip": "skipped"}[decision]
    cur = con.execute(
        "UPDATE review_queue SET status = ?, reviewed_at = ? "
        "WHERE id = ? AND status IN ('pending', 'skipped')",
        (status, _now(), item_id),
    )
    con.commit()
    if cur.rowcount == 0:
        return None
    return get_item(con, item_id)


def uningested_decisions(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM review_queue "
        "WHERE status IN ('approved', 'rejected') AND ingested_at IS NULL"
    ).fetchall()


def mark_ingested(con: sqlite3.Connection, ids: list[int]) -> None:
    now = _now()
    con.executemany("UPDATE review_queue SET ingested_at = ? WHERE id = ?", [(now, i) for i in ids])
    con.commit()


def unresolved_items(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM review_queue WHERE status IN ('pending', 'skipped')"
    ).fetchall()


def resolve_externally(con: sqlite3.Connection, item_id: int, status: str) -> None:
    now = _now()
    con.execute(
        "UPDATE review_queue SET status = ?, reviewed_at = ?, ingested_at = ? WHERE id = ?",
        (status, now, now, item_id),
    )
    con.commit()


def existing_pairs(con: sqlite3.Connection) -> set[tuple[str, str]]:
    rows = con.execute("SELECT record_a_id, record_b_id FROM review_queue").fetchall()
    return {(r["record_a_id"], r["record_b_id"]) for r in rows}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
