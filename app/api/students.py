"""Onboarding + student endpoints.

APIRouter (instead of stuffing routes into main.py) keeps main.py a
composition root: it wires apps together, it doesn't define behavior.
As endpoints grow (chat in , reports in ), each gets its own module.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.data import curriculum
from app.data.db import get_conn

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


class MessageOut(BaseModel):
    id: int
    role: str          # 'user' | 'ai'
    content: str
    created_at: str


# --- endpoints --------------------------------------------------------------

@router.post("/students", response_model=StudentOut, status_code=201)
def create_student(body: StudentIn) -> StudentOut:
    """Onboarding: create the learner. One row per kid; the chat keys
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


@router.get("/students/{student_id}/messages", response_model=list[MessageOut])
def get_messages(student_id: int, limit: int = 50,
                 before_id: int | None = None) -> list[MessageOut]:
    """Chat transcript for UI replay: most recent `limit` messages, oldest
    first. Only completed turns are stored, so this reads as a clean
    conversation. `before_id` paginates backwards ("load earlier")."""
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM students WHERE id = ?", (student_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="student not found")
        sql = "SELECT * FROM messages WHERE student_id = ?"
        params: list = [student_id]
        if before_id is not None:
            sql += " AND id < ?"
            params.append(before_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(limit, 200)))
        rows = conn.execute(sql, params).fetchall()
    return [MessageOut(**dict(r)) for r in reversed(rows)]


@router.delete("/students/{student_id}/chat", status_code=204)
async def clear_chat(student_id: int):
    """Fresh conversation for a student. Clears BOTH stores that make up a
    chat: the messages table (visible history) and the checkpointer thread
    (the tutor's memory). Progress is deliberately UNTOUCHED - quiz results,
    PrepScore and chapter flags live in progress_events and survive the
    reset: a fresh chat is a fresh conversation, not a fresh student."""
    from app.agent.graph import graph
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM students WHERE id = ?", (student_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="student not found")
        conn.execute("DELETE FROM messages WHERE student_id = ?", (student_id,))
    await graph.checkpointer.adelete_thread(f"student-{student_id}")
    return None


@router.get("/curriculum")
def get_curriculum(grade: int) -> dict:
    """Chapters for a grade, enriched with unit marks (the 'what matters most'
    signal). Serves the onboarding UI and doubles as the template for the
    agent's get_curriculum tool."""
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
