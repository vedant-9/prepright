"""Parent auth + dashboard + PrepScore tests (tmp DB, no network)."""
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


def test_signup_login_wrong_pin(client):
    p = client.post("/api/parent/signup", json={"name": "R", "pin": "1234"}).json()
    assert client.post("/api/parent/login", json={"parent_id": p["parent"]["id"], "pin": "9999"}).status_code == 401


def test_kid_crud_and_dashboard(client):
    p = client.post("/api/parent/signup", json={"name": "R", "pin": "1234"}).json()
    pid = p["parent"]["id"]
    kid = client.post(f"/api/parent/{pid}/kids", json={"name": "K", "grade": 10}).json()
    with db_mod.get_conn() as conn:
        conn.execute("INSERT INTO progress_events (student_id, chapter_id, difficulty, correct, total) VALUES (?,?,?,?,?)",
                     (kid["id"], "g10-c-quadratic-eq", 3, 4, 5))
    d = client.get(f"/api/parent/{pid}/dashboard").json()
    assert d["kids"][0]["chapters"][0]["flag"] == "strong"
    assert 0 <= d["kids"][0]["prepscore"] <= 100


def test_parent_cannot_see_others_kid(client):
    p = client.post("/api/parent/signup", json={"name": "R", "pin": "1234"}).json()
    assert client.get(f"/api/parent/{p['parent']['id']}/dashboard").json()["kids"] == []
