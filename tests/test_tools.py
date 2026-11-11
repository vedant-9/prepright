"""tool argument clamp tests - the model supplies parameters,
so every numeric arg that crosses the model->tool boundary must be
clamped before it can burn quota or poison mastery data."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.tools.progress import record_progress


def test_generate_quiz_clamps_absurd_difficulty_and_n(monkeypatch):
    """difficulty=99, n=50 must clamp to 5/5 BEFORE any LLM call.
    Cache is bypassed so the stubbed generator runs; the stub records what
    it was actually called with (and prevents API spend). Side-proof found
    while writing this: even WITHOUT the bypass, the clamp is observable -
    the cache hit on key (chapter,5,5) only exists because clamping fired."""
    from app.tools import quiz as quiz_mod
    seen = {}

    def fake_verified(chapter, difficulty, n):
        seen["difficulty"], seen["n"] = difficulty, n
        return [{"question_text": "1+1?", "options": ["2", "3", "4", "5"],
                 "answer_index": 0, "answer": "2", "skill": "t", "verified": True}]

    monkeypatch.setattr(quiz_mod, "_verified_quiz", fake_verified)
    monkeypatch.setattr(quiz_mod.cache, "get", lambda *a, **k: None)   # no cache hits
    monkeypatch.setattr(quiz_mod.cache, "put", lambda *a: None)   # no writes
    out = quiz_mod.generate_quiz_core("g10-c-quadratic-eq", difficulty=99, n=50)
    assert seen == {"difficulty": 5, "n": 5}          # clamped, not trusted
    assert out["difficulty"] == 5


def test_generate_quiz_clamps_zero_upward(monkeypatch):
    from app.tools import quiz as quiz_mod
    seen = {}
    monkeypatch.setattr(quiz_mod, "_verified_quiz",
                        lambda c, d, n: seen.update(d=d, n=n) or [])
    monkeypatch.setattr(quiz_mod.cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(quiz_mod.cache, "put", lambda *a: None)
    quiz_mod.generate_quiz_core("g10-c-quadratic-eq", difficulty=0, n=0)
    assert seen == {"d": 1, "n": 1}                   # floors, not just ceilings


def test_grade_quiz_single_answer_is_gradable(tmp_path, monkeypatch):
    """A 1-question quiz has one legal answer line ("1:A") and must grade.
    The old '< 2 parsed answers' guard rejected it, so no grade event reached
    the frontend and the model narrated a score from the answer key in its
    own context - exactly what compute-don't-trust exists to prevent.
    Zero parsed answers is still the premature-call error."""
    from app.tools import quiz as quiz_mod
    from app.data import db as db_mod
    db_mod.DB_PATH = tmp_path / "t.db"
    db_mod.init_db()
    with db_mod.get_conn() as conn:
        conn.execute("INSERT INTO students (name, grade) VALUES ('T', 10)")
    quiz = {"chapter": "g10-c-quadratic-eq", "difficulty": 2,
            "questions": [{"question_text": "2+3?", "options": ["5", "6", "4", "7"],
                           "answer_index": 0}]}
    monkeypatch.setattr(quiz_mod.cache, "get", lambda *a, **k: quiz)
    monkeypatch.setattr(quiz_mod.cache, "put", lambda *a: None)

    out = quiz_mod.grade_quiz.invoke({"chapter_id": "g10-c-quadratic-eq",
                                      "difficulty": 2, "answers": "1:A",
                                      "student_id": 1})
    assert out["verdicts"][0]["chosen"] == "A"
    assert out["verdicts"][0]["correct"] is True
    with db_mod.get_conn() as conn:      # progress was recorded server-side
        row = conn.execute("SELECT correct, total FROM progress_events").fetchone()
    assert (row["correct"], row["total"]) == (1, 1)

    early = quiz_mod.grade_quiz.invoke({"chapter_id": "g10-c-quadratic-eq",
                                        "difficulty": 2, "answers": "not yet",
                                        "student_id": 1})
    assert "error" in early                              # premature call still blocked


def test_grade_quiz_grades_the_served_quiz_not_a_new_one(tmp_path, monkeypatch):
    """The agent serves 1-question warm-ups; grade_quiz used to look the quiz
    up with a hardcoded n=5, MISS, and silently grade a freshly generated
    different quiz - marking right answers wrong. The exact quiz served to
    THIS student is now tracked and graded, and regeneration must not run."""
    from app.tools import quiz as quiz_mod
    from app.data import db as db_mod
    db_mod.DB_PATH = tmp_path / "t.db"
    db_mod.init_db()
    with db_mod.get_conn() as conn:
        conn.execute("INSERT INTO students (name, grade) VALUES ('T', 10)")

    q = [{"question_text": "2+3?", "options": ["5", "6", "4", "7"],
          "answer_index": 0, "answer": "5", "skill": "t", "verified": True}]
    served = {"chapter": "Quadratic Equations", "chapter_id": "g10-c-quadratic-eq",
              "difficulty": 2, "verified_count": 1, "questions": q}
    served_key = quiz_mod.cache.make_key("quiz", "g10-c-quadratic-eq", 2, 1)
    store = {served_key: served}
    monkeypatch.setattr(quiz_mod.cache, "get", lambda k, ttl_seconds=None: store.get(k))
    monkeypatch.setattr(quiz_mod.cache, "put", lambda k, v: store.setdefault(k, v))
    monkeypatch.setattr(quiz_mod, "_verified_quiz",
                        lambda c, d, n: q[:n])          # only used if core regenerates

    out = quiz_mod.generate_quiz.invoke({"chapter_id": "g10-c-quadratic-eq",
                                         "difficulty": 2, "n": 1, "student_id": 1})
    assert len(out["questions"]) == 1                   # 1-question warm-up served

    regen = []
    real_core = quiz_mod.generate_quiz_core
    monkeypatch.setattr(quiz_mod, "generate_quiz_core",
                        lambda *a, **k: regen.append(a) or {"error": "regenerated"})
    graded = quiz_mod.grade_quiz.invoke({"chapter_id": "g10-c-quadratic-eq",
                                         "difficulty": 4,          # model misremembers
                                         "answers": "1:A", "student_id": 1})
    assert regen == []                                  # graded the served quiz, no regen
    assert graded["verdicts"][0]["correct"] is True     # "5"/A was the right answer
    with db_mod.get_conn() as conn:                     # served difficulty recorded
        row = conn.execute("SELECT difficulty FROM progress_events").fetchone()
    assert row["difficulty"] == 2


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
