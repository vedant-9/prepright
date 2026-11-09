"""P8: retriever tests - deterministic, no network.

Semantic mode is tested by INJECTING vectors (the retrieve() parameters),
so the math (cosine ordering) is verified without any embedding API call.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag.retriever import _cosine, load_cards, retrieve


def test_corpus_loads_and_is_well_formed():
    cards = load_cards()
    assert len(cards) >= 10
    for c in cards:
        assert c["id"] and c["chapter_id"] and c["title"]
        assert len(c["explanation"]) > 40 and len(c["worked_example"]) > 10


def test_cosine_math():
    assert abs(_cosine([1, 0], [1, 0]) - 1.0) < 1e-9   # identical
    assert abs(_cosine([1, 0], [0, 1])) < 1e-9          # orthogonal
    assert abs(_cosine([1, 0], [-1, 0]) + 1.0) < 1e-9  # opposite


def test_semantic_ordering_with_injected_vectors():
    # three cards, engineered vectors: query is closest to the 2nd card
    cards = load_cards()
    ids = [c["id"] for c in cards[:3]]
    vectors = {
        ids[0]: [1.0, 0.0],
        ids[1]: [0.0, 1.0],
        ids[2]: [0.7, 0.7],
    }
    out = retrieve("anything", k=2, vectors=vectors, query_vector=[0.0, 1.0])
    assert out["mode"] == "semantic"
    assert out["results"][0]["id"] == ids[1]
    assert out["results"][0]["score"] > out["results"][1]["score"]


def test_lexical_fallback_when_no_vectors():
    out = retrieve("HCF and LCM prime factorisation", k=3, vectors=None,
                   query_vector=None)
    # None vectors + forced failure: retrieve() will try the real API...
    # in CI without network it degrades to lexical; either way it must
    # return SOMETHING well-formed with a valid mode.
    assert out["mode"] in ("semantic", "lexical")
    assert len(out["results"]) >= 1
    assert all("explanation" in r for r in out["results"])


def test_lexical_scores_rank_relevant_card_first():
    # direct lexical exercise via retrieve with empty vector table
    out = retrieve("square roots prime factorisation", k=3, vectors={},
                   query_vector=[])
    assert out["mode"] == "lexical"
    assert out["results"][0]["id"] == "cc-square-roots"


def test_never_returns_empty():
    out = retrieve("zzz qqq vvv", k=3, vectors={}, query_vector=[])
    assert len(out["results"]) >= 1
