"""SQLite persistence for PrepRight.

DESIGN NOTES (why it looks this way):

1. App data vs agent memory are SEPARATE databases:
   - prepright.db  -> our domain schema below (students, progress, reports)
   - checkpoints.db -> LangGraph's conversation checkpointing (arrives in )
   Rationale: the framework manages its own tables/migrations; mixing them
   with ours means a framework upgrade can silently break our data.

2. WAL journal mode: SQLite's default locks the whole file on writes.
   Write-Ahead Logging lets reads proceed during a write — matters once
   the SSE chat stream and the parent dashboard hit the DB concurrently.

3. sqlite3.Row factory: rows behave like dicts, keeps call sites clean.

4. No ORM (no SQLAlchemy): the schema is 4 tables; raw SQL with
   parameterized queries is transparent and removes a dependency.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "prepright.db"

SCHEMA = """
-- A student is created at onboarding: name, board, class.
-- v1 is single-board (CBSE) but the column exists from day one so
-- ICSE support later is data + content, not a migration.
-- Parents own the account (DPDP-safe: kids never have credentials).
-- pin_hash = SHA-256 of a 4-digit PIN; switch to bcrypt before this
-- endpoint faces the internet.
CREATE TABLE IF NOT EXISTS parents (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    pin_hash   TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS students (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id  INTEGER REFERENCES parents(id),
    name       TEXT NOT NULL,
    board      TEXT NOT NULL DEFAULT 'CBSE',
    grade      INTEGER NOT NULL CHECK (grade BETWEEN 5 AND 10),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One row per chat sitting. chapter_id is nullable because a session
-- may start generic ("what should I revise?") before the agent and
-- student settle on a chapter. Chapter ids reference content/curriculum.json.
CREATE TABLE IF NOT EXISTS sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id),
    chapter_id TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The student-mastery feed. Every graded quiz appends one event.
-- Mastery is DERIVED (rolling accuracy per chapter, computed in SQL),
-- never stored as a mutable column — event-sourcing-lite:
-- append-only history means we can always recompute and backfill
-- when we improve the mastery formula (e.g. add decay) later.
CREATE TABLE IF NOT EXISTS progress_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id),
    chapter_id TEXT NOT NULL,
    difficulty INTEGER NOT NULL CHECK (difficulty BETWEEN 1 AND 5),
    correct    INTEGER NOT NULL,
    total      INTEGER NOT NULL CHECK (total > 0),
    -- Free-text diagnostic written by the agent after grading, e.g.
    -- "flips numerator/denominator when converting mixed fractions".
    -- This is what makes the next session adaptive AND gives parents
    -- the 'struggle flags' in the dashboard.
    error_notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Generated weekly report cards. content_json holds the full
-- structured report (scores + LLM narrative) so reports are immutable
-- snapshots: regenerating a week doesn't lose what the parent saw.
CREATE TABLE IF NOT EXISTS reports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id   INTEGER NOT NULL REFERENCES students(id),
    week         TEXT NOT NULL,          -- ISO week, e.g. '2026-W45'
    content_json TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (student_id, week)            -- one report per student per week
);

CREATE INDEX IF NOT EXISTS idx_progress_student_chapter
    ON progress_events (student_id, chapter_id);

-- Session tokens: issued on PIN login, required on all parent API calls.
-- Tokens expire after 24 hours; one per parent (new login revokes old).
CREATE TABLE IF NOT EXISTS auth_sessions (
    token      TEXT PRIMARY KEY,
    parent_id  INTEGER NOT NULL REFERENCES parents(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL
);

-- Chat transcript, written at the API boundary on COMPLETED turns only
-- (a failed turn persists nothing - a retry reads as a fresh question).
-- Content is the PII-redacted text: phones/emails never reach storage.
-- This is the UI replay source - deliberately NOT the LangGraph
-- checkpointer, whose blobs are framework-internal.
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id),
    role       TEXT NOT NULL,               -- 'user' | 'ai'
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_student ON messages (student_id, id);
"""


def get_conn() -> sqlite3.Connection:
    """Open a connection with the pragmas we want. Caller closes (or uses `with`)."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Idempotent schema creation. Runs at app startup and in tests."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)


if __name__ == "__main__":
    init_db()
    print(f"Initialized {DB_PATH}")
