"""SSE chat endpoint — the thin slice (P4).

STATELESS BY DESIGN (for now): the client sends the full conversation
history with every request; the server keeps no session state. That's the
simplest correct thing for a first slice. P6 replaces this with LangGraph's
checkpointer (server-side durable state, resumable threads), and this
endpoint shrinks to sending only the new message.

SSE (Server-Sent Events) protocol refresher: a long-lived HTTP response of
`text/event-stream`, where each event is `data: <json>\n\n`. The browser
reads it with fetch + ReadableStream (native EventSource only does GET).
"""

import json
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel, Field

from app.agent.graph import stream_tutor_reply
from app.data.db import get_conn

router = APIRouter(prefix="/api", tags=["chat"])


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=4000)


class ChatIn(BaseModel):
    student_id: int
    # Full history (thin-slice contract; see module docstring).
    messages: list[ChatMessage] = Field(min_length=1, max_length=60)
    # max_length=60 caps prompt size — a crude token-budget guard until
    # real context management arrives with the checkpointer in P6.


def _to_langchain(msg: ChatMessage) -> BaseMessage:
    return HumanMessage(content=msg.content) if msg.role == "user" else AIMessage(content=msg.content)


def _get_student(student_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="student not found")
    return dict(row)


@router.post("/chat")
async def chat(body: ChatIn) -> StreamingResponse:
    student = _get_student(body.student_id)
    history = [_to_langchain(m) for m in body.messages]

    async def sse() -> AsyncIterator[str]:
        try:
            async for delta in stream_tutor_reply(student, history):
                yield f"data: {json.dumps({'delta': delta})}\n\n"
            yield 'data: {"done": true}\n\n'
        except Exception as exc:  # surface errors to the client stream, not a dead hang
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")
