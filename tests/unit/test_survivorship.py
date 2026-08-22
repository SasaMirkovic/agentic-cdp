import pandas as pd

from core.clustering import ClusterResult, cluster_id_for
from core.survivorship import anchor_of, assign_customer_ids, mint_customer_id

RECORDS = {
    "salesforce:1": {"loaded_at": "2026-07-01", "source_system": "salesforce"},
    "xactly:2": {"loaded_at": "2026-07-02", "source_system": "xactly"},
    "marketo:3": {"loaded_at": "2026-07-03", "source_system": "marketo"},
    "sixsense:4": {"loaded_at": "2026-07-04", "source_system": "sixsense"},
}

EMPTY_XWALK = pd.DataFrame(
    columns=["customer_id", "anchor_record_id", "member_set_hash", "status",
             "superseded_by_customer_id", "run_id"]
)


def _result(*clusters):
    r = ClusterResult()
    for members in clusters:
        r.clusters[cluster_id_for(list(members))] = sorted(members)
    return r


def test_anchor_is_oldest_then_source_priority():
    assert anchor_of(["marketo:3", "salesforce:1"], RECORDS) == "salesforce:1"
    assert anchor_of(["sixsense:4", "xactly:2"], RECORDS) == "xactly:2"


def test_minting_is_deterministic():
    assert mint_customer_id("salesforce:1") == mint_customer_id("salesforce:1")


def test_ids_stable_when_membership_unchanged():
    result = _result(["salesforce:1", "xactly:2"])
    ids1, rows1 = assign_customer_ids(result, RECORDS, EMPTY_XWALK, "run-a")
    xwalk = pd.DataFrame(rows1)
    ids2, rows2 = assign_customer_ids(result, RECORDS, xwalk, "run-b")
    assert ids1 == ids2
    assert rows2 == []  # nothing to append on an unchanged re-run


def test_merge_survivor_is_oldest_anchor_and_loser_is_superseded():
    separate = _result(["salesforce:1"], ["xactly:2"])
    ids1, rows1 = assign_customer_ids(separate, RECORDS, EMPTY_XWALK, "run-a")
    xwalk = pd.DataFrame(rows1)
    merged = _result(["salesforce:1", "xactly:2"])
    ids2, rows2 = assign_customer_ids(merged, RECORDS, xwalk, "run-b")
    survivor = ids2[cluster_id_for(["salesforce:1", "xactly:2"])]
    assert survivor == mint_customer_id("salesforce:1")
    superseded = [r for r in rows2 if r["status"] == "superseded"]
    assert len(superseded) == 1
    assert superseded[0]["customer_id"] == mint_customer_id("xactly:2")
    assert superseded[0]["superseded_by_customer_id"] == survivor


def test_ids_never_reused_after_merge_then_split():
    separate = _result(["salesforce:1"], ["xactly:2"])
    ids1, rows1 = assign_customer_ids(separate, RECORDS, EMPTY_XWALK, "run-a")
    xactly_id = ids1[cluster_id_for(["xactly:2"])]
    merged = _result(["salesforce:1", "xactly:2"])
    _, rows2 = assign_customer_ids(merged, RECORDS, pd.DataFrame(rows1), "run-b")
    xwalk = pd.DataFrame(rows1 + rows2)
    # Split back apart: the xactly fragment must get a brand-new id, not the superseded one.
    ids3, _ = assign_customer_ids(separate, RECORDS, xwalk, "run-c")
    assert ids3[cluster_id_for(["xactly:2"])] != xactly_id


def test_split_fragment_with_anchor_keeps_id():
    merged = _result(["salesforce:1", "xactly:2", "marketo:3"])
    ids1, rows1 = assign_customer_ids(merged, RECORDS, EMPTY_XWALK, "run-a")
    old_id = next(iter(ids1.values()))
    xwalk = pd.DataFrame(rows1)
    split = _result(["salesforce:1", "xactly:2"], ["marketo:3"])
    ids2, _ = assign_customer_ids(split, RECORDS, xwalk, "run-b")
    assert ids2[cluster_id_for(["salesforce:1", "xactly:2"])] == old_id
    assert ids2[cluster_id_for(["marketo:3"])] != old_id
