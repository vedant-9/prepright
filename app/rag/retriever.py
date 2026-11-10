"""The RAG retriever: corpus -> embeddings -> cosine search (with fallback).

THE TWO-PHASE RAG PATTERN (this is the canonical shape):
  INGESTION (once):  15 concept cards -> embed each -> disk cache
                     (keyed by content hash, so editing a card re-embeds
                     only when content actually changed)
  RUNTIME (per lookup): embed the query with the SAME model -> cosine
                     similarity vs every card -> top-k back

WHY NO VECTOR DATABASE: 15 cards x ~768 dims is 15 dot products - a Python
loop answers in microseconds. FAISS/pgvector/Pinecone pay off at ~100k+
vectors; before that they are a moving part.

FALLBACK: if the embedding API is unavailable (no key / quota), retrieval
degrades to lexical scoring (keyword overlap) instead of failing - the
tutor still teaches from cards, just with dumber matching. The response
reports which mode ran, so we never silently pretend semantic quality.

Demo: python -m app.rag.retriever "how do I factorise a quadratic"
"""

import json
import math
from functools import lru_cache
from pathlib import Path

from app.llm.embeddings import embed_query, embed_texts

CORPUS_PATH = Path(__file__).resolve().parent.parent.parent / "content" / "math_concepts.json"
EMBED_CACHE_PATH = (CORPUS_PATH.parent.parent / "data" / "cache" / "embeddings.json")


# ---------------------------------------------------------------- corpus

@lru_cache(maxsize=1)
def load_cards() -> list[dict]:
    """Load concept cards (file changes only between deploys)."""
    data = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    cards = data["cards"]
    for c in cards:
        for field in ("id", "chapter_id", "title", "explanation", "worked_example"):
            if field not in c:
                raise ValueError(f"concept card {c.get('id', '?')!r} missing {field!r}")
    return cards


def _card_text(card: dict) -> str:
    """What the embedding model actually 'reads' per card."""
    parts = [card["title"], card["explanation"], card["worked_example"]]
    parts.extend(card.get("misconceptions", []))
    return " \n ".join(parts)


def _corpus_hash() -> str:
    return str(len(load_cards())) + "-" + str(hash(json.dumps(
        [_card_text(c) for c in load_cards()], sort_keys=True)))


# ---------------------------------------------------------------- vectors

def _load_cached_vectors() -> dict | None:
    """Disk cache: {corpus_hash: {card_id: vector}} - single active entry."""
    try:
        blob = json.loads(EMBED_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    entry = blob.get(_corpus_hash())
    return entry if entry else None


def get_corpus_vectors() -> dict | None:
    """Card-id -> embedding vector, embedding at most once per corpus
    version. Returns None if the embedding API is unavailable (caller
    falls back to lexical)."""
    if (cached := _load_cached_vectors()) is not None:
        return cached
    try:
        texts = [_card_text(c) for c in load_cards()]
        vectors = embed_texts(texts)
    except Exception:
        return None  # no key / quota -> lexical mode; never crash teaching
    table = {card["id"]: vec for card, vec in zip(load_cards(), vectors)}
    EMBED_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EMBED_CACHE_PATH.write_text(
        json.dumps({_corpus_hash(): table}), encoding="utf-8")
    return table


# ---------------------------------------------------------------- scoring

def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _lexical_score(query: str, card: dict) -> float:
    """Keyword-overlap fallback: honest dumb matching for offline mode."""
    q = {w.strip(".,?!").lower() for w in query.split() if len(w) > 2}
    text = _card_text(card).lower()
    title = card["title"].lower()
    return sum(2.0 if w in title else 1.0 for w in q if w in text)


def retrieve(query: str, k: int = 3, *, vectors: dict | None = None,
             query_vector: list[float] | None = None) -> dict:
    """Top-k concept cards for a topic. vectors/query_vector injectable for
    deterministic tests (no network in tests)."""
    cards = load_cards()

    if vectors is None:
        vectors = get_corpus_vectors()

    if vectors:
        if query_vector is None:
            try:
                query_vector = embed_query(query)
            except Exception:
                vectors = None  # degrade rather than fail
        mode = "semantic"
    if not vectors or query_vector is None:
        mode = "lexical"

    if mode == "semantic":
        # guard: skip cards without a vector (stale cache edge case);
        # production always has a full table, injected test tables may not
        scored = [(_cosine(query_vector, vectors[c["id"]]), c)
                  for c in cards if c["id"] in vectors]
    else:
        scored = [(_lexical_score(query, c), c) for c in cards]

    scored.sort(key=lambda pair: pair[0], reverse=True)
    results = [
        {
            "id": c["id"],
            "chapter_id": c["chapter_id"],
            "title": c["title"],
            "explanation": c["explanation"],
            "worked_example": c["worked_example"],
            "misconceptions": c.get("misconceptions", []),
            "score": round(s, 4),
        }
        for s, c in scored[:k] if s > 0
    ] or [{"id": c["id"], "chapter_id": c["chapter_id"], "title": c["title"],
           "explanation": c["explanation"],
           "worked_example": c["worked_example"],
           "misconceptions": c.get("misconceptions", []), "score": 0.0}
          for c in cards[:1]]  # never return nothing - give the closest card

    return {"mode": mode, "query": query, "results": results}


if __name__ == "__main__":
    import sys

    q = " ".join(sys.argv[1:]) or "how do I factorise a quadratic"
    out = retrieve(q, k=3)
    print(f"mode: {out['mode']}  query: {q!r}")
    for r in out["results"]:
        print(f"  {r['score']:8.4f}  {r['title']}  [{r['id']}]")
