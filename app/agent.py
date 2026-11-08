"""The tutor agent — thin-slice edition (P4): one node, no tools yet.

LANGGRAPH MENTAL MODEL (this is the core concept of the whole project):
- A graph = STATE flowing through NODES. State here is `MessagesState`,
  LangGraph's built-in {"messages": [...]}. Nodes are plain functions:
  state in -> state-delta out.
- The magic is the REDUCER on the messages key (add_messages): when a node
  returns {"messages": [reply]}, it APPENDS to history rather than replacing
  it. That reducer IS this thin slice's memory.
- This file grows with the product: P5 adds a tools node + conditional
  edges (the loop that makes it an *agent*), P6 adds the guardrail node
  and the durable checkpointer.
"""

from typing import AsyncIterator

from langchain_core.messages import AIMessageChunk, BaseMessage, SystemMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from app.llm import get_chat_model, _extract_text

SYSTEM_TEMPLATE = """You are PrepRight, a friendly, patient AI math tutor for a CBSE student in India.

You are tutoring: {name}, Class {grade} (CBSE).

Teaching rules:
1. Socratic first. When the student asks for an answer, respond with a hint or a guiding question. Reveal the full solution step-by-step only after they have attempted it.
2. Keep each turn short: 2-4 sentences of explanation, then one small question to check understanding.
3. Use simple language and everyday examples (cricket, trains, money, mangoes).
4. Wrong answers are data, not failures: say what was right, then fix exactly the gap. Never shame.
5. Stay on mathematics and study skills. If asked about anything else, warmly steer back to math.
6. Never ask for personal information (full name, address, school, phone numbers).
7. If the student is frustrated, slow down, simplify, and be kind before continuing.
"""


def _build_graph():
    """One node: tutor -> END. Compiled once at module import, reused per request.

    Note the system prompt is NOT baked in here — it carries per-student
    fields (name/grade), so it's prepended to the INPUT messages at call
    time (see stream_tutor_reply). Keeps the graph static and reusable.
    """
    model = get_chat_model()

    def tutor_node(state: MessagesState):
        response = model.invoke(state["messages"])
        # returns a DELTA: [reply] gets appended to history by the reducer
        return {"messages": [response]}

    graph = StateGraph(MessagesState)
    graph.add_node("tutor", tutor_node)
    graph.add_edge(START, "tutor")
    graph.add_edge("tutor", END)
    return graph.compile()


graph = _build_graph()


async def stream_tutor_reply(student: dict, history: list[BaseMessage]) -> AsyncIterator[str]:
    """Yield the tutor's reply as text deltas (token streaming).

    stream_mode="messages" emits AIMessageChunks AS THE MODEL GENERATES
    them — this is what makes a ~30-60s Gemini generation feel responsive
    in the browser. (The alternative, stream_mode="values", would deliver
    one finished message at the end: a spinner for a minute.)
    """
    system = SystemMessage(content=SYSTEM_TEMPLATE.format(
        name=student["name"], grade=student["grade"],
    ))
    inputs = {"messages": [system, *history]}

    async for chunk, _meta in graph.astream(inputs, stream_mode="messages"):
        # The stream yields (message_chunk, metadata) pairs; we only care
        # about AI chunks (there's exactly one LLM call in this graph).
        if isinstance(chunk, AIMessageChunk):
            text = _extract_text(chunk.content)  # Gemini 3.x content-block quirk
            if text:
                yield text
