"""
Tests for aico.evals.day01:
- normalise matches the matching_rule used to label anchors
- anchor_hit_rank finds the right rank, or None if there's no match
- evaluate_config computes Hit@1 / MRR, multi-chunk full hit, and scores
  no_match queries separately

These use the real supplied documents and the real day01_queries.json, at
the same 200/40 config used for the report, so they double as a check that
the scorer behaves the way the eval set was designed to be scored.
"""
import json
import pathlib

from aico.evals.day01 import (
    NO_MATCH_SCORE_FLOOR,
    anchor_hit_rank,
    build_chunks,
    evaluate_config,
    has_phrase_support,
    normalise,
    pick_winning_config,
    pick_worst_query,
    query_content_bigrams,
    render_report,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCUMENTS_DIR = REPO_ROOT / "data" / "documents"
QUERIES_PATH = REPO_ROOT / "data" / "evals" / "day01_queries.json"

QUERIES = json.loads(QUERIES_PATH.read_text(encoding="utf-8"))["queries"]
CHUNKS = build_chunks(DOCUMENTS_DIR, tokens=200, overlap=40)
RESULT = evaluate_config(CHUNKS, QUERIES)

CHUNKS_400_80 = build_chunks(DOCUMENTS_DIR, tokens=400, overlap=80)
RESULT_400_80 = evaluate_config(CHUNKS_400_80, QUERIES)


def test_normalise_matches_the_anchor_labelling_rule():
    # lowercase, strip punctuation, collapse whitespace - the exact rule
    # anchors are labelled and matched under (matching_rule in the queries file)
    assert normalise("Net-30 Days!!") == "net 30 days"
    assert normalise("  too   many   spaces  ") == "too many spaces"


class FakeResult:
    def __init__(self, text):
        self.chunk = {"text": text}


def test_anchor_hit_rank_finds_the_matching_rank_or_none():
    results = [
        FakeResult("nothing useful here"),
        FakeResult("ninety days written notice of withdrawal"),
    ]
    assert anchor_hit_rank(results, "ninety days written notice of withdrawal") == 2

    irrelevant_results = [FakeResult("irrelevant"), FakeResult("still irrelevant")]
    assert anchor_hit_rank(irrelevant_results, "ninety days written notice of withdrawal") is None


def test_build_chunks_returns_records_with_text():
    # regression check: this used to crash because Chunk had no to_record()
    assert len(CHUNKS) > 0
    assert "text" in CHUNKS[0]


def test_exact_term_query_hits_rank_one():
    # Q01 asks for public liability insurance, and DOC-004 uses those exact
    # words - this is the "known unambiguous query" case BM25 should nail.
    q1 = RESULT["per_query"]["Q01"]
    assert q1["hit_at_1"] is True
    assert q1["mrr"] == 1.0


def test_synonym_poor_query_is_hard_for_bm25_and_the_gap_is_vocabulary_not_ranking():
    # Q05 asks about a vendor "handing the agreement over", but the document
    # says "assign or novate" - no shared words, so this is expected to miss.
    q5 = RESULT["per_query"]["Q05"]
    assert q5["hit_at_5"] is False

    # The top-ranked (wrong) chunk shares a low-IDF word ("agreement") with
    # the anchor, but the term that actually drives its top score is
    # "vendor" (much higher IDF), which has nothing to do with the anchor -
    # "any overlap at all" would misreport this as a near-miss rather than
    # the vocabulary-gap failure it actually is.
    assert q5["top1_terms"][0]["term"] == "vendor"
    assert q5["vocab_overlap_with_anchor"] is False


def test_multi_chunk_query_needs_both_anchors_for_a_full_hit():
    # Q08's two anchors both exist in the corpus -> full hit.
    assert RESULT["per_query"]["Q08"]["multi_chunk_full_hit"] is True
    # Q07 only turns up one of its two anchors in the top 5 -> not a full hit,
    # even though hit_at_5 is still True.
    assert RESULT["per_query"]["Q07"]["multi_chunk_full_hit"] is False
    assert RESULT["per_query"]["Q07"]["hit_at_5"] is True


def test_category_breakdown_covers_every_category():
    categories = RESULT["by_category"]
    assert categories["exact_term"]["count"] == 4
    assert categories["synonym_poor"]["count"] == 2
    assert categories["multi_chunk"]["count"] == 2


def test_no_match_queries_are_reported_separately_with_a_named_floor():
    assert set(RESULT["no_match"].keys()) == {"Q09", "Q10"}
    # they must not leak into the scored categories or the overall average
    assert "no_match" not in RESULT["by_category"]
    for qid, nm in RESULT["no_match"].items():
        assert isinstance(nm["top_score"], float)
        assert isinstance(nm["phrase_support"], bool)
        assert isinstance(nm["correctly_abstained"], bool)
        # the floor is the *sole* decisive rule, so this must hold exactly,
        # not just as a one-directional sufficient condition
        assert nm["correctly_abstained"] == (nm["top_score"] < NO_MATCH_SCORE_FLOOR)


def test_no_match_phrase_evidence_diagnoses_but_never_overrides_the_floor():
    # Q09 ("what interest rate is charged on a late payment?") top-ranks
    # DOC-003's Rate Cards section in 200/40: it shares "rate" and "charged"
    # with the query at high term frequency, but only in the unrelated sense
    # of a billed day-rate, not an interest rate. Neither "interest rate" nor
    # "late payment" appears as an adjacent phrase in that chunk, so
    # phrase_support is False - but that's diagnosis, not a verdict: the
    # score is above the floor, so this is correctly reported as a false
    # positive, not silently waved through.
    q9 = RESULT["no_match"]["Q09"]
    assert q9["top_score"] >= NO_MATCH_SCORE_FLOOR
    assert q9["phrase_support"] is False
    assert q9["correctly_abstained"] is False

    # Q10 ("what penalty fee applies to a late delivery?") in 400/80 top-ranks
    # a chunk that genuinely contains DOC-005's whole "Late Delivery" section
    # - the query's own phrase "late delivery" really does appear adjacently
    # in it, but "penalty fee" never does, so phrase_support is still False.
    # Same rule applies: the floor alone decides, and the score is above it.
    q10 = RESULT_400_80["no_match"]["Q10"]
    assert q10["top_score"] >= NO_MATCH_SCORE_FLOOR
    assert q10["phrase_support"] is False
    assert q10["correctly_abstained"] is False


def test_query_content_bigrams_drops_pairs_touching_a_stopword():
    terms = ["what", "interest", "rate", "is", "charged", "on", "a", "late", "payment"]
    assert query_content_bigrams(terms) == {("interest", "rate"), ("late", "payment")}


def test_has_phrase_support_requires_adjacency_and_every_bigram():
    bigrams = {("interest", "rate")}
    assert has_phrase_support("the interest rate is fixed", bigrams) is True
    # same two words present, but not adjacent - scattered overlap, not a phrase
    assert has_phrase_support("interest accrues at the standard rate", bigrams) is False

    two_phrases = {("penalty", "fee"), ("late", "delivery")}
    # only one of the two required phrases is present - not enough
    assert has_phrase_support("goods receipt handles a late delivery routinely", two_phrases) is False
    # both required phrases present - genuinely supported
    assert has_phrase_support("the penalty fee for a late delivery is fixed", two_phrases) is True


ALL_RESULTS = {"200_40": RESULT, "400_80": RESULT_400_80}
CONFIGS_META = [("200_40", 200, 40), ("400_80", 400, 80)]


def test_pick_winning_config_prefers_hit_at_1_then_breaks_ties_on_mrr():
    # 200/40 has Hit@1=0.625, 400/80 has Hit@1=0.500 - 200/40 must win.
    assert pick_winning_config(ALL_RESULTS) == "200_40"

    # equal Hit@1 -> tie-break on MRR
    fake = {
        "A": {"overall": {"hit_at_1": 0.5, "mrr": 0.6, "hit_at_5": 0.9}},
        "B": {"overall": {"hit_at_1": 0.5, "mrr": 0.7, "hit_at_5": 0.8}},
    }
    assert pick_winning_config(fake) == "B"


SCORED_IDS_FOR_TEST = ["Q01", "Q02", "Q03", "Q04", "Q05", "Q06", "Q07", "Q08"]


def test_pick_worst_query_returns_the_lowest_average_mrr():
    worst = pick_worst_query(ALL_RESULTS)
    avg_mrrs = {
        qid: (RESULT["per_query"][qid]["mrr"] + RESULT_400_80["per_query"][qid]["mrr"]) / 2
        for qid in SCORED_IDS_FOR_TEST
    }
    assert avg_mrrs[worst] == min(avg_mrrs.values())


def test_render_report_includes_every_required_section_and_reflects_no_match_verdicts():
    report = render_report(ALL_RESULTS, CONFIGS_META, QUERIES, NO_MATCH_SCORE_FLOOR)
    for expected in [
        "# Day 1 Retrieval Report",
        "Token counting method",
        "## The configurations",
        "Config 200_40",
        "Config 400_80",
        "## no_match",
        "## Winning configuration",
        "## Worst-performing scored query",
        "## Reproducing this report",
    ]:
        assert expected in report

    # both no_match queries currently score above the floor in both configs,
    # so - now that the floor alone decides - both are correctly reported
    # as false positives, with phrase evidence explaining why (coincidental
    # term overlap, not genuine topical relevance)
    for qid in ["Q09", "Q10"]:
        for label in ["200_40", "400_80"]:
            nm = ALL_RESULTS[label]["no_match"][qid]
            assert nm["correctly_abstained"] == (nm["top_score"] < NO_MATCH_SCORE_FLOOR)
    assert "is a false positive" in report
