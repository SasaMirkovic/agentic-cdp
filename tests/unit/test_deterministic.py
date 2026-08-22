import pandas as pd

from core.config import Settings
from core.stages.deterministic import run_stage1


def _silver(rows):
    defaults = {
        "company_name": None, "normalized_domain": None, "record_id": None,
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


def _settings() -> Settings:
    return Settings(_env_file=None)


def test_same_domain_same_name_matches():
    silver = _silver([
        {"record_id": "salesforce:1", "company_name": "Acme Corp.", "normalized_domain": "acme.com"},
        {"record_id": "marketo:2", "company_name": "Acme Corporation", "normalized_domain": "acme.com"},
    ])
    decisions, demoted = run_stage1(silver, _settings(), set())
    assert len(decisions) == 1
    assert decisions[0]["match"] is True
    assert decisions[0]["stage"] == "deterministic"
    assert not demoted


def test_shared_domain_subsidiary_is_demoted_not_matched():
    silver = _silver([
        {"record_id": "salesforce:1", "company_name": "Northwind", "normalized_domain": "northwind.com"},
        {"record_id": "xactly:2", "company_name": "Northwind Labs", "normalized_domain": "northwind.com"},
    ])
    decisions, demoted = run_stage1(silver, _settings(), set())
    assert decisions == []
    assert ("salesforce:1", "xactly:2") in demoted


def test_freemail_domains_never_match():
    silver = _silver([
        {"record_id": "salesforce:1", "company_name": "Acme", "normalized_domain": "gmail.com"},
        {"record_id": "marketo:2", "company_name": "Acme", "normalized_domain": "gmail.com"},
    ])
    decisions, demoted = run_stage1(silver, _settings(), set())
    assert decisions == [] and not demoted


def test_already_decided_pairs_are_skipped():
    silver = _silver([
        {"record_id": "salesforce:1", "company_name": "Acme", "normalized_domain": "acme.com"},
        {"record_id": "marketo:2", "company_name": "Acme", "normalized_domain": "acme.com"},
    ])
    decisions, _ = run_stage1(silver, _settings(), {("marketo:2", "salesforce:1")})
    assert decisions == []
