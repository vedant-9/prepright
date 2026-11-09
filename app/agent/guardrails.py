"""Deterministic kid-safety guardrails - the floor, not the ceiling.

DESIGN NOTES:
1. PII redaction happens at the API BOUNDARY (before the graph), so phone
   numbers/emails are NEVER stored in checkpoints - data hygiene first.
2. Topic screening runs as a NODE inside the graph (see graph.py) so the
   blocked path visibly routes to END without spending a model call -
   the routing is the teaching example.
3. Deliberately dumb: regex + word-list, zero LLM cost, no false theater.
   Smarter classification can later be a cheap model call on top - the
   deterministic floor stays (belt and suspenders).

OFF-TOPIC SCREEN: two layers, each doing only what it is good at.
1. REGEX FAST-PASS (instant, typo-tolerant stems): math evidence means
   NEVER off-topic. This list is hardcoded ON PURPOSE and is safe to be
   incomplete - a missing stem only falls through to layer 2, never
   refuses a real question (fail-open by construction).
2. TINY LLM CLASSIFIER (open vocabulary): the actual rejecter. Handles any
   phrasing, typo, or language (Hinglish included) that regex can't
   enumerate - hardcoded lists cannot scale to how kids actually talk.
   ~20 output tokens, and only spent when regex finds no evidence.
   Fails OPEN on any error: a broken classifier must never block teaching.
An in-memory cache keeps repeated messages free.
"""

import re
from functools import lru_cache

# Indian mobile numbers (optionally +91 prefixed), compact or 5+5 spaced,
# never matching inside longer digit runs (lookarounds)
PHONE_RE = re.compile(r"(?<!\d)(?:\+91[\s-]?)?(?:[6-9]\d{9}|[6-9]\d{4}[\s-]\d{5})(?!\d)")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")

# Word-boundary matched, case-insensitive. Floor-level list; extend via
# pilot feedback - do not pretend this is comprehensive.
BLOCKED_WORDS = [
    "suicide", "kill myself", "self-harm", "self harm",
    "porn", "nude", "nudes", "gambling", "casino",
]
_BLOCKED_RE = re.compile(r"\b(?:%s)\b" % "|".join(re.escape(w) for w in BLOCKED_WORDS),
                         re.IGNORECASE)

SAFE_REFUSAL = ("I can only help with maths and studying - and I keep our chat "
                "safe. Let's get back to it: which chapter shall we practice?")

OFF_TOPIC_REPLY = ("That's outside maths, so it's not something I can help with - "
                   "I'm your maths tutor! Want to learn a concept or take a "
                   "practice quiz?")

OFF_TOPIC_THRESHOLD = 0.62  # legacy: embeddings threshold, kept for reference

# MATH EVIDENCE (deterministic override, ): mixed messages like
# "who is modi and what is 5 * 3 ?" must never be refused. When hard math
# evidence exists we never classify off-topic: refusing a student's real
# math question is the worst failure mode.
#
#  HARDENING - STEM MATCHING: kids typo constantly. Whole-word
# matching (\bquiz\b) refused "start quizz" - exactly the right request,
# spelled slightly wrong. Stems (quiz -> quizz/quizzes, factori ->
# factorise/factoring/factorization) catch typos and inflections. Short
# ambiguous stems (mean, mode, tan, test, sin, cos) keep their \b.
# Failure direction is deliberate: a false positive reaches layer 2
# or the model, which politely steers - a false negative refuses a real
# question.
MATH_EVIDENCE_RE = re.compile(
    r"(\d\s*[+\-x×*/÷^]\s*\d)"          # arithmetic: 5 * 3, 12+7, x^2
    r"|(\d+\s*%)"                        # percentages
    r"|(=\s*\d|\d\s*=)"                  # equations: = 5
    r"|\b(solv|equat|quadrat|factori|factoriz|fract|decimal|"
    r"hcf|lcm|gcd|percent|probab|trig|sin\b|cos\b|tan\b|"
    r"polynom|algebr|geometr|triangl|circl|angl|area|volume|"
    r"perimeter|mean\b|median|mode\b|statist|graph|theorem|root\b|"
    r"arithmetic|progress|square root|cube\b|prime\b|ratio|proportion|"
    r"integer|rational|chapter|quiz|practic|practis|revis|worksheet|"
    r"learn|teach|concept|test\b|exam\b)",
    re.IGNORECASE)


def redact_pii(text: str) -> str:
    """Strip phones/emails BEFORE anything is stored or sent to a model."""
    text = PHONE_RE.sub("[phone removed]", text)
    text = EMAIL_RE.sub("[email removed]", text)
    return text


def is_blocked(text: str) -> bool:
    """Topic screen: True means refuse without a model call."""
    return bool(_BLOCKED_RE.search(text))


# ---------------------------------------------------------------- off-topic

def has_math_evidence(text: str) -> bool:
    """Deterministic: symbols/operators or math vocabulary present."""
    return bool(MATH_EVIDENCE_RE.search(text))


def is_off_topic(text: str, *, classify=None) -> bool:
    """True if unrelated to maths/study -> canned redirect, zero chat tokens.

    Layered decision (each layer covers the other's weakness):
    1. MATH EVIDENCE fast-pass - explicit symbols/words mean never
       off-topic, even inside a mixed message ("who is modi and 5 * 3?").
       Instant, free, typo-tolerant, and deliberately allowed to be an
       incomplete list (misses fall to layer 2, never refuse).
    2. TINY LLM CLASSIFIER - open-vocabulary rejecter for everything regex
       can't enumerate: typos, Hinglish, slang, novel phrasings.
       `classify` is a test hook to inject the verdict deterministically.
       Fails OPEN on any error: a broken classifier must never block
       maths teaching.
    """
    if has_math_evidence(text):
        return False
    if classify is not None:
        return classify
    try:
        return _classify_with_llm(text)
    except Exception:
        return False  # double armor: even the call site must fail open


@lru_cache(maxsize=512)
def _classify_with_llm(text: str) -> bool:
    """~20-token structured call, only spent when regex finds no evidence.
    Cached so repeated messages (and flapping retries) stay free."""
    try:
        from langchain_core.messages import HumanMessage
        from pydantic import BaseModel

        from app.llm.gateway import generate_structured

        class Relevance(BaseModel):
            is_maths: bool

        out = generate_structured(Relevance, [HumanMessage(content=(
            "You filter messages for a CBSE maths-tutoring app (students aged 10-16). "
            "Is this message about maths, maths homework, quizzes/tests on maths, "
            "chapters, or asking the tutor for maths help? General study intent and "
            "being stuck on schoolwork count as YES. Small typos and any language "
            "(Hindi, Hinglish, etc.) count. Politics, celebrities, relationships, "
            "jokes, games and everything else are NO.\n\n"
            f'Student message: "{text}"'
        ))])
        return not out.is_maths
    except Exception:
        return False  # fail open: never block teaching on a broken classifier

