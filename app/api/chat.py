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

import asyncio
import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.graph import stream_tutor_turn
from app.agent.guardrails import redact_pii
from app.data.db import get_conn
from app.llm.gateway import is_quota_error, vision_extract

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


# Notebook photo input. PRIVACY CANNOT BE RETROFITTED, so it is the design:
# - the client re-encodes through a canvas before upload, which destroys
#   EXIF/GPS metadata and downsizes, all device-side
# - the image lives in request memory ONLY: passed to the vision call,
#   never stored, logged, checkpointed, or written to the messages table
# - what enters the system is the EXTRACTED TEXT the student confirms
class PhotoIn(BaseModel):
    student_id: int
    # raw base64 JPEG (what canvas.toDataURL produces), no data: prefix
    image_b64: str = Field(min_length=100, max_length=2_000_000)
    # the student's own words typed next to the attached photo (optional)
    prompt: str = Field(default="", max_length=2000)


EXTRACTION_PROMPT = (
    "This is a photo of a schoolkid's maths notebook or textbook. "
    "Transcribe the ONE maths problem that is the clearest/central on the "
    "page, exactly as written (keep numbers, symbols and units; use plain "
    "text like x^2 and sqrt()). Do NOT solve it. If there is no maths "
    "problem visible, reply exactly: NO MATHS FOUND")


@router.post("/photo")
async def photo(body: PhotoIn) -> dict:
    """Vision extraction for attached photos. The student's typed prompt
    rides along as context for the extraction. Returns the proposed text;
    the CLIENT shows it for the student to confirm or edit before it ever
    becomes a chat message (extraction is a proposal - the student is
    ground truth)."""
    _get_student(body.student_id)
    instruction = EXTRACTION_PROMPT
    if body.prompt.strip():
        instruction += f"\nThe student asks: {body.prompt.strip()}"
    try:
        # to_thread is not optional: vision_extract is a SYNCHRONOUS SDK
        # call, and running it inline parks the whole event loop for the
        # duration of a slow provider read. The thread keeps the loop -
        # and every other student - alive.
        text = (await asyncio.to_thread(vision_extract, body.image_b64, instruction)).strip()
    except Exception as exc:
        log.exception("photo extraction failed (student=%s)", body.student_id)
        raise HTTPException(status_code=503, detail=friendly_error(exc))
    if not text or "NO MATHS FOUND" in text.upper():
        return {"found": False}
    return {"found": True, "text": text[:2000]}


def _get_student(student_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="student not found")
    return dict(row)


def _event(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _save_turn(student_id: int, user_text: str, ai_text: str,
               quiz: dict | None = None) -> None:
    """Persist a COMPLETED exchange for UI replay. Called only on success:
    a failed turn writes nothing, so a retry reads as a fresh question.
    Stores the PII-redacted text only (the no-PII-in-storage invariant).
    A structured quiz payload is stored as its own row AFTER the ai text
    (matching live render order: intro, then cards) so replay can show
    real read-only cards instead of a conversation about invisible
    questions."""
    rows = [(student_id, "user", user_text), (student_id, "ai", ai_text)]
    if quiz:
        rows.append((student_id, "quiz", json.dumps(quiz, default=str)))
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO messages (student_id, role, content) VALUES (?, ?, ?)",
            rows)


@router.post("/chat")
async def chat(body: ChatIn) -> StreamingResponse:
    student = _get_student(body.student_id)
    # Boundary guardrail: redact BEFORE storing/sending anywhere.
    clean = redact_pii(body.message.strip())

    async def sse() -> AsyncIterator[str]:
        ai_text: list[str] = []
        quiz_payload: dict | None = None
        try:
            async for payload in stream_tutor_turn(student, clean):
                if "delta" in payload:
                    ai_text.append(payload["delta"])
                elif "quiz" in payload:
                    quiz_payload = payload["quiz"]
                yield _event(payload)
            yield _event({"done": True})
            if ai_text:
                _save_turn(student["id"], clean, "".join(ai_text), quiz_payload)
        except Exception as exc:
            # full detail (traceback included) stays server-side;
            # the student sees only the friendly classification
            log.exception("chat turn failed (student=%s)", student["id"])
            yield _event({"error": friendly_error(exc)})

    return StreamingResponse(sse(), media_type="text/event-stream")
