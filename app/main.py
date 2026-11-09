"""FastAPI entrypoint.

MINIMAL ON PURPOSE (P1.2): just health check + startup DB init, so the repo
is runnable and every future piece has a boot check. The real endpoints
arrive piece by piece: /api/students (P3), /api/chat (P4), /api/report (P10).

Run:  .venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.agent.graph import init_graph
from app.api.chat import router as chat_router
from app.api.students import router as students_router
from app.data.db import init_db


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
    await init_graph()  # async checkpointer must enter inside the event loop
    yield


app = FastAPI(title="PrepRight", version="0.1.0", lifespan=lifespan)


# Standard liveness probe (HF Spaces deploy + uptime checks in P11).
@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "app": "prepright"}


# Routers BEFORE the static mount: Starlette matches routes in
# registration order, and the "/" mount below is a catch-all —
# anything registered after it would never be reached.
app.include_router(students_router)
app.include_router(chat_router)

# Serve the web UI. html=True maps "/" -> web/index.html.
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
