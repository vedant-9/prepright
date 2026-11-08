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
    # NOTE: model IDs get RETIRED (gemini-2.0-flash died mid-2026 with a
    # 404 "no longer available"). That's why this is env-config, not a
    # hardcoded constant — a model swap must never require a code deploy.
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    # --- Runtime flags ----------------------------------------------------
    # DEMO_MODE=true serves cached/replayed responses so live demos and
    # the pilot never die on an API quota error (P5+ implement this).
    demo_mode: bool = os.getenv("DEMO_MODE", "false").lower() == "true"


settings = Settings()
