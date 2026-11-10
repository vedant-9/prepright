"""Central configuration — everything comes from environment (.env), 12-factor style.

KEY DESIGN: LLM_PROVIDER is the vendor-lock-in escape hatch.
Change one env var to move between Gemini (free tier, default) and Groq
(free fallback) — no code changes anywhere else in the app. See app/llm.py.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the repo root (prepright/.env) — gitignored, holds secrets.
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Settings:
    """App-wide settings, read once at import time."""

    # --- LLM provider chain ---------------------------------------------
    # "gemini" (default, generous free tier) or "groq" (fallback when
    # Gemini quota is exhausted — Gemini free-tier limits have swung
    # between ~20 and ~1500 requests/day, so we never hard-depend on it).
    llm_provider: str = os.getenv("LLM_PROVIDER", "gemini")

    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")

    # Flash-tier models: cheap + fast, plenty for a tutoring agent.
    # TWO real-world lessons baked in (both hit mid-project):
    # 1. Model IDs get RETIRED (gemini-2.0-flash 404'd mid-project).
    # 2. Same tier family, wildly different latency: 3.8-flash took
    #    50-120s to first token (thinking + free-tier queueing);
    #    3.5-flash measured 1.1s. Benchmark before choosing.
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
    groq_model: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    # Same-provider fallback: Groq rate limits are PER-MODEL, so a second
    # Groq model has its own quota bucket.
    groq_fallback_model: str = os.getenv("GROQ_FALLBACK_MODEL", "openai/gpt-oss-20b")

    # Embeddings (RAG, ): free tier, embedded ONCE and disk-cached.
    gemini_embedding_model: str = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")

    # --- Runtime flags ----------------------------------------------------
    # DEMO_MODE=true serves cached/replayed responses so live demos and
    # the pilot never die on an API quota error.
    demo_mode: bool = os.getenv("DEMO_MODE", "false").lower() == "true"


settings = Settings()
