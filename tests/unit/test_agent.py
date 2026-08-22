import json
from unittest.mock import MagicMock, patch

import pandas as pd

from core.config import Settings
from core.schemas import AgentVerdict
from core.stages.agent import OracleAgent, run_stage3


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


GROUND_TRUTH = pd.DataFrame(
    {
        "record_id": ["salesforce:1", "marketo:2", "xactly:3"],
        "entity_id": ["E1", "E1", "E2"],
    }
)

SILVER = pd.DataFrame(
    [
        {"record_id": "salesforce:1", "source_system": "salesforce", "company_name": "Acme",
         "normalized_name": "acme", "normalized_domain": "acme.com", "normalized_phone": "1",
         "normalized_address": None, "industry": "Software", "notes": None,
         "domain": "acme.com", "address": None, "phone": "1", "employees": 10, "revenue": 1.0},
        {"record_id": "marketo:2", "source_system": "marketo", "company_name": "Acme Corp",
         "normalized_name": "acme", "normalized_domain": "acme.com", "normalized_phone": "1",
         "normalized_address": None, "industry": "Software", "notes": None,
         "domain": "acme.com", "address": None, "phone": "1", "employees": 10, "revenue": 1.0},
        {"record_id": "xactly:3", "source_system": "xactly", "company_name": "Zeta",
         "normalized_name": "zeta", "normalized_domain": "zeta.com", "normalized_phone": "2",
         "normalized_address": None, "industry": "Retail", "notes": None,
         "domain": "zeta.com", "address": None, "phone": "2", "employees": 5, "revenue": 2.0},
    ]
)


def test_oracle_is_deterministic_per_pair():
    settings = _settings()
    agent1 = OracleAgent(settings, GROUND_TRUTH)
    agent2 = OracleAgent(settings, GROUND_TRUTH)
    a = SILVER.iloc[0].to_dict()
    b = SILVER.iloc[1].to_dict()
    assert agent1.decide(a, b) == agent2.decide(a, b)


def test_oracle_true_match_is_match():
    agent = OracleAgent(_settings(), GROUND_TRUTH)
    verdict = agent.decide(SILVER.iloc[0].to_dict(), SILVER.iloc[1].to_dict())
    assert verdict.match is True


def test_injected_errors_are_false_matches_above_threshold():
    # Force a 100% error rate: every true-negative pair becomes an injected FP.
    settings = _settings(oracle_error_rate=1.0)
    agent = OracleAgent(settings, GROUND_TRUTH)
    verdict = agent.decide(SILVER.iloc[0].to_dict(), SILVER.iloc[2].to_dict())
    assert verdict.match is True
    assert verdict.confidence >= settings.agent_confidence_threshold
    assert agent.injected_errors == [["salesforce:1", "xactly:3"]]


def test_zero_error_rate_never_injects():
    settings = _settings(oracle_error_rate=0.0)
    agent = OracleAgent(settings, GROUND_TRUTH)
    verdict = agent.decide(SILVER.iloc[0].to_dict(), SILVER.iloc[2].to_dict())
    assert verdict.match is False
    assert agent.injected_errors == []


def test_run_stage3_mock_labels_and_costs():
    settings = _settings(oracle_error_rate=0.0)
    decisions, injected = run_stage3(
        SILVER, [("marketo:2", "salesforce:1")], settings, GROUND_TRUTH
    )
    assert len(decisions) == 1
    d = decisions[0]
    assert d["decided_by"] == "mock-oracle"
    assert d["cost_usd"] is not None
    assert json.loads(d["evidence"])["cost_simulated"] is True
    assert injected == []


def test_live_agent_request_shape():
    settings = _settings(mock_llm=False, anthropic_api_key="test-key")
    fake_verdict = AgentVerdict(match=True, confidence=0.9, evidence=["domain_match"], reason="x")
    with patch("anthropic.Anthropic") as anthropic_cls:
        client = MagicMock()
        anthropic_cls.return_value = client
        client.messages.parse.return_value = MagicMock(parsed_output=fake_verdict)
        decisions, _ = run_stage3(
            SILVER, [("marketo:2", "salesforce:1")], settings, GROUND_TRUTH
        )
        call = client.messages.parse.call_args
        assert call.kwargs["model"] == settings.agent_model
        assert call.kwargs["output_format"] is AgentVerdict
        payload = json.loads(call.kwargs["messages"][0]["content"])
        assert payload["record_a"]["record_id"] in ("marketo:2", "salesforce:1")
    assert decisions[0]["decided_by"] == f"model:{settings.agent_model}"
    assert decisions[0]["cost_usd"] is None
