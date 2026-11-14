"""Conversation-history tests: messages table roundtrip, replay endpoint,
no-write-on-failure. The chat turn itself is faked (graph monkeypatched) -
these tests verify the PERSISTENCE layer, not the tutor.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from app.data import db as db_mod


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_mod.DB_PATH = tmp_path / "t.db"
    db_mod.init_db()
    from app.main import app
    with TestClient(app) as c:
        yield c


def _student(client) -> int:
    return client.post("/api/students", json={"name": "T", "grade": 10}).json()["id"]


async def _fake_turn_ok(student, message):
    yield {"delta": "The HCF is "}

async def _fake_turn_more(student, message):
    yield {"delta": "part one "}
    yield {"tool": "calculate"}
    yield {"delta": "part two"}

async def _fake_turn_dies(student, message):
    yield {"delta": "partial answer..."}
    raise RuntimeError("provider exploded")


def test_completed_turn_persists_both_rows(client, monkeypatch):
    from app.api import chat as chat_mod
    sid = _student(client)
    monkeypatch.setattr(chat_mod, "stream_tutor_turn", _fake_turn_ok)
    r = client.post("/api/chat", json={"student_id": sid, "message": "HCF of 12 and 18?"})
    assert r.status_code == 200
    msgs = client.get(f"/api/students/{sid}/messages").json()
    assert [(m["role"], m["content"]) for m in msgs] == [
        ("user", "HCF of 12 and 18?"), ("ai", "The HCF is ")]


def test_deltas_concatenate_across_tool_events(client, monkeypatch):
    from app.api import chat as chat_mod
    sid = _student(client)
    monkeypatch.setattr(chat_mod, "stream_tutor_turn", _fake_turn_more)
    client.post("/api/chat", json={"student_id": sid, "message": "hi"})
    msgs = client.get(f"/api/students/{sid}/messages").json()
    assert msgs[1]["content"] == "part one part two"   # tool chips don't break text


def test_failed_turn_persists_nothing(client, monkeypatch):
    """A provider death mid-stream must not leave a dangling user bubble:
    the retry reads as a fresh question, replay stays clean."""
    from app.api import chat as chat_mod
    sid = _student(client)
    monkeypatch.setattr(chat_mod, "stream_tutor_turn", _fake_turn_dies)
    r = client.post("/api/chat", json={"student_id": sid, "message": "help"})
    assert "error" in r.text
    assert client.get(f"/api/students/{sid}/messages").json() == []


def test_limit_returns_oldest_first(client, monkeypatch):
    from app.api import chat as chat_mod

    async def fake(student, message):
        yield {"delta": f"reply to {message}"}

    monkeypatch.setattr(chat_mod, "stream_tutor_turn", fake)
    sid = _student(client)
    for i in range(3):
        client.post("/api/chat", json={"student_id": sid, "message": f"q{i}"})
    msgs = client.get(f"/api/students/{sid}/messages?limit=2").json()
    # limit is MESSAGES, not exchanges: the newest two (q2 + its reply),
    # returned oldest-first
    assert [m["content"] for m in msgs] == ["q2", "reply to q2"]
    msgs4 = client.get(f"/api/students/{sid}/messages?limit=4").json()
    assert [m["content"] for m in msgs4] == ["q1", "reply to q1", "q2", "reply to q2"]


def test_unknown_student_404(client):
    assert client.get("/api/students/999/messages").status_code == 404


def test_before_id_paginates_backwards(client, monkeypatch):
    from app.api import chat as chat_mod

    async def fake(student, message):
        yield {"delta": f"reply to {message}"}

    monkeypatch.setattr(chat_mod, "stream_tutor_turn", fake)
    sid = _student(client)
    for i in range(4):
        client.post("/api/chat", json={"student_id": sid, "message": f"q{i}"})
    # 8 messages total; newest 2 first, then everything older via cursor
    first = client.get(f"/api/students/{sid}/messages?limit=2").json()
    assert [m["content"] for m in first] == ["q3", "reply to q3"]
    older = client.get(f"/api/students/{sid}/messages?limit=2&before_id={first[0]['id']}").json()
    assert [m["content"] for m in older] == ["q2", "reply to q2"]
    # cursor past the beginning returns the oldest exchange, then nothing
    oldest = client.get(f"/api/students/{sid}/messages?limit=50&before_id={older[0]['id']}").json()
    assert [m["content"] for m in oldest] == ["q0", "reply to q0", "q1", "reply to q1"]
    assert client.get(f"/api/students/{sid}/messages?limit=50&before_id={oldest[0]['id']}").json() == []


def test_clear_chat_wipes_both_stores_keeps_progress(client, monkeypatch):
    """New chat clears messages + the memory thread, NEVER progress."""
    from app.api import chat as chat_mod
    from app.agent import graph as graph_mod

    sid = _student(client)

    async def fake(student, message):
        yield {"delta": "ok"}
    monkeypatch.setattr(chat_mod, "stream_tutor_turn", fake)
    client.post("/api/chat", json={"student_id": sid, "message": "hello"})

    with db_mod.get_conn() as conn:      # seed a progress event that MUST survive
        conn.execute("INSERT INTO progress_events (student_id, chapter_id, difficulty, correct, total)"
                     " VALUES (?, 'x', 2, 3, 5)", (sid,))

    deleted_threads = []
    class FakeCheckpointer:
        async def adelete_thread(self, thread_id):
            deleted_threads.append(thread_id)
    class FakeGraph:
        checkpointer = FakeCheckpointer()
    monkeypatch.setattr(graph_mod, "graph", FakeGraph())

    r = client.delete(f"/api/students/{sid}/chat")
    assert r.status_code == 204
    assert deleted_threads == [f"student-{sid}"]          # tutor memory reset
    assert client.get(f"/api/students/{sid}/messages").json() == []  # history gone
    with db_mod.get_conn() as conn:                        # progress SURVIVED
        n = conn.execute("SELECT COUNT(*) FROM progress_events WHERE student_id=?",
                         (sid,)).fetchone()[0]
    assert n == 1

    assert client.delete("/api/students/999/chat").status_code == 404


def test_quiz_turn_persists_readonly_card_payload(client, monkeypatch):
    """A quiz turn must persist its structured payload as a third row -
    otherwise replay shows 'Here is your quiz' followed by answers to
    questions the student cannot see."""
    from app.api import chat as chat_mod
    sid = _student(client)

    async def fake(student, message):
        yield {"delta": "Here is your quiz."}
        yield {"quiz": {"chapter": "Statistics", "difficulty": 2,
                        "questions": [{"num": 1, "text": "mean of 1, 2, 3",
                                       "options": ["2", "3", "1", "6"]}]}}
    monkeypatch.setattr(chat_mod, "stream_tutor_turn", fake)

    client.post("/api/chat", json={"student_id": sid, "message": "quiz me"})
    msgs = client.get(f"/api/students/{sid}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "ai", "quiz"]
    import json as _json
    payload = _json.loads(msgs[2]["content"])
    assert payload["chapter"] == "Statistics"
    assert payload["questions"][0]["options"] == ["2", "3", "1", "6"]
