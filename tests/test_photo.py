"""Notebook photo input tests: extraction endpoint contract, limits, and
the privacy invariant (image bytes never reach storage). Vision is faked -
these test OUR plumbing, not Gemini's eyes.
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


def test_photo_extracts_and_never_persists(client, monkeypatch):
    from app.api import chat as chat_mod
    sid = _student(client)

    seen_images = []
    def fake_vision(image_b64, prompt):
        seen_images.append((image_b64, prompt))
        return "solve x^2 - 5x + 6 = 0"
    monkeypatch.setattr(chat_mod, "vision_extract", fake_vision)

    r = client.post("/api/photo", json={"student_id": sid, "image_b64": "A" * 500})
    assert r.json() == {"found": True, "text": "solve x^2 - 5x + 6 = 0"}
    assert "notebook" in seen_images[0][1].lower()      # the extraction prompt

    # PRIVACY INVARIANT: no image bytes in the messages table (or anywhere
    # in the DB file) - the image exists in request memory only
    blob = (db_mod.DB_PATH).read_bytes()
    assert b"A" * 500 not in blob
    assert client.get(f"/api/students/{sid}/messages").json() == []


def test_photo_no_maths_found(client, monkeypatch):
    from app.api import chat as chat_mod
    sid = _student(client)
    monkeypatch.setattr(chat_mod, "vision_extract",
                        lambda b, p: "NO MATHS FOUND")
    r = client.post("/api/photo", json={"student_id": sid, "image_b64": "B" * 500})
    assert r.json() == {"found": False}


def test_photo_size_and_unknown_student(client):
    sid = _student(client)
    # oversized payload rejected by schema before any vision call
    r = client.post("/api/photo",
                    json={"student_id": sid, "image_b64": "C" * 2_100_000})
    assert r.status_code == 422
    r = client.post("/api/photo",
                    json={"student_id": 999, "image_b64": "D" * 500})
    assert r.status_code == 404


def test_photo_vision_failure_is_friendly_503(client, monkeypatch):
    from app.api import chat as chat_mod
    sid = _student(client)

    def dead(image_b64, prompt):
        raise RuntimeError("quota 429 resource_exhausted")
    monkeypatch.setattr(chat_mod, "vision_extract", dead)

    r = client.post("/api/photo", json={"student_id": sid, "image_b64": "E" * 500})
    assert r.status_code == 503
    assert "busy" in r.json()["detail"].lower()          # kid-friendly, no internals
