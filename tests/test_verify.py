"""pure-logic tests for the compute-don't-trust verifier.

No LLM, no network - verification must be deterministic. The LLM-facing
parts (drafting) get fake-model tests in .
"""

import sys

import pytest
from pathlib import Path
from sympy import Rational, zoo

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


# ------------------------------------------------------------ new spec types

def test_trig_value_exact():
    text, v = solve_spec({"type": "trig_value", "fn": "sin", "deg": 30})
    assert text == "1/2" and v == Rational(1, 2)
    text, v = solve_spec({"type": "trig_value", "fn": "sin", "deg": 45})
    assert text == "√2/2"                     # CBSE form, not "sqrt(2)/2"
    text, v = solve_spec({"type": "trig_value", "fn": "tan", "deg": 30})
    assert text == "√3/3"
    text, v = solve_spec({"type": "trig_value", "fn": "cos", "deg": 90})
    assert text == "0"


def test_trig_tan90_and_nonstandard_fail_closed():
    with pytest.raises(ValueError):           # tan 90 -> zoo
        solve_spec({"type": "trig_value", "fn": "tan", "deg": 90})
    with pytest.raises(ValueError):           # not a standard angle
        solve_spec({"type": "trig_value", "fn": "sin", "deg": 50})
    with pytest.raises(ValueError):           # not a supported function
        solve_spec({"type": "trig_value", "fn": "sec", "deg": 30})


def test_trig_coherence_requires_fn_word():
    spec = {"type": "trig_value", "fn": "cos", "deg": 60}
    assert coherence_check("What is cos 60 degrees?", spec)
    assert not coherence_check("What is sin 60 degrees?", spec)  # phrased sin, spec'd cos


def test_trig_distractors_mirror_misconceptions():
    spec = {"type": "trig_value", "fn": "sin", "deg": 30}
    text, v = solve_spec(spec)
    opts = make_options(text, v, spec)
    assert opts[0] == "1/2" and len(opts) == 4 and len(set(opts)) == 4
    assert "√3/2" in opts                     # cos 30: the sin<->cos confusion


def test_stats_basic_all_three():
    assert solve_spec({"type": "stats_basic", "nums": [4, 6, 9, 3, 5], "stat": "mean"}) == ("27/5", Rational(27, 5))
    assert solve_spec({"type": "stats_basic", "nums": [3, 4, 5, 6, 9], "stat": "median"})[0] == "5"
    assert solve_spec({"type": "stats_basic", "nums": [1, 2, 3, 4], "stat": "median"})[0] == "5/2"
    assert solve_spec({"type": "stats_basic", "nums": [2, 2, 3, 7], "stat": "mode"})[0] == "2"


def test_stats_mode_tie_and_empty_fail_closed():
    with pytest.raises(ValueError):
        solve_spec({"type": "stats_basic", "nums": [1, 1, 2, 2], "stat": "mode"})
    with pytest.raises(ValueError):
        solve_spec({"type": "stats_basic", "nums": [], "stat": "mean"})
    with pytest.raises(ValueError):
        solve_spec({"type": "stats_basic", "nums": [1, 2], "stat": "range"})


def test_stats_coherence_checks_every_datum():
    spec = {"type": "stats_basic", "nums": [4, 6, 9], "stat": "mean"}
    assert coherence_check("Find the mean of 4, 6 and 9", spec)
    assert not coherence_check("Find the mean of 4 and 6", spec)  # 9 missing from text


def test_stats_distractors_are_the_other_stats():
    spec = {"type": "stats_basic", "nums": [3, 4, 5, 6, 9], "stat": "mean"}
    text, v = solve_spec(spec)                # mean = 27/5
    opts = make_options(text, v, spec)
    assert opts[0] == "27/5" and len(opts) == 4 and len(set(opts)) == 4
    assert "5" in opts                        # the median - mean/median confusion


def test_ap_term():
    text, v = solve_spec({"type": "ap_term", "a": 3, "d": 4, "n": 10})
    assert text == "39" and v == 39


def test_ap_term_distractors_off_by_one():
    spec = {"type": "ap_term", "a": 3, "d": 4, "n": 10}
    text, v = solve_spec(spec)
    opts = make_options(text, v, spec)
    assert opts[0] == "39" and len(opts) == 4 and len(set(opts)) == 4
    assert "43" in opts                       # a + n*d: used n instead of n-1
    assert "30" in opts                       # n*a: multiplied


def test_new_types_pass_the_gate():
    v = verify_question("What is sin 30 degrees?", {"type": "trig_value", "fn": "sin", "deg": 30})
    assert v.ok and v.answer == "1/2"
    v = verify_question("Find the mean of 4, 6 and 9",
                        {"type": "stats_basic", "nums": [4, 6, 9], "stat": "mean"})
    assert v.ok and v.answer == "19/3"
    v = verify_question("An AP starts at 3 with common difference 4. What is the 10th term?",
                        {"type": "ap_term", "a": 3, "d": 4, "n": 10})
    assert v.ok and v.answer == "39"


def test_percentage_zero_whole_fails_closed():
    """whole=0 -> zoo. This was the ONE branch without the non-finite guard,
    and a zoo answer makes answer+k == answer forever, so the padding loop
    would never terminate. The builder must drop the question instead."""
    with pytest.raises(ValueError):
        solve_spec({"type": "percentage", "part": 45, "whole": 0})


def test_distractor_padding_never_hangs():
    """The class-level guarantee: whatever a future spec type emits as an
    answer, make_options must terminate - drop the question rather than spin."""
    import threading
    result = {}
    def run():
        try:
            make_options("zoo", zoo, {"type": "arithmetic", "expr": "1/0"})
            result["outcomes"] = "returned"
        except ValueError:
            result["outcomes"] = "raised"
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=10)              # must terminate - bounded by design
    assert not t.is_alive(), "make_options hung"
    assert result.get("outcomes") == "raised"
