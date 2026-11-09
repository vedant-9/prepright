"""Progress-tracking tools: mastery history in, mastery profile out."""

from langchain_core.tools import tool

from app.data.db import get_conn


@tool
def record_progress(student_id: float, chapter_id: str, difficulty: float,
                    correct: float, total: float, error_notes: str = "") -> dict:
    """Append a practice result to the student's mastery history. Call once
    per completed quiz. error_notes: one short diagnostic line describing the
    mistake pattern you observed (e.g. 'sign errors when factoring')."""
    # output-side guardrail: the MODEL supplies these numbers - clamp them
    # so a hallucinated total=999 can never poison mastery/PrepScore
    difficulty = max(1, min(int(difficulty), 5))
    total = max(1, min(int(total), 50))
    correct = max(0, min(int(correct), total))
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO progress_events (student_id, chapter_id, difficulty, correct, total, error_notes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (int(student_id), chapter_id, difficulty, correct, total, error_notes or None),
        )
    return {"recorded": True}


@tool
def get_student_profile(student_id: float) -> dict:
    """Fetch a student's per-chapter mastery (rolling accuracy) so teaching
    can adapt: revisit weak chapters, raise difficulty on strong ones.
    Read this at the start of a session."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT chapter_id, SUM(correct) AS c, SUM(total) AS t "
            "FROM progress_events WHERE student_id = ? GROUP BY chapter_id",
            (int(student_id),),
        ).fetchall()
    chapters = []
    for r in rows:
        chapters.append({
            "chapter_id": r["chapter_id"],
            "accuracy": round(r["c"] / r["t"], 2),
            "attempts": r["t"],
        })
    return {"student_id": int(student_id), "chapters": chapters}
