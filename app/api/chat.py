"""SSE chat endpoint - full edition (P6): stateful via the checkpointer.

WHAT CHANGED FROM THE THIN SLICE (P4):
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
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.graph import stream_tutor_turn
from app.agent.guardrails import redact_pii
from app.data.db import get_conn

router = APIRouter(prefix="/api", tags=["chat"])


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
        except Exception as exc:  # surface errors in-stream, never a dead hang
            yield _event({"error": str(exc)})

    return StreamingResponse(sse(), media_type="text/event-stream")
