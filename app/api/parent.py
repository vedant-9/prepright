"""Parent authentication + kid profiles + PrepScore API.

THE KID-SAFE CONTAINER PATTERN:
- Parent owns the account (email at deploy; name + PIN now)
- Kid profiles live under the parent; picking a profile needs NO PIN
  (a kid should never need a parent just to start studying)
- PIN gates EXITS from kid-mode: parent dashboard, settings
- DPDP-safe: kids never hold credentials

PrepScore = sum(chapter_mastery * unit_marks) / total_marks.
A measurement of practice coverage, never an exam-result promise.
"""

import hashlib
import json
import secrets
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.data import curriculum
from app.data.db import get_conn

router = APIRouter(prefix="/api", tags=["parent"])


# ---------------------------------------------------------------- sessions

def _create_session(parent_id: int) -> str:
    """Issue a session token (24h expiry, replaces any old one)."""
    token = secrets.token_hex(24)
    expires = (datetime.utcnow() + timedelta(hours=24)).isoformat()
    with get_conn() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE parent_id = ?", (parent_id,))
        conn.execute(
            "INSERT INTO auth_sessions (token, parent_id, expires_at) VALUES (?, ?, ?)",
            (token, parent_id, expires))
    return token


def require_parent(authorization: str = Header(None)) -> int:
    """FastAPI dependency: validates the Bearer token, returns parent_id.
    Raises 401 if missing, invalid, or expired."""
    if not authorization:
        raise HTTPException(status_code=401, detail="missing session token")
    token = authorization.replace("Bearer ", "").strip()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT parent_id, expires_at FROM auth_sessions WHERE token = ?",
            (token,)).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="invalid session")
    if row["expires_at"] < datetime.utcnow().isoformat():
        raise HTTPException(status_code=401, detail="session expired")
    return row["parent_id"]


# ---------------------------------------------------------------- helpers

def _hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()


def _iso_week() -> str:
    d = date.today()
    return f"{d.isocalendar().year}-W{d.isocalendar().week:02d}"


# ---------------------------------------------------------------- models

class ParentIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    pin: str = Field(min_length=4, max_length=4, pattern=r"^\d{4}$")


class ParentLogin(BaseModel):
    parent_id: int
    pin: str = Field(min_length=4, max_length=4, pattern=r"^\d{4}$")


class KidIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    grade: int = Field(ge=5, le=10)
    board: str = "CBSE"


# ---------------------------------------------------------------- endpoints

@router.post("/parent/signup")
def parent_signup(body: ParentIn) -> dict:
    """Find-or-create: known name + right PIN = login; known name + wrong
    PIN = 401; new name = create. One endpoint, no duplicates."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, name, pin_hash FROM parents WHERE name = ?",
            (body.name.strip(),)).fetchone()
        if row is not None:
            if row["pin_hash"] != _hash_pin(body.pin):
                raise HTTPException(status_code=401, detail="wrong PIN for this name")
            return {"parent": {"id": row["id"], "name": row["name"]},
                    "kids": _kids_of(row["id"]),
                    "token": _create_session(row["id"])}
        cur = conn.execute(
            "INSERT INTO parents (name, pin_hash) VALUES (?, ?)",
            (body.name.strip(), _hash_pin(body.pin)))
        new = conn.execute(
            "SELECT id, name FROM parents WHERE id = ?", (cur.lastrowid,)).fetchone()
    return {"parent": dict(new), "kids": [], "token": _create_session(new["id"])}


@router.post("/parent/login")
def parent_login(body: ParentLogin) -> dict:
    """PIN check. Returns kid profiles for the switcher UI."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, name, pin_hash FROM parents WHERE id = ?",
            (body.parent_id,)).fetchone()
    if row is None or row["pin_hash"] != _hash_pin(body.pin):
        raise HTTPException(status_code=401, detail="wrong PIN")
    kids = _kids_of(body.parent_id)
    return {"parent": {"id": row["id"], "name": row["name"]}, "kids": kids}


@router.get("/parent/{parent_id}/kids")
def list_kids(parent_id: int) -> list[dict]:
    return _kids_of(parent_id)


@router.post("/parent/{parent_id}/kids", status_code=201)
def add_kid(parent_id: int, body: KidIn, _auth: int = Depends(require_parent)) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO students (parent_id, name, grade, board) VALUES (?, ?, ?, ?)",
            (parent_id, body.name.strip(), body.grade, body.board))
        row = conn.execute(
            "SELECT id, name, grade, board FROM students WHERE id = ?",
            (cur.lastrowid,)).fetchone()
    return dict(row)


