"""The tutor's tools (P5): quiz generation, grading, progress, profile.

HOW TOOL CALLING WILL WORK (wired into the graph in P6):
The model never runs these. It emits a tool_call (name + JSON args);
LangGraph's ToolNode executes the matching function and hands the result
back as a ToolMessage. These @tool definitions are simultaneously:
  - the schemas the model sees (docstring = instruction, types = contract)
  - the functions that run on our server

COMPUTE-DON'T-TRUST QUIZ PIPELINE (generate_quiz):
  1. cache check          (chapter, difficulty, n) - repeat asks cost zero
  2. LLM drafts questions with machine SPECS but NO answers
  3. verify.verify_question() on every draft:
       sympy solves the spec -> answer key is computed, not generated
       spec numbers must appear in the text -> kills phrasing drift
  4. failed drafts are DROPPED (never served); thin batch -> one retry
  5. verified quiz cached and returned

Demo:  python -m app.tools demo g10-c-quadratic-eq 2
Test:  pytest tests/test_verify.py -q
"""

import json
import random

from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app import cache, curriculum, verify
from app.db import get_conn
from app.llm import generate_structured

# ---------------------------------------------------------------- schemas

class QuestionDraft(BaseModel):
    """What the LLM may produce. Note what's ABSENT: any answer field."""
    question_text: str = Field(min_length=8, max_length=400)
    spec: dict = Field(description="machine-solvable spec; type from the allowed list")
    skill: str = Field(max_length=60)
    difficulty: int = Field(ge=1, le=5)


class QuizDraft(BaseModel):
    questions: list[QuestionDraft] = Field(min_length=1, max_length=10)


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


# ---------------------------------------------------------------- quiz pipeline

def _draft_quiz(chapter: dict, difficulty: int, n: int) -> QuizDraft:
    prompt = f"""Draft {n} multiple-choice-ready maths questions for CBSE Class {chapter["grade"]}
chapter: "{chapter["title"]}" (unit: {chapter["unit"] or "general"}).

Difficulty {difficulty} of 5 means: {DIFFICULTY_GUIDE[difficulty]}.

Rules:
- Choose spec types that FIT this chapter's skills.
- The question_text MUST contain the same numbers as its spec, written for a
  {DIFFICULTY_GUIDE[difficulty]} student. Do NOT include the answer anywhere.
- Keep language warm and simple (the reader is 13-15 years old).

{SPEC_MENU}

Return exactly {n} questions."""
    return generate_structured(QuizDraft, [HumanMessage(content=prompt)])


def _verified_quiz(chapter: dict, difficulty: int, n: int) -> list[dict]:
    """Full pipeline with one regeneration retry if too few drafts survive."""
    for attempt in (1, 2):
        try:
            draft = _draft_quiz(chapter, difficulty, n)
        except Exception:
            continue  # provider/parse failure on attempt 1 -> retry once
        questions, dropped = [], []
        for q in draft.questions:
            v = verify.verify_question(q.question_text, q.spec, q.skill)
            (questions if v.ok else dropped).append((q, v))
        if len(questions) >= max(1, n // 2):  # majority survived -> good batch
            break
    else:
        return []

    out = []
    for q, v in questions:
        options = v.options[:]                 # options[0] is the correct answer
        rng = random.Random(q.question_text)   # deterministic shuffle per question
        rng.shuffle(options)
        out.append({
            "question_text": q.question_text,
            "options": options,
            "answer_index": options.index(v.options[0]),
            "answer": v.answer,                # kept server-side; never sent raw to UI
            "skill": v.skill,
            "verified": True,
        })
    return out


def generate_quiz_core(chapter_id: str, difficulty: int = 2, n: int = 5) -> dict:
    """Cache-aware entry point (what the @tool wrapper and any API call)."""
    chapter = curriculum.find_chapter(chapter_id)
    if chapter is None:
        return {"error": f"unknown chapter_id: {chapter_id}"}

    key = cache.make_key("quiz", chapter_id, difficulty, n)
    if (hit := cache.get(key)) is not None:
        return hit

    questions = _verified_quiz(chapter, difficulty, n)
    result = {
        "chapter": chapter["title"],
        "chapter_id": chapter_id,
        "difficulty": difficulty,
        "verified_count": len(questions),
        "questions": questions,
    }
    if questions:
        cache.put(key, result)
    return result


# ---------------------------------------------------------------- the tools
# Docstrings are the model's instructions in P6 - write them for the LLM
# to act on, not for human developers only.

@tool
def generate_quiz(chapter_id: str, difficulty: int, n: int = 5) -> dict:
    """Generate a practice quiz for a CBSE maths chapter. Every question is
    verified: answers are computed by sympy, never by the language model.
    Use when the student should practice a chapter (they ask for practice,
    a test, or have just finished learning a topic)."""
    return generate_quiz_core(chapter_id, difficulty, n)


@tool
def grade_answer(answer_index: int, correct_index: int) -> dict:
    """Grade one multiple-choice answer the student just submitted.
    Returns verdict plus what to praise or fix. Call immediately after the
    student answers a quiz question."""
    ok = answer_index == correct_index
    return {
        "correct": ok,
        # the pedagogy prompt turns this into encouragement or a targeted hint
        "guidance": "confirm warmly and advance" if ok else "reveal what was right first, then give ONE hint, not the answer",
    }


@tool
def record_progress(student_id: int, chapter_id: str, difficulty: int,
                    correct: int, total: int, error_notes: str = "") -> dict:
    """Append a practice result to the student's mastery history. Call once
    per completed quiz. error_notes: one short diagnostic line describing the
    mistake pattern you observed (e.g. 'sign errors when factoring')."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO progress_events (student_id, chapter_id, difficulty, correct, total, error_notes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (student_id, chapter_id, difficulty, correct, total, error_notes or None),
        )
    return {"recorded": True}


@tool
def get_student_profile(student_id: int) -> dict:
    """Fetch a student's per-chapter mastery (rolling accuracy) so teaching
    can adapt: revisit weak chapters, raise difficulty on strong ones.
    Read this at the start of a session."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT chapter_id, SUM(correct) AS c, SUM(total) AS t "
            "FROM progress_events WHERE student_id = ? GROUP BY chapter_id",
            (student_id,),
        ).fetchall()
    chapters = []
    for r in rows:
        chapters.append({
            "chapter_id": r["chapter_id"],
            "accuracy": round(r["c"] / r["t"], 2),
            "attempts": r["t"],
        })
    return {"student_id": student_id, "chapters": chapters}


@tool
def get_curriculum(grade: int) -> dict:
    """List the maths chapters for a CBSE grade, with each chapter's unit
    marks (marks indicate exam importance). Use to help the student pick
    what to practice."""
    chapters = curriculum.get_chapters(grade)
    return {"grade": grade, "chapters": chapters}


# ---------------------------------------------------------------- demo CLI

if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 3 and sys.argv[1] == "demo":
        chapter_id = sys.argv[2]
        diff = int(sys.argv[3]) if len(sys.argv) > 3 else 2
        quiz = generate_quiz_core(chapter_id, diff, n=3)
        print(json.dumps(quiz, indent=2, ensure_ascii=True))
    else:
        print("usage: python -m app.tools demo <chapter_id> [difficulty]")
