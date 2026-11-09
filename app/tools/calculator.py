"""The calculate tool: sympy-backed arithmetic for casual chat questions.

Extends compute-don't-trust from quizzes to ALL maths the tutor states:
the model may DECIDE what to compute, but never computes itself. Every
numeric answer it gives originates from this tool (or verified quiz keys).
"""

import re

from langchain_core.tools import tool
from sympy import Rational, sympify

# digits, + - * / ( ) . ^ and spaces only - no names, no imports possible
_SAFE_EXPR = re.compile(r"^[\d+\-*/().^ ]+$")


@tool
def calculate(expression: str) -> dict:
    """Evaluate an arithmetic expression exactly (integers and fractions).
    Use this for EVERY calculation you mention in your reply - never do
    arithmetic yourself. Example: calculate("5 * 3") -> {"result": 15}."""
    expression = expression.replace("^", "**").strip()
    if not _SAFE_EXPR.match(expression):
        return {"error": "only numbers and + - * / ( ) ^ allowed"}
    try:
        value = sympify(expression)
    except Exception as exc:
        return {"error": f"could not parse: {exc}"}
    if isinstance(value, Rational) and value.q == 1:
        value = value.p
    return {"result": str(value), "expression": expression}
