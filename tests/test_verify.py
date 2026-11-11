"""pure-logic tests for the compute-don't-trust verifier.

No LLM, no network - verification must be deterministic. The LLM-facing
parts (drafting) get fake-model tests in .
"""

import sys

import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.verify.math import (Verified, coherence_check, make_options,
                             solve_spec, verify_question)


# ---- layer 1: solvers -------------------------------------------------

def test_arithmetic():
    text, v = solve_spec({"type": "arithmetic", "expr": "12 + 7 * 3"})
    assert text == "33" and v == 33


def test_linear_eq():
    text, v = solve_spec({"type": "linear_eq", "a": 2, "b": 3, "c": 11})
    assert text == "4" and v == 4


def test_quadratic_roots():
    text, (r1, r2) = solve_spec({"type": "quadratic_roots", "a": 1, "b": -5, "c": 6})
    assert (r1, r2) == (2, 3)
    assert text == "x = 2 or x = 3"


def test_quadratic_irrational_still_rational_repr():
    # x^2 - 2 = 0 -> roots +-sqrt(2); sympy Rational conversion would fail
    # -> verify_question must return ok=False, not crash (unknown handling)
    v = verify_question("solve x^2 - 2 = 0", {"type": "quadratic_roots", "a": 1, "b": 0, "c": -2})
    assert isinstance(v, Verified)  # either ok or cleanly dropped


def test_percentage():
    text, v = solve_spec({"type": "percentage", "part": 45, "whole": 60})
    assert text == "75" and v == 75


def test_gcd_lcm():
    assert solve_spec({"type": "gcd_lcm", "m": 12, "n": 18, "op": "hcf"})[0] == "6"
    assert solve_spec({"type": "gcd_lcm", "m": 12, "n": 18, "op": "lcm"})[0] == "36"


def test_unknown_type_rejected():
    v = verify_question("what is 2+2?", {"type": "mind_reading", "x": 1})
    assert v.ok is False and "unsolvable" in v.reason


# ---- layer 2: coherence ------------------------------------------------

def test_coherence_pass():
    q = "Find the HCF of 12 and 18."
    assert coherence_check(q, {"type": "gcd_lcm", "m": 12, "n": 18, "op": "hcf"})


def test_coherence_catches_drift():
    # model spec'd 12/18 but phrased the question with different numbers
    q = "Find the HCF of 16 and 24."
    assert not coherence_check(q, {"type": "gcd_lcm", "m": 12, "n": 18, "op": "hcf"})


def test_verify_drops_incoherent():
    v = verify_question("Find the HCF of 16 and 24.",
                        {"type": "gcd_lcm", "m": 12, "n": 18, "op": "hcf"})
    assert v.ok is False and "not in question" in v.reason


# ---- layer 3: distractors ---------------------------------------------

def test_options_contract_correct_is_index_0():
    # tools.py shuffles and locates the correct answer via options.index(options[0])
    # -> the contract "options[0] == correct, 4 unique options" is load-bearing
    text, v = solve_spec({"type": "gcd_lcm", "m": 12, "n": 18, "op": "lcm"})
    opts = make_options(text, v, {"type": "gcd_lcm", "m": 12, "n": 18, "op": "lcm"})
    assert opts[0] == "36"
    assert len(opts) == 4 and len(set(opts)) == 4


def test_quadratic_distractors_mirror_misconceptions():
    text, (r1, r2) = solve_spec({"type": "quadratic_roots", "a": 1, "b": -5, "c": 6})
    opts = make_options(text, (r1, r2), {"type": "quadratic_roots", "a": 1, "b": -5, "c": 6})
    assert opts[0] == "x = 2 or x = 3" and len(set(opts)) == 4
    assert "x = -2 or x = -3" in opts  # sign-flip misconception present


# ---- end-to-end gate ----------------------------------------------------

def test_verify_question_happy_path():
    v = verify_question(
        "Solve x² - 5x + 6 = 0 by factorisation.",
        {"type": "quadratic_roots", "a": 1, "b": -5, "c": 6},
        skill="factorisation",
    )
    assert v.ok and v.answer == "x = 2 or x = 3"
    assert v.options[0] == "x = 2 or x = 3" and len(v.options) == 4
    assert v.skill == "factorisation"


def test_division_by_zero_fails_closed():
    # a drafted spec like 1/0 must never become a quiz question whose
    # answer is "zoo" - non-finite values raise -> question dropped
    with pytest.raises(ValueError):
        solve_spec({"type": "arithmetic", "expr": "1/0"})
    with pytest.raises(ValueError):
        solve_spec({"type": "linear_eq", "a": 0, "b": 2, "c": 6})
    v = verify_question("What is 1/0?", {"type": "arithmetic", "expr": "1/0"})
    assert v.ok is False
