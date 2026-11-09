"""The LLM layer — the ONLY module allowed to import provider SDKs.

This is the vendor-lock-in firewall: every other file talks to
`get_chat_model()` / `chat()` / `generate_structured()` and never
imports ChatGoogleGenerativeAI or ChatGroq directly. Swapping or adding
a provider (OpenAI, Ollama local, ...) touches this file and .env only.

THREE PUBLIC FUNCTIONS:
- get_chat_model()        -> configured chat model instance
- chat()                  -> plain completion with automatic quota fallback
- generate_structured()   -> schema-validated output (Pydantic in, Pydantic out)

Live smoke test (needs a key in .env):
    python -m app.llm.gateway "Reply with exactly: OK"
"""

import logging
from functools import lru_cache
from typing import Type, TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from app.config import settings

log = logging.getLogger("prepright.llm")

T = TypeVar("T", bound=BaseModel)

# Gemini free-tier limits have swung between ~20 and ~1500 requests/day
# across 2025-26, so a quota failure must never kill a user request:
# we detect it and retry once on the other provider.
_QUOTA_HINTS = ("quota", "rate limit", "429", "resource_exhausted", "too many requests", "rate_limit")


@lru_cache(maxsize=4)
def get_chat_model(provider: str | None = None) -> BaseChatModel:
    """Build (and memoize) a chat model for the requested provider.

    lru_cache: model clients are cheap but not free to construct, and are
    safe to reuse across requests. One instance per provider config.
    Imported lazily INSIDE the branches so an unused provider's SDK
    (and its missing API key) never breaks the app.
    """
    provider = provider or settings.llm_provider
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=settings.gemini_model,
            api_key=settings.gemini_api_key,   # explicit, not env-sniffing
            temperature=0.7,                   # tutoring wants warmth; quizzes use structured output anyway
        )
    if provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(
            model=settings.groq_model,
            api_key=settings.groq_api_key,
            temperature=0.7,
        )
    raise ValueError(f"Unknown LLM_PROVIDER: {provider!r} (expected 'gemini' or 'groq')")


def _fallback_provider() -> str | None:
    """The other provider, if it has a key configured."""
    if settings.llm_provider == "gemini" and settings.groq_api_key:
        return "groq"
    if settings.llm_provider == "groq" and settings.gemini_api_key:
        return "gemini"
    return None


def _looks_like_quota_error(exc: Exception) -> bool:
    """
    Honest engineering note: matching on message text is pragmatic, not
    elegant — provider exception classes rename/move between SDK versions
    more often than their error strings change. Narrow enough to avoid
    masking real bugs (auth errors 401/403 must NOT trigger fallback),
    broad enough to catch the variants Google/Groq actually emit.
    """
    text = str(exc).lower()
    return any(hint in text for hint in _QUOTA_HINTS)


def extract_text(content: object) -> str:
    """Normalize AIMessage.content to a plain string.

    REAL-WORLD LESSON (hit in P2 testing): `.content` is NOT always a str.
    Gemini 3.x returns a list of content blocks
    ([{'type': 'text', 'text': ..., 'extras': ...}]); other providers
    return plain strings. Callers shouldn't care — the LLM layer's job
    is to absorb provider differences (that's the firewall's whole point).
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return str(content)


def chat(messages: list[BaseMessage]) -> str:
    """Plain completion. Falls back to the alternate provider once on quota errors.

    Raises the original error if both providers fail or the error isn't quota-related.
    """
    model = get_chat_model()
    try:
        return extract_text(model.invoke(messages).content)
    except Exception as exc:
        fallback = _fallback_provider()
        if not _looks_like_quota_error(exc) or fallback is None:
            raise
        log.warning("primary provider (%s) hit quota: %s — falling back to %s",
                    settings.llm_provider, exc, fallback)
        return extract_text(get_chat_model(fallback).invoke(messages).content)


def generate_structured(schema: Type[T], messages: list[BaseMessage]) -> T:
    """Schema-validated generation: pass a Pydantic model, get an instance back.

    Under the hood `with_structured_output()` flips the provider's
    native JSON-schema mode on (Gemini: response_schema; Groq: tool
    calling) — the model is *forced* to emit schema-conforming JSON,
    so downstream code never parses free text. This is what makes
    quiz generation (P5) reliable.

    Quota fallback (added in P5): quiz generation is the app's most
    important structured call - it gets the same one-shot provider
    fallback as chat(), closing the gap noted in the P2 review.
    """
    try:
        return get_chat_model().with_structured_output(schema).invoke(messages)
    except Exception as exc:
        fallback = _fallback_provider()
        if not _looks_like_quota_error(exc) or fallback is None:
            raise
        log.warning("primary provider (%s) hit quota on structured call: %s — falling back to %s",
                    settings.llm_provider, exc, fallback)
        return get_chat_model(fallback).with_structured_output(schema).invoke(messages)


if __name__ == "__main__":
    # Smoke test: `python -m app.llm "your prompt"` — one live call.
    import sys

    from langchain_core.messages import HumanMessage

    prompt = sys.argv[1] if len(sys.argv) > 1 else "Say OK."
    print(chat([HumanMessage(content=prompt)]))
