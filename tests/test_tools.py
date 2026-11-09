"""P9.1: tool argument clamp tests - the model supplies parameters,
so every numeric arg that crosses the model->tool boundary must be
clamped before it can burn quota or poison mastery data."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.tools.progress import record_progress


def test_generate_quiz_clamps_absurd_difficulty_and_n(monkeypatch):
    """difficulty=99, n=50 must clamp to 5/5 BEFORE any LLM call.
    We stub the generator: if clamping failed, the stub would see the
    raw values and the test fails; the stub also prevents API spend."""
    from app.tools import quiz as quiz_mod
    seen = {}

    def fake_verified(chapter, difficulty, n):
        seen["difficulty"], seen["n"] = difficulty, n
        return [{"question_text": "1+1?", "options": ["2", "3", "4", "5"],
                 "answer_index": 0, "answer": "2", "skill": "t", "verified": True}]

    monkeypatch.setattr(quiz_mod, "_verified_quiz", fake_verified)
    out = quiz_mod.generate_quiz_core("g10-c-quadratic-eq", difficulty=99, n=50)
    assert seen == {"difficulty": 5, "n": 5}          # clamped, not trusted
    assert out["difficulty"] == 5


def test_generate_quiz_clamps_zero_upward(monkeypatch):
    from app.tools import quiz as quiz_mod
    seen = {}
    monkeypatch.setattr(quiz_mod, "_verified_quiz",
                        lambda c, d, n: seen.update(d=d, n=n) or [])
    quiz_mod.generate_quiz_core("g10-c-quadratic-eq", difficulty=0, n=0)
    assert seen == {"d": 1, "n": 1}                   # floors, not just ceilings


def test_record_progress_clamps_forged_totals(tmp_path, monkeypatch):
    """total=999 from a hallucinating model must never reach the DB.
    (Bonus discovered by this test: the FK on student_id rejects progress
    for non-existent students - a DB-level guard against forged ids.)"""
    from app.data import db as db_mod
    db_mod.DB_PATH = tmp_path / "t.db"
    db_mod.init_db()
    with db_mod.get_conn() as conn:
        conn.execute("INSERT INTO students (name, grade) VALUES ('T', 10)")
    out = record_progress.invoke({"student_id": 1, "chapter_id": "x",
                                  "difficulty": 99, "correct": 500, "total": 999})
    assert out == {"recorded": True}
    with db_mod.get_conn() as conn:
        row = conn.execute("SELECT difficulty, correct, total FROM progress_events").fetchone()
    assert (row["difficulty"], row["correct"], row["total"]) == (5, 50, 50)
