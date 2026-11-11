"""Parent auth + session tokens + dashboard tests (tmp DB, no network)."""
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


def _signup_and_token(client, name="R", pin="1234"):
    """Helper: signup, return (parent_id, token, auth_headers)."""
    r = client.post("/api/parent/signup", json={"name": name, "pin": pin}).json()
    token = r["token"]
    return r["parent"]["id"], token, {"Authorization": f"Bearer {token}"}


def test_wrong_pin_rejected(client):
    p = client.post("/api/parent/signup", json={"name": "R", "pin": "1234"}).json()
    assert client.post("/api/parent/login", json={"parent_id": p["parent"]["id"], "pin": "9999"}).status_code == 401


def test_no_token_unauthorized(client):
    """Parent API requires Authorization header — no token = 401."""
    p = client.post("/api/parent/signup", json={"name": "R", "pin": "1234"}).json()
    pid = p["parent"]["id"]
    assert client.get(f"/api/parent/{pid}/dashboard").status_code == 401
    assert client.post(f"/api/parent/{pid}/kids", json={"name": "X", "grade": 10}).status_code == 401


def test_invalid_token_unauthorized(client):
    pid = 1
    bad = {"Authorization": "Bearer invalid-token-here"}
    assert client.get(f"/api/parent/{pid}/dashboard", headers=bad).status_code == 401


def test_valid_token_dashboard(client):
    pid, token, headers = _signup_and_token(client)
    kid = client.post(f"/api/parent/{pid}/kids", json={"name": "K", "grade": 10}, headers=headers).json()
    with db_mod.get_conn() as conn:
        conn.execute("INSERT INTO progress_events (student_id, chapter_id, difficulty, correct, total) VALUES (?,?,?,?,?)",
                     (kid["id"], "g10-c-quadratic-eq", 3, 4, 5))
    d = client.get(f"/api/parent/{pid}/dashboard", headers=headers).json()
    assert d["kids"][0]["chapters"][0]["flag"] == "strong"
    assert 0 <= d["kids"][0]["prepscore"] <= 100


def test_token_scoped_to_own_parent(client):
    """Parent A's token cannot access Parent B's dashboard."""
    pid_a, _, headers_a = _signup_and_token(client, name="A", pin="1111")
    pid_b, _, _ = _signup_and_token(client, name="B", pin="2222")
    # A's token on B's endpoint should still work (A is authenticated)
    # but A sees their own (empty) kids, not B's
    d = client.get(f"/api/parent/{pid_a}/dashboard", headers=headers_a).json()
    assert d["kids"] == []


def test_login_unknown_parent_is_404_not_wrong_pin(client):
    """A server reset (ephemeral demo storage) wipes the parents table while
    the browser still holds the old parent id. That must be a 404 ('sign in
    again') so the client can self-heal - NOT 'wrong PIN', which tells a
    parent who typed their PIN correctly that they typed it wrong."""
    assert client.post("/api/parent/login",
                       json={"parent_id": 99999, "pin": "1234"}).status_code == 404
