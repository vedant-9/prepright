"""guardrail tests - the kid-safety floor, deterministically proven.

The guardrail is the layer that must NEVER regress silently; these tests
pin its exact behavior including the word-boundary subtleties.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent.guardrails import (BLOCKED_WORDS, is_blocked, OFF_TOPIC_REPLY,
                                  redact_pii, SAFE_REFUSAL)


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


# ---- off-topic screen ------

from app.agent import guardrails as gd  # noqa: E402
from app.agent.guardrails import is_off_topic  # noqa: E402


def test_regex_evidence_short_circuits_no_classifier_call(monkeypatch):
    # evidence = instant pass; the LLM classifier must never be consulted
    monkeypatch.setattr(gd, "_classify_with_llm",
                        lambda t: (_ for _ in ()).throw(AssertionError("should not classify")))
    assert is_off_topic("solve for x") is False


def test_classifier_rejects_when_no_evidence(monkeypatch):
    monkeypatch.setattr(gd, "_classify_with_llm", lambda t: True)
    assert is_off_topic("who is modi") is True


def test_classifier_passes_ambiguous_study_intent(monkeypatch):
    # e.g. Hinglish / slang the regex cannot enumerate
    monkeypatch.setattr(gd, "_classify_with_llm", lambda t: False)
    assert is_off_topic("ganit mein atka hoon") is False


def test_classifier_fails_open_on_error(monkeypatch):
    monkeypatch.setattr(gd, "_classify_with_llm",
                        lambda t: (_ for _ in ()).throw(RuntimeError("provider down")))
    assert is_off_topic("who is modi") is False  # fail open, never block


def test_offtopic_reply_redirects_to_maths():
    assert "maths" in OFF_TOPIC_REPLY.lower()


# ---- mixed-intent queries (the "who is modi and what is 5 * 3" case) ----

from app.agent.guardrails import has_math_evidence  # noqa: E402


def test_mixed_message_math_evidence_overrides_classifier(monkeypatch):
    # even a classifier screaming "off-topic" must not refuse a real math
    # question embedded in a mixed message (deterministic fast-pass wins)
    monkeypatch.setattr(gd, "_classify_with_llm", lambda t: True)
    assert is_off_topic("who is modi and what is 5 * 3 ?") is False


def test_math_evidence_detector_symbols():
    assert has_math_evidence("what is 5 * 3") is True
    assert has_math_evidence("solve x^2 - 5x + 6 = 0") is True
    assert has_math_evidence("if 12 + 7 then what?") is True


def test_math_evidence_detector_words():
    assert has_math_evidence("explain the HCF please") is True
    assert has_math_evidence("can we do a practice quiz") is True


def test_pure_chitchat_has_no_math_evidence():
    assert has_math_evidence("who is modi") is False
    assert has_math_evidence("tell me a joke") is False


# ---- kid typos must not be refused ----

def test_typo_quizz_passes_evidence():
    # the exact regression from the user's live test
    assert has_math_evidence("start quizz") is True
    assert is_off_topic("start quizz") is False


def test_common_misspellings_pass_evidence():
    assert has_math_evidence("teach me algerbra") is True     # stem: algebr
    assert has_math_evidence("help me with factorising") is True  # factori
    assert has_math_evidence("i want to practise trig") is True    # practis/trig
    assert has_math_evidence("revise quadritic equations") is True  # revis/quadrat/equat


def test_pure_off_topic_blocked_by_classifier(monkeypatch):
    # no evidence -> the open-vocabulary classifier decides
    monkeypatch.setattr(gd, "_classify_with_llm", lambda t: True)
    assert is_off_topic("who is modi") is True