@router.delete("/parent/{parent_id}/kids/{student_id}")
def delete_kid(parent_id: int, student_id: int,
               _auth: int = Depends(require_parent)) -> dict:
    """Remove a kid profile and all associated data (progress, sessions).
    Parent PIN re-authentication recommended for production; pilot = token auth."""
    with get_conn() as conn:
        # verify kid belongs to this parent
        row = conn.execute(
            "SELECT id FROM students WHERE id = ? AND parent_id = ?",
            (student_id, parent_id)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="kid not under this parent")
        # cascade cleanup
        conn.execute("DELETE FROM progress_events WHERE student_id = ?", (student_id,))
        conn.execute("DELETE FROM sessions WHERE student_id = ?", (student_id,))
        conn.execute("DELETE FROM reports WHERE student_id = ?", (student_id,))
        conn.execute("DELETE FROM students WHERE id = ?", (student_id,))
    return {"deleted": True}


@router.get("/parent/{parent_id}/dashboard")
def dashboard(parent_id: int, _auth: int = Depends(require_parent)) -> dict:
    """Mastery per kid per chapter + PrepScore + struggle flags."""
    kids = _kids_of(parent_id)
    out = []
    for kid in kids:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT chapter_id, difficulty, SUM(correct) AS c, SUM(total) AS t, "
                "MAX(created_at) AS last, "
                "GROUP_CONCAT(DISTINCT error_notes) AS notes "
                "FROM progress_events WHERE student_id = ? GROUP BY chapter_id, difficulty",
                (kid["id"],)).fetchall()
        chapters = []
        for r in rows:
            accuracy = round(r["c"] / r["t"], 2) if r["t"] else 0
            flag = "struggling" if accuracy < 0.5 else ("strong" if accuracy >= 0.8 else "ok")
            chapters.append({
                "chapter_id": r["chapter_id"],
                "difficulty": r["difficulty"],
                "accuracy": accuracy, "attempts": r["t"],
                "last_practiced": r["last"], "flag": flag,
                "error_notes": [n for n in (r["notes"] or "").split(",") if n][:3],
            })
        out.append({**kid, "chapters": chapters,
                    "prepscore": _prepscore(chapters, kid["grade"])})
    return {"kids": out, "week": _iso_week()}


@router.post("/parent/{parent_id}/report/{student_id}")
def generate_report(parent_id: int, student_id: int, _auth: int = Depends(require_parent)) -> dict:
    """Weekly report card: LLM narrative over the mastery data, stored
    immutable (one per student per week)."""
    dash = dashboard(parent_id)
    kid = next((k for k in dash["kids"] if k["id"] == student_id), None)
    if kid is None:
        raise HTTPException(status_code=404, detail="kid not under this parent")

    from langchain_core.messages import HumanMessage
    from app.llm.gateway import generate_structured

    class Report(BaseModel):
        headline: str
        summary: str
        wins: list[str]
        focus_areas: list[str]
        suggested_activity: str

    # NO-DATA GUARD: a kid with zero practice gets a "not started yet"
    # message, never a hallucinated progress report (the LLM will happily
    # fabricate "completed all assignments" for an empty dataset)
    if not kid["chapters"]:
        return {"week": dash["week"], "prepscore": kid["prepscore"],
                "chapters": [], "report": {
                    "headline": "No practice yet",
                    "summary": f"{kid['name']} hasn't started any practice sessions. "
                               "Once they complete a quiz, this report will show real progress.",
                    "wins": [], "focus_areas": [],
                    "suggested_activity": "Start with a quick quiz from the kid's tutor page."}}

    report = generate_structured(Report, [HumanMessage(content=(
        f"Write a warm, honest weekly report for a parent about their child "
        f"{kid['name']} (Class {kid['grade']}). Data: "
        f"{json.dumps(kid['chapters'])}. PrepScore: {kid['prepscore']}/100. "
        f"Guidelines: no exam-result promises; celebrate effort; name ONE "
        f"focus area with a concrete 15-minute activity. Under 150 words."
    ))])
    blob = {"week": dash["week"], "prepscore": kid["prepscore"],
            "chapters": kid["chapters"], "report": report.model_dump()}
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO reports (student_id, week, content_json) "
            "VALUES (?, ?, ?)",
            (student_id, dash["week"], json.dumps(blob)))
    return blob


@router.get("/parent/{parent_id}/reports/{student_id}")
def get_reports(parent_id: int, student_id: int, _auth: int = Depends(require_parent)) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT content_json FROM reports WHERE student_id = ? "
            "ORDER BY week DESC", (student_id,)).fetchall()
    return [json.loads(r["content_json"]) for r in rows]


# ---------------------------------------------------------------- internals

def _kids_of(parent_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, grade, board FROM students "
            "WHERE parent_id = ? ORDER BY id", (parent_id,)).fetchall()
    return [dict(r) for r in rows]


def _prepscore(chapters: list[dict], grade: int) -> int:
    """sum(mastery x unit_marks) / total_marks, 0-100. Chapters without
    a unit mark (grades 5-8) count equally. A coverage measurement."""
    all_ch = curriculum.get_chapters(grade)
    marks = {c["id"]: c["unit_marks"] for c in all_ch if c["unit_marks"]}
    total_marks = sum(marks.values()) or len(all_ch) or 1
    score = sum(
        (marks.get(c["chapter_id"], 1) or 1) * min(c["accuracy"], 1.0)
        for c in chapters)
    return min(round(100 * score / total_marks), 100)
