"""Quiz tools (P5): generation, grading - the compute-don't-trust pipeline.

HOW TOOL CALLING WORKS (wired into the graph in P6):
The model never runs these. It emits a tool_call (name + JSON args);
LangGraph's ToolNode executes the matching function and hands the result
back as a ToolMessage. These @tool definitions are simultaneously:
  - the schemas the model sees (docstring = instruction, types = contract)
  - the functions that run on our server

PIPELINE (generate_quiz):
  1. cache check          (chapter, difficulty, n) - repeat asks cost zero
  2. LLM drafts questions with machine SPECS but NO answers
  3. verify.verify_question() on every draft:
       sympy solves the spec -> answer key is computed, not generated
       spec numbers must appear in the text -> kills phrasing drift
  4. failed drafts are DROPPED (never served); thin batch -> one retry
  5. verified quiz cached and returned

Demo:  python -m app.tools.quiz demo g10-c-quadratic-eq 2
Test:  pytest tests/test_verify.py -q
"""

import json
import random
import re

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.data import cache, curriculum
from app.data.db import get_conn
from app.llm.gateway import generate_structured
from app.tools.prompts import DIFFICULTY_GUIDE, SPEC_MENU
from app.verify.math import verify_question

# ---------------------------------------------------------------- schemas

class QuestionDraft(BaseModel):
    """What the LLM may produce. Note what's ABSENT: any answer field."""
    question_text: str = Field(min_length=8, max_length=400)
    spec: dict = Field(description="machine-solvable spec; type from the allowed list")
    skill: str = Field(max_length=60)
    difficulty: int = Field(ge=1, le=5)


class QuizDraft(BaseModel):
    questions: list[QuestionDraft] = Field(min_length=1, max_length=10)


# ---------------------------------------------------------------- pipeline

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
            v = verify_question(q.question_text, q.spec, q.skill)
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
    """Cache-aware entry point (what the @tool wrapper and any API call).

    ARG CLAMPS (output-side guardrail): the model supplies difficulty/n,
    so they are clamped to sane ranges here - schema validation alone
    permits difficulty=99, which would burn quota and traumatise a
    13-year-old. Trust boundary: model INTENT yes, model PARAMETERS
    clamped."""
    difficulty = max(1, min(int(difficulty), 5))
    n = max(1, min(int(n), 5))
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
def generate_quiz(chapter_id: str, difficulty: float = 2, n: float = 5) -> dict:
    """Generate a practice quiz for a CBSE maths chapter. Every question is
    verified: answers are computed by sympy, never by the language model.
    Use when the student should practice a chapter (they ask for practice,
    a test, or have just finished learning a topic)."""
    # args arrive as JSON numbers: 2 or 2.0 - accept both, coerce to int
    return generate_quiz_core(chapter_id, int(difficulty), int(n))


@tool
def grade_answer(answer_index: float, correct_index: float) -> dict:
    """Grade one multiple-choice answer the student just submitted (learn
    mode). Returns verdict plus what to praise or fix."""
    answer_index, correct_index = int(answer_index), int(correct_index)
    ok = answer_index == correct_index
    return {
        "correct": ok,
        "guidance": "confirm warmly and advance" if ok else "reveal what was right first, then give ONE hint, not the answer",
    }


@tool
def grade_quiz(chapter_id: str, difficulty: float, answers: str,
               student_id: float = 0) -> dict:
    """Grade a completed BATCH quiz in ONE call and record progress.
    answers = the student's line like "1:A 2:C 3:B 4:A 5:D". Pass the SAME
    chapter_id and difficulty used for generate_quiz. Returns score and
    per-question verdicts - narrate the review from this result. Never call
    record_progress after grade_quiz (it already did)."""
    quiz = generate_quiz_core(chapter_id, int(difficulty), 5)  # cache hit -> same quiz
    if "questions" not in quiz:
        return {"error": "quiz not found for that chapter/difficulty"}

    picked = {}
    for num, letter in re.findall(r"(\d+)\s*[:\-.]?\s*([A-Da-d])", answers):
        picked[int(num)] = letter.upper()

    verdicts, correct_count = [], 0
    for i, q in enumerate(quiz["questions"], start=1):
        letter = picked.get(i)
        chosen_idx = "ABCD".index(letter) if letter in ("A", "B", "C", "D") else None
        ok = chosen_idx == q["answer_index"]
        correct_count += ok
        verdicts.append({
            "question": i,
            "chosen": letter or "(skipped)",
            "correct": ok,
            "right_option": q["options"][q["answer_index"]],
        })

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO progress_events (student_id, chapter_id, difficulty, correct, total) "
            "VALUES (?, ?, ?, ?, ?)",
            (int(student_id), chapter_id, int(difficulty),
             correct_count, len(quiz["questions"])),
        )
    return {"score": f"{correct_count}/{len(quiz['questions'])}", "verdicts": verdicts}


# ---------------------------------------------------------------- demo CLI

if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 3 and sys.argv[1] == "demo":
        chapter_id = sys.argv[2]
        diff = int(sys.argv[3]) if len(sys.argv) > 3 else 2
        quiz = generate_quiz_core(chapter_id, diff, n=3)
        print(json.dumps(quiz, indent=2, ensure_ascii=True))
    else:
        print("usage: python -m app.tools.quiz demo <chapter_id> [difficulty]")
