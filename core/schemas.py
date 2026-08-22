"""Shared Pydantic models — the contract between pipeline, API server, and frontend."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AgentVerdict(BaseModel):
    """Required structured output of the Stage-3 agent (live or mock)."""

    match: bool
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str]
    reason: str


class NormalizedFields(BaseModel):
    name: str | None = None
    domain: str | None = None
    phone: str | None = None
    address: str | None = None


class RecordView(BaseModel):
    """A source record as shown in the review UI. PII fields may be masked per role."""

    record_id: str
    source_system: str
    company_name: str | None = None
    domain: str | None = None
    address: str | None = None
    phone: str | None = None
    industry: str | None = None
    employees: int | None = None
    revenue: float | None = None
    notes: str | None = None
    normalized: NormalizedFields = NormalizedFields()
    pii_flags: list[str] = []


class AgentSnapshot(BaseModel):
    match: bool
    confidence: float
    evidence: list[str]
    reason: str
    decided_by: str


QueueStatus = Literal["pending", "approved", "rejected", "skipped"]


class QueueItem(BaseModel):
    id: int
    record_a: RecordView
    record_b: RecordView
    agent: AgentSnapshot
    status: QueueStatus
    created_at: str
    reviewed_at: str | None = None


class QueuePage(BaseModel):
    items: list[QueueItem]
    total: int


class DecisionRequest(BaseModel):
    decision: Literal["approve", "reject", "skip"]


class MetricsSummary(BaseModel):
    precision: float | None = None
    recall: float | None = None
    false_positives: int | None = None
    agent_band_size: int | None = None
    flagged_clusters: int | None = None


class Stats(BaseModel):
    pending: int
    approved: int
    rejected: int
    skipped: int
    decisions_pending_ingestion: int
    metrics: MetricsSummary | None = None
