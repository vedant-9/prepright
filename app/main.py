"""FastAPI entrypoint.

MINIMAL ON PURPOSE: just health check + startup DB init, so the repo
is runnable and every future piece has a boot check. The real endpoints
arrive piece by piece: /api/students, /api/chat, /api/report.

Run:  .venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.agent.graph import init_graph
from app.api.chat import router as chat_router
from app.api.parent import router as parent_router
from app.api.students import router as students_router
from app.data.db import init_db

# Make prepright.* loggers visible in the console (
# warnings fired into a void because nothing configured the root
# logger - "why is the error not logged" had TWO answers: nothing called
# log in the failing path, AND no handler was configured).
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")


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


# Standard liveness probe (HF Spaces deploy + uptime checks in ).
@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "app": "prepright"}


# Routers BEFORE the static mount: Starlette matches routes in
# registration order, and the "/" mount below is a catch-all —
# anything registered after it would never be reached.
app.include_router(students_router)
app.include_router(parent_router)
app.include_router(chat_router)

# Serve the web UI. html=True maps "/" -> web/index.html.
# no-cache headers: the in-app browser's webview aggressively caches HTML,
# which then pins old CSS versions - the root cause of "I don't see borders"
# (computed style showed v=21 CSS loading from cached v=21 HTML).
class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
app.mount("/", NoCacheStaticFiles(directory=WEB_DIR, html=True), name="web")
