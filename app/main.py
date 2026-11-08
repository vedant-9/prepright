"""FastAPI entrypoint.

MINIMAL ON PURPOSE (P1.2): just health check + startup DB init, so the repo
is runnable and every future piece has a boot check. The real endpoints
arrive piece by piece: /api/students (P3), /api/chat (P4), /api/report (P10).

Run:  .venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import init_db


# `lifespan` is the modern startup/shutdown hook (FastAPI's older
# @app.on_event("startup") is deprecated). Everything before `yield`
# runs once at server start; code after `yield` would run at shutdown.
#
# WHY init the DB here and not at import time? Import-time side effects
# (I/O at module import) bite you in tests — importing app.main in a
# test would create/touch the real database. Lifespan runs only when a
# server actually starts; tests can call init_db() explicitly.
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="PrepRight", version="0.1.0", lifespan=lifespan)


# Standard liveness probe. Later used by the HF Spaces deploy (P11)
# and any uptime check — cheap request, no auth, no DB touch.
@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "app": "prepright"}
