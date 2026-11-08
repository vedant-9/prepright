"""Onboarding + student endpoints.

APIRouter (instead of stuffing routes into main.py) keeps main.py a
composition root: it wires apps together, it doesn't define behavior.
As endpoints grow (chat in P4, reports in P10), each gets its own module.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import curriculum
from app.db import get_conn

router = APIRouter(prefix="/api", tags=["students"])


# --- request/response models ---------------------------------------------
# Pydantic models do triple duty: input validation (422 on bad input),
# response shaping, and — free of charge — the OpenAPI schema that /docs renders.

class StudentIn(BaseModel):
    name: str = Field(min_length=1, max_length=40, examples=["Aarav"])
    grade: int = Field(ge=5, le=10, description="CBSE class 5–10")
    board: str = Field(default="CBSE")


class StudentOut(BaseModel):
    id: int
    name: str
    grade: int
    board: str
    created_at: str


# --- endpoints --------------------------------------------------------------

@router.post("/students", response_model=StudentOut, status_code=201)
def create_student(body: StudentIn) -> StudentOut:
    """Onboarding: create the learner. One row per kid; the chat (P4) keys
    conversation state to this id."""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO students (name, grade, board) VALUES (?, ?, ?)",
            (body.name.strip(), body.grade, body.board),
        )
        row = conn.execute("SELECT * FROM students WHERE id = ?", (cur.lastrowid,)).fetchone()
    return StudentOut(**dict(row))


@router.get("/students", response_model=list[StudentOut])
def list_students() -> list[StudentOut]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM students ORDER BY id").fetchall()
    return [StudentOut(**dict(r)) for r in rows]


@router.get("/students/{student_id}", response_model=StudentOut)
def get_student(student_id: int) -> StudentOut:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="student not found")
    return StudentOut(**dict(row))


@router.get("/curriculum")
def get_curriculum(grade: int) -> dict:
    """Chapters for a grade, enriched with unit marks (the 'what matters most'
    signal). Serves the onboarding UI and doubles as the template for the
    agent's get_curriculum tool (P5)."""
    chapters = curriculum.get_chapters(grade)
    if not chapters:
        raise HTTPException(status_code=404, detail=f"no curriculum for grade {grade}")
    data = curriculum.load()
    return {
        "grade": grade,
        "version": data["version"],
        "last_verified": data["last_verified"],
        "chapters": chapters,
    }
