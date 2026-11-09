"""SSE chat endpoint - full edition: stateful via the checkpointer.

WHAT CHANGED FROM THE THIN SLICE:
- Client sends ONE new message; the graph's checkpointer restores the
  student's full history from their thread (thread_id = student-{id}).
  The old "client sends full history" contract is gone - the server no
  longer trusts the client's memory, and history survives tab closes.
- PII is redacted HERE, at the boundary: phone numbers/emails never enter
  checkpoints or reach any provider (data hygiene before anything else).
- Stream now carries THREE event kinds: {"delta"} tokens, {"tool"} activity
  chips, {"done"} / {"error"} terminators.

SSE protocol refresher: long-lived `text/event-stream` response, each event
is `data: <json>\n\n`; browser reads via fetch + ReadableStream.
"""

import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.graph import stream_tutor_turn
from app.agent.guardrails import redact_pii
from app.data.db import get_conn
from app.llm.gateway import is_quota_error

log = logging.getLogger("prepright.chat")

router = APIRouter(prefix="/api", tags=["chat"])

# CLIENT-FACING ERRORS ARE FRIENDLY BY POLICY: raw provider
# exceptions (model names, quota URLs, retry timers) are internal detail -
# they belong in server logs, never in a kid's chat bubble. Classification
# happens here; the browser only ever sees the friendly text.
FRIENDLY_QUOTA = ("Oops - our teaching service is very busy right now. "
                  "Please try again in a few minutes!")
FRIENDLY_GENERIC = ("Hmm, something went wrong on my side. "
                    "Please try again - your progress is safe.")


def friendly_error(exc: Exception) -> str:
    """Map any exception to a kid-safe message. Detail goes to the log."""
    if is_quota_error(exc):
        return FRIENDLY_QUOTA
    return FRIENDLY_GENERIC


class ChatIn(BaseModel):
    student_id: int
    # ONE new message per request - history lives server-side now.
    message: str = Field(min_length=1, max_length=2000)


def _get_student(student_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="student not found")
    return dict(row)


def _event(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


@router.post("/chat")
async def chat(body: ChatIn) -> StreamingResponse:
    student = _get_student(body.student_id)
    # Boundary guardrail: redact BEFORE storing/sending anywhere.
    clean = redact_pii(body.message.strip())

    async def sse() -> AsyncIterator[str]:
        try:
            async for payload in stream_tutor_turn(student, clean):
                yield _event(payload)
            yield _event({"done": True})
        except Exception as exc:
            # full detail (traceback included) stays server-side;
            # the student sees only the friendly classification
            log.exception("chat turn failed (student=%s)", student["id"])
            yield _event({"error": friendly_error(exc)})

    return StreamingResponse(sse(), media_type="text/event-stream")
