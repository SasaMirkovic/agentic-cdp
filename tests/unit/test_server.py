import json

import pytest
from fastapi.testclient import TestClient

from adapters import queue_store
from app.server.main import create_app
from core.config import Settings


def _payload(a_id: str, b_id: str) -> dict:
    def record(rid):
        return {
            "record_id": rid, "source_system": rid.split(":")[0],
            "company_name": "Acme Corp", "domain": "acme.com",
            "address": "1 Main St, Austin, TX", "phone": "(512) 555-0100",
            "industry": "Software", "employees": 100, "revenue": 5e6,
            "notes": "Renewal call notes.",
            "normalized": {"name": "acme", "domain": "acme.com",
                           "phone": "5125550100", "address": "1 main st austin tx"},
            "pii_flags": ["phone", "address", "notes"],
        }
    return {
        "record_a": record(a_id),
        "record_b": record(b_id),
        "agent": {"match": True, "confidence": 0.78, "evidence": ["domain_match"],
                  "reason": "Likely same company.", "decided_by": "mock-oracle"},
    }


@pytest.fixture()
def client(tmp_path) -> TestClient:
    settings = Settings(_env_file=None, cdp_data_dir=tmp_path)
    con = queue_store.connect(settings.queue_db_path)
    queue_store.upsert_pending(con, "marketo:1", "salesforce:2", _payload("marketo:1", "salesforce:2"))
    queue_store.upsert_pending(con, "salesforce:3", "xactly:4", _payload("salesforce:3", "xactly:4"))
    con.close()
    return TestClient(create_app(settings))


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_stats_with_no_metrics_file(client):
    stats = client.get("/api/stats").json()
    assert stats["pending"] == 2
    assert stats["metrics"] is None
    assert stats["decisions_pending_ingestion"] == 0


def test_next_and_decide_flow(client):
    item = client.get("/api/queue/next").json()
    assert item["status"] == "pending"
    decided = client.post(f"/api/queue/{item['id']}/decision", json={"decision": "approve"}).json()
    assert decided["status"] == "approved"
    stats = client.get("/api/stats").json()
    assert stats["approved"] == 1
    assert stats["decisions_pending_ingestion"] == 1


def test_double_decide_conflicts(client):
    item = client.get("/api/queue/next").json()
    client.post(f"/api/queue/{item['id']}/decision", json={"decision": "reject"})
    response = client.post(f"/api/queue/{item['id']}/decision", json={"decision": "approve"})
    assert response.status_code == 409


def test_queue_drains_to_404(client):
    for _ in range(2):
        item = client.get("/api/queue/next").json()
        client.post(f"/api/queue/{item['id']}/decision", json={"decision": "approve"})
    assert client.get("/api/queue/next").status_code == 404


def test_skip_keeps_item_reviewable(client):
    first = client.get("/api/queue/next").json()
    client.post(f"/api/queue/{first['id']}/decision", json={"decision": "skip"})
    second = client.get("/api/queue/next").json()
    assert second["id"] != first["id"]
    client.post(f"/api/queue/{second['id']}/decision", json={"decision": "approve"})
    resurfaced = client.get("/api/queue/next").json()
    assert resurfaced["id"] == first["id"]


def test_analyst_role_masks_pii_steward_sees_clear(client):
    steward = client.get("/api/queue/next", params={"role": "steward"}).json()
    assert steward["record_a"]["phone"] == "(512) 555-0100"
    analyst = client.get("/api/queue/next", params={"role": "analyst"}).json()
    assert analyst["record_a"]["phone"] == "•••"
    assert analyst["record_a"]["address"] == "•••"
    assert analyst["record_a"]["notes"] == "•••"
    assert analyst["record_a"]["normalized"]["phone"] == "•••"
    # Non-PII fields stay clear for both roles.
    assert analyst["record_a"]["company_name"] == "Acme Corp"


def test_unknown_item_404(client):
    assert client.post("/api/queue/999/decision", json={"decision": "approve"}).status_code == 404


def test_masking_fails_closed(client):
    # No role / unknown role must never elevate to clear PII.
    default = client.get("/api/queue/next").json()
    assert default["record_a"]["phone"] == "•••"
    assert client.get("/api/queue/next", params={"role": "ANALYST"}).status_code == 422
    assert client.get("/api/queue/next", params={"role": "<script>"}).status_code == 422


def test_query_validation(client):
    assert client.get("/api/queue", params={"status": "nonsense"}).status_code == 422
    assert client.get("/api/queue", params={"limit": 0}).status_code == 422
    assert client.get("/api/queue", params={"limit": -5}).status_code == 422
    huge = "9" * 30
    assert client.post(f"/api/queue/{huge}/decision", json={"decision": "approve"}).status_code == 422
    assert client.post("/api/queue/1/decision", json={"decision": "maybe"}).status_code == 422


def test_fixture_refuses_real_data_dir(tmp_path):
    from app.server.main import seed_from_fixture

    settings = Settings(_env_file=None, cdp_data_dir=tmp_path)
    settings.duckdb_path.write_bytes(b"")
    fixture = tmp_path / "f.json"
    fixture.write_text('{"items": []}', encoding="utf-8")
    with pytest.raises(RuntimeError):
        seed_from_fixture(settings, str(fixture))


def test_fixture_utf8_and_pair_ordering(tmp_path):
    from app.server.main import seed_from_fixture

    settings = Settings(_env_file=None, cdp_data_dir=tmp_path)
    item = _payload("salesforce:9", "marketo:8")  # reversed order on purpose
    item["record_a"]["company_name"] = "Société Générale • Straße"
    fixture = tmp_path / "f.json"
    fixture.write_text(json.dumps({"items": [item, item]}, ensure_ascii=False), encoding="utf-8")
    seed_from_fixture(settings, str(fixture))
    client = TestClient(create_app(settings))
    page = client.get("/api/queue", params={"role": "steward"}).json()
    assert page["total"] == 1
    assert page["items"][0]["record_a"]["company_name"] == "Société Générale • Straße"
