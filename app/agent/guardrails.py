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

OFF-TOPIC SCREEN (P9.1): "who is modi" used to burn a full chat completion
just to be told no. Now: the best RAG retrieval score against our PURE-MATHS
concept corpus (embeddings are a separate tiny quota; corpus vectors are
disk-cached) - below threshold -> canned redirect, ZERO chat tokens.
Calibrated live: maths/study queries score >= 0.66, chit-chat <= 0.575;
threshold 0.62 has margin on both sides. Fails OPEN if retrieval is
unavailable: a broken classifier must never block maths teaching.
"""

import re

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

OFF_TOPIC_THRESHOLD = 0.62

# MATH EVIDENCE (deterministic override, P9.1): mixed messages like
# "who is modi and what is 5 * 3 ?" score ~0.635 on embeddings - barely
# above the threshold by luck. When hard math evidence exists we never
# classify off-topic: refusing a student's real math question is the worst
# failure mode, so evidence overrides embeddings every time.
#
# P9.4 HARDENING - STEM MATCHING: kids typo constantly. Whole-word
# matching (\bquiz\b) refused "start quizz" - exactly the right request,
# spelled slightly wrong. Stems (quiz -> quizz/quizzes, factori ->
# factorise/factoring/factorization) catch typos and inflections. Short
# ambiguous stems (mean, mode, tan, test, sin, cos) keep their \b.
# Failure direction is deliberate: a false positive reaches the model,
# which politely steers - a false negative refuses a real question.
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


def is_off_topic(text: str, *, top_score=None) -> bool:
    """True if unrelated to maths/study -> canned redirect, zero chat tokens.

    Layered decision (each layer covers the other's weakness):
    1. MATH EVIDENCE override - explicit symbols/words mean never off-topic,
       even inside a mixed message ("who is modi and what is 5 * 3 ?").
    2. Embedding similarity - the whole-message retrieval score vs the pure
       maths corpus, for fuzzy cases with no symbols ("i am confused").

    Decision is separated from the signal for testability: pass top_score
    to exercise the threshold branch deterministically.
    """
    if has_math_evidence(text):
        return False
    if top_score is not None:
        return top_score < OFF_TOPIC_THRESHOLD
    try:
        from app.rag.retriever import retrieve
        best = retrieve(text, k=1)["results"][0]["score"]
        return best < OFF_TOPIC_THRESHOLD
    except Exception:
        return False  # fail open: never block teaching on a broken classifier

