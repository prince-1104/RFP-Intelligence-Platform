import pytest

from rfp.schemas.search import SearchConfig
from rfp.search.errors import InvalidSearchRequest
from rfp.search.fusion import reciprocal_rank_fusion
from rfp.search.identifiers import extract_identifiers, identifier_terms, matched_identifiers
from rfp.search.query import expand, normalize_query, plan_query


def test_normalization_is_conservative_and_deterministic():
    text = "  PX\u2013420\u00a0 \u201cpump\u201d\tdue\x00 date "
    normalized = normalize_query(text)
    assert normalized == 'PX-420 "pump" due date'
    assert normalize_query(normalized) == normalized


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Model PX-420 and px-420", ["PX-420"]),
        ("RFP-2026-014 / SKU AB-77/X", ["RFP-2026-014", "AB-77/X"]),
        ("Part #A17B ships", ["A17B"]),
        ("10mm 3rd 20 pcs 2026 15%", []),
        ("Bid 25-118 opens", ["25-118"]),
    ],
)
def test_identifier_extraction(text, expected):
    assert extract_identifiers(text) == expected


def test_identifier_terms_and_matching():
    assert identifier_terms(["PX-420"]) == ["PX-420", "PX420"]
    assert matched_identifiers(["PX420"], ["PX-420", "PX420"]) == ["PX420"]
    assert matched_identifiers(["PX-421"], ["PX-420", "PX420"]) == []


def test_expansion_is_additive_bounded_and_word_bounded():
    synonyms = SearchConfig().synonyms
    expansions = expand("What is the deadline?", synonyms, 12)
    assert expansions[0].term == "deadline"
    assert "due date" in expansions[0].added
    assert expand("deadlines", synonyms, 12) == []
    assert expand("deadline", synonyms, 1)[0].added == ["submission deadline"]
    assert expand("submission deadline", synonyms, 12)[0].added == ["due date", "closing date"]


def test_plan_query_branches():
    plan = plan_query("  Pre-bid meeting for PX-420 ", SearchConfig())
    assert plan.dense_query == "Pre-bid meeting for PX-420"
    assert plan.sparse_query.startswith(plan.normalized)
    assert "pre-proposal conference" in plan.sparse_query
    assert plan.identifiers == ["PX-420"]
    assert plan.identifier_terms == ["PX-420", "PX420"]
    assert len(plan.query_hash) == 16 and "PX" not in plan.query_hash


def test_custom_synonyms_and_validation():
    config = SearchConfig(synonyms={"Widget": ["gadget"]})
    assert plan_query("widget", config).sparse_query == "widget gadget"
    with pytest.raises(InvalidSearchRequest) as error:
        plan_query("?!", config)
    assert error.value.code == "invalid_query"
    with pytest.raises(InvalidSearchRequest):
        plan_query("a" * 2000, config)


def test_rrf_scores_ties_and_weights():
    fused = reciprocal_rank_fusion({"dense": ["a", "b", "c"], "sparse": ["b", "a", "d"]}, k=60)
    order = [item for item, _ in fused]
    assert set(order[:2]) == {"a", "b"} and order[2:] == ["c", "d"]
    assert fused[0][1] == pytest.approx(1 / 61 + 1 / 62)
    assert (
        reciprocal_rank_fusion({"dense": ["a", "b", "c"], "sparse": ["b", "a", "d"]}, k=60) == fused
    )
    weighted = reciprocal_rank_fusion(
        {"dense": ["a"], "sparse": ["b"]}, k=60, weights={"dense": 1.0, "sparse": 2.0}
    )
    assert [item for item, _ in weighted] == ["b", "a"]
    assert reciprocal_rank_fusion({}) == []
