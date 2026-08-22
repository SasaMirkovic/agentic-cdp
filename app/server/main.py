"""Review-queue API server.

Reads/writes the SQLite queue and reads metrics.json. Never opens the DuckDB
file — that keeps `cdp pipeline` safely runnable while this server is up.

Role-based masking demo: `role=analyst` masks PII fields (phone, address,
notes) per each record's pii_flags; `role=steward` sees them clear.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Path as PathParam, Query
from fastapi.staticfiles import StaticFiles

from adapters import queue_store
from core.config import REPO_ROOT, Settings
from core.schemas import DecisionRequest, MetricsSummary, QueueItem, QueuePage, Stats
from core.stages.common import pair_key

MASK = "•••"
FRONTEND_DIST = REPO_ROOT / "app" / "frontend" / "dist"

# Fail closed: anything that is not explicitly the steward role sees PII masked,
# and unknown role values are rejected (422) rather than silently elevated.
Role = Literal["analyst", "steward"]
MAX_ITEM_ID = 2**62


def _mask_record(record: dict) -> dict:
    masked = deepcopy(record)
    flags = set(masked.get("pii_flags") or [])
    normalized = masked.setdefault("normalized", {})
    if "phone" in flags:
        masked["phone"] = MASK
        normalized["phone"] = MASK
    if "address" in flags:
        masked["address"] = MASK
        normalized["address"] = MASK
    if "notes" in flags:
        masked["notes"] = MASK
    return masked


def _item_from_row(row, role: str) -> QueueItem:
    payload = json.loads(row["payload"])
    record_a, record_b = payload["record_a"], payload["record_b"]
    if role != "steward":
        record_a, record_b = _mask_record(record_a), _mask_record(record_b)
    return QueueItem(
        id=row["id"],
        record_a=record_a,
        record_b=record_b,
        agent=payload["agent"],
        status=row["status"],
        created_at=row["created_at"],
        reviewed_at=row["reviewed_at"],
    )


def _load_metrics_summary(settings: Settings) -> MetricsSummary | None:
    if not settings.metrics_path.exists():
        return None
    try:
        metrics = json.loads(settings.metrics_path.read_text(encoding="utf-8"))
        final = metrics["stages"][-1]
        return MetricsSummary(
            precision=final["precision"],
            recall=final["recall"],
            false_positives=final["false_positive_pairs"],
            agent_band_size=metrics.get("agent_band_size"),
            flagged_clusters=metrics.get("flagged_clusters"),
        )
    except (json.JSONDecodeError, KeyError, IndexError):
        return None


def seed_from_fixture(settings: Settings, fixture_path: str) -> None:
    """Reset queue.db and seed it from a JSON fixture (tests and demos).

    Refuses to run against a data dir that holds a real pipeline database:
    fixture record ids share the real namespace, and a human decision on a
    fixture pair would otherwise be ingested as an uncuttable edge between two
    unrelated real companies. Point CDP_DATA_DIR at an isolated directory.
    """
    if settings.duckdb_path.exists():
        raise RuntimeError(
            f"Refusing --fixture: {settings.duckdb_path} holds real pipeline data. "
            "Set CDP_DATA_DIR to an isolated directory for fixture-backed serving."
        )
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(settings.queue_db_path) + suffix)
        if p.exists():
            p.unlink()
    fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    con = queue_store.connect(settings.queue_db_path)
    try:
        for item in fixture["items"]:
            a, b = pair_key(item["record_a"]["record_id"], item["record_b"]["record_id"])
            queue_store.upsert_pending(con, a, b, item)
    finally:
        con.close()


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="Agentic CDP — Review Queue", docs_url="/api/docs")

    def q():
        return queue_store.connect(settings.queue_db_path)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/stats", response_model=Stats)
    def stats(role: Role = Query("analyst")) -> Stats:
        con = q()
        try:
            counts = queue_store.counts(con)
            return Stats(
                pending=counts["pending"] + counts["skipped"],
                approved=counts["approved"],
                rejected=counts["rejected"],
                skipped=counts["skipped"],
                decisions_pending_ingestion=queue_store.pending_ingestion_count(con),
                metrics=_load_metrics_summary(settings),
            )
        finally:
            con.close()

    @app.get("/api/queue", response_model=QueuePage)
    def queue_page(
        status: Literal["pending", "approved", "rejected", "skipped"] = Query("pending"),
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
        role: Role = Query("analyst"),
    ) -> QueuePage:
        con = q()
        try:
            rows, total = queue_store.list_items(con, status, limit, offset)
            return QueuePage(items=[_item_from_row(r, role) for r in rows], total=total)
        finally:
            con.close()

    @app.get("/api/queue/next", response_model=QueueItem)
    def next_item(role: Role = Query("analyst")) -> QueueItem:
        con = q()
        try:
            row = queue_store.next_pending(con)
            if row is None:
                raise HTTPException(status_code=404, detail="queue empty")
            return _item_from_row(row, role)
        finally:
            con.close()

    @app.post("/api/queue/{item_id}/decision", response_model=QueueItem)
    def decide(
        item_id: int = PathParam(ge=1, le=MAX_ITEM_ID),
        body: DecisionRequest = ...,
        role: Role = Query("analyst"),
    ) -> QueueItem:
        con = q()
        try:
            if queue_store.get_item(con, item_id) is None:
                raise HTTPException(status_code=404, detail="unknown queue item")
            # Atomic: the update only applies while the item is still reviewable.
            updated = queue_store.decide(con, item_id, body.decision)
            if updated is None:
                raise HTTPException(status_code=409, detail="item already decided")
            return _item_from_row(updated, role)
        finally:
            con.close()

    if FRONTEND_DIST.exists():
        # Mounted last so /api/* routes win; html=True serves index.html at "/"
        # and root-level files (favicon.svg, icons.svg) alongside /assets.
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

    return app
