"""Calculator tool tests: exact arithmetic, injection safety."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.tools.calculator import calculate


def test_basic_arithmetic():
    assert calculate.invoke({"expression": "5 * 3"})["result"] == "15"
    assert calculate.invoke({"expression": "(12 + 7) / 2"})["result"] == "19/2"


def test_power_and_precedence():
    assert calculate.invoke({"expression": "2 ^ 3 + 1"})["result"] == "9"


def test_rejects_non_arithmetic():
    out = calculate.invoke({"expression": "__import__('os')"})
    assert "error" in out
    out2 = calculate.invoke({"expression": "sqrt(16)"})
    assert "error" in out2  # names are not arithmetic - maths beyond + - * / ^ is taught, not calculated


def test_division_by_zero_teaches_undefined():
    # zoo must never reach a kid's bubble; the model narrates this result
    # as a teaching moment (division by zero IS Class-10 syllabus)
    out = calculate.invoke({"expression": "1/0"})
    assert "undefined" in out["result"] and "zoo" not in out["result"]
    out2 = calculate.invoke({"expression": "5 / (3 - 3)"})
    assert "undefined" in out2["result"]
