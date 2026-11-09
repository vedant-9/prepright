"""Prompt assets for tool-backed generation.

PROMPTS AS ASSETS: they change as often as code but for different reasons
(model behavior drift, pedagogy tuning) - keeping them in dedicated modules
makes them easy to find, diff, and later version (registry/registry pattern
in bigger shops).
"""

SPEC_MENU = """Allowed spec types (JSON "spec" must be exactly one of these):
- {{"type": "arithmetic", "expr": "12 + 7 * 3"}}              -> integer arithmetic
- {{"type": "linear_eq", "a": 2, "b": 3, "c": 11}}            -> solve a*x + b = c
- {{"type": "quadratic_roots", "a": 1, "b": -5, "c": 6}}      -> solve a*x^2 + b*x + c = 0
- {{"type": "evaluate", "expr": "x**2 + 5*x + 6", "val": 2}}  -> evaluate at a value
- {{"type": "percentage", "part": 45, "whole": 60}}           -> part is what % of whole
- {{"type": "gcd_lcm", "m": 12, "n": 18, "op": "hcf"}}        -> op is "hcf" or "lcm" """

DIFFICULTY_GUIDE = {
    1: "single step, small friendly numbers",
    2: "one or two steps, numbers under 30",
    3: "two steps, standard chapter-exercise level",
    4: "multi-step or word-problem framing",
    5: "challenge level, multi-step with traps",
}
