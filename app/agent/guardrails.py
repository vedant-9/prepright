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
"""

import re

# Indian mobile numbers (optionally +91 prefixed), not part of longer digits
PHONE_RE = re.compile(r"(?<!\d)(?:\+91[\s-]?)?[6-9]\d{9}(?!\d)")
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


def redact_pii(text: str) -> str:
    """Strip phones/emails BEFORE anything is stored or sent to a model."""
    text = PHONE_RE.sub("[phone removed]", text)
    text = EMAIL_RE.sub("[email removed]", text)
    return text


def is_blocked(text: str) -> bool:
    """Topic screen: True means refuse without a model call."""
    return bool(_BLOCKED_RE.search(text))
