"""The RAG tool: the tutor's window into the concept-card corpus."""

from langchain_core.tools import tool


@tool
def lookup_concept(topic: str) -> dict:
    """Fetch curriculum-aligned concept cards for a maths topic: a clear
    explanation, a worked example, and common misconceptions to watch for.
    Call this BEFORE teaching any new concept so your explanation follows
    the course material; teach FROM the returned card."""
    from app.rag.retriever import retrieve

    return retrieve(topic, k=3)
