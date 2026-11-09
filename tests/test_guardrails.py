"""P9: guardrail tests - the kid-safety floor, deterministically proven.

The guardrail is the layer that must NEVER regress silently; these tests
pin its exact behavior including the word-boundary subtleties.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent.guardrails import (BLOCKED_WORDS, is_blocked, redact_pii,
                                  SAFE_REFUSAL)


# ---- PII redaction -----------------------------------------------------

def test_redacts_indian_mobile_number():
    assert redact_pii("my number is 9876543210 call me") == \
        "my number is [phone removed] call me"


def test_redacts_number_with_country_code_and_spaces():
    assert "[phone removed]" in redact_pii("+91 98765 43210")


def test_redacts_email():
    assert "[email removed]" in redact_pii("mail me at kid@example.com please")


def test_leaves_normal_math_alone():
    msg = "what is 12 + 7 times 3 divided by 2?"
    assert redact_pii(msg) == msg


def test_does_not_mangle_long_numbers_in_math():
    # a 10-digit sequence inside longer digits must NOT match (lookarounds)
    assert redact_pii("1234569876543210") == "1234569876543210"


# ---- topic screen -------------------------------------------------------

def test_blocks_self_harm_phrase():
    assert is_blocked("i want to kill myself") is True


def test_blocks_single_word():
    assert is_blocked("tell me about suicide") is True


def test_all_blocklist_words_are_detected():
    for w in BLOCKED_WORDS:
        assert is_blocked(f"something {w} something") is True, w


def test_math_talk_passes():
    assert is_blocked("help me with quadratic equations") is False
    assert is_blocked("what is the HCF of 12 and 18") is False


def test_refusal_is_safe_and_redirects():
    assert "maths" in SAFE_REFUSAL.lower() and "chapter" in SAFE_REFUSAL.lower()
