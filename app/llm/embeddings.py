"""Embedding client - behind the vendor firewall like the chat models.

Embeddings turn text into vectors where similar meaning lands nearby
(cosine similarity). One tiny model, two calls: embed_texts for the corpus
(once, then disk-cached) and embed_query per lookup.
"""

from functools import lru_cache

from app.config import settings


@lru_cache(maxsize=1)
def _get_embedder():
    from langchain_google_genai import GoogleGenerativeAIEmbeddings  # lazy: firewall rule

    return GoogleGenerativeAIEmbeddings(
        model=settings.gemini_embedding_model,
        api_key=settings.gemini_api_key,
    )


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of documents (corpus side)."""
    return _get_embedder().embed_documents(texts)


def embed_query(text: str) -> list[float]:
    """Embed one query. MUST come from the same model as the corpus, or
    cosine similarity is meaningless - vectors from different models live
    in different spaces."""
    return _get_embedder().embed_query(text)
