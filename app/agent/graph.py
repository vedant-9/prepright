"""The tutor agent - full edition (P6): guardrail -> agent + tool loop.

THE GRAPH (matches the 'Agent loop' diagram):

    START -> [guardrail] --blocked--> END (safe refusal, no model call)
                |pass
                v
            [agent: LLM + tools bound]
                |-- reply has tool_calls --> [tools] --results--> back to agent
                |-- plain text --------------------------> END (SSE stream)

THE THREE CONCEPTS THIS FILE DEMONSTRATES:
1. CONDITIONAL EDGES are what make it an agent: route_after_agent() checks
   whether the model ASKED to run a tool. Chatbot = always END; agent = can
   loop through tools first.
2. THE MODEL NEVER EXECUTES ANYTHING. It emits a tool_call (name + JSON
   args); tools_node runs OUR code and returns ToolMessages. The loop cap
   (recursion_limit, set by the API config) bounds runaway models.
3. THE CHECKPOINTER is durable per-student memory: state is auto-saved
   after EVERY step keyed by thread_id, and reloaded on the next request -
   that is server-side chat history per user.
"""

import json
import logging
from pathlib import Path
from typing import AsyncIterator

from langchain_core.messages import (AIMessage, AIMessageChunk, HumanMessage,
                                     SystemMessage, ToolMessage)
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, MessagesState, StateGraph

from app.agent.guardrails import OFF_TOPIC_REPLY, SAFE_REFUSAL, is_blocked, is_off_topic
from app.agent.prompts import SYSTEM_TEMPLATE
from app.llm.gateway import (_fallback_provider, extract_text, get_chat_model,
                             is_quota_error)
from app.tools import TOOLS

TOOL_MAP = {t.name: t for t in TOOLS}
log = logging.getLogger("prepright.agent")

# Loop cap (measured in P9.1 E2E): a full quiz turn spends guardrail +
# profile/curriculum/quiz rounds + 5 batched grade_answer calls +
# record_progress + review = ~14 super-steps. 16 leaves headroom while
# still making runaway loops impossible.
RECURSION_LIMIT = 16


class TutorState(MessagesState):
    """MessagesState + a blocked flag the guardrail sets for routing."""
    blocked: bool


# ---------------------------------------------------------------- checkpointer
# Separate database from prepright.db on purpose: these are LangGraph's
# tables, and a framework upgrade should never touch our domain schema.
#
# ASYNC SAVER + STARTUP COMPILE (a P6 war story): the sync SqliteSaver
# cannot serve graph.astream() - it raised at the first request. The async
# saver is an async context manager, so the graph now compiles ONCE at app
# startup (see main.py lifespan -> init_graph), not at import time.

CHECKPOINTS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "checkpoints.db"

graph = None  # built at app startup by init_graph()
_saver_cm = None


async def init_graph() -> None:
    """Called from FastAPI lifespan: enter the saver, compile the graph."""
    global graph, _saver_cm
    CHECKPOINTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _saver_cm = AsyncSqliteSaver.from_conn_string(str(CHECKPOINTS_PATH))
    saver = await _saver_cm.__aenter__()
    graph = _build_graph(checkpointer=saver)
    # NOTE: no explicit __aexit__ - the saver lives for the process lifetime
    # (uvicorn shutdown closes everything). Acceptable at POC scale.


# ---------------------------------------------------------------- nodes

def guardrail_node(state: TutorState):
    """Screens BEFORE any model spend. Two zero-token exits:
    - blocked (unsafe list)   -> SAFE_REFUSAL
    - off-topic (embedding similarity below threshold, e.g. "who is modi")
                             -> OFF_TOPIC_REPLY - no chat tokens wasted
    (PII redaction already happened at the API boundary - it must never be
    stored in checkpoints. This node is the routing example: notice no LLM.)
    """
    last = state["messages"][-1]
    text = last.content if isinstance(last.content, str) else str(last.content)
    if is_blocked(text):
        return {"blocked": True, "messages": [AIMessage(content=SAFE_REFUSAL)]}
    if is_off_topic(text):
        return {"blocked": True, "messages": [AIMessage(content=OFF_TOPIC_REPLY)]}
    return {"blocked": False}


def make_agent_node(model_with_tools):
    """The tutor's brain. Student identity rides in via config (NOT stored in
    state) so the system prompt is rebuilt fresh each request and never
    accumulates in checkpointed history.

    QUOTA FALLBACK (found the hard way in P8 E2E): bind_tools().invoke()
    is a different call path from chat() and had NO fallback - one 429
    killed the whole turn. Now: primary -> on quota error, retry once on
    the fallback provider's tool-bound twin (if a fallback key exists).
    """
    fallback_model = None
    if (fb := _fallback_provider()):
        fallback_model = get_chat_model(fb).bind_tools(TOOLS)

    def agent_node(state: TutorState, config: RunnableConfig):
        student = (config.get("configurable") or {}).get("student") or {}
        system = SystemMessage(content=SYSTEM_TEMPLATE.format(
            name=student.get("name", "friend"),
            grade=student.get("grade", 10),
        ))
        messages = [system, *state["messages"]]
        try:
            response = model_with_tools.invoke(messages)
        except Exception as exc:
            # Fallback on ANY primary failure (P9.1 hardening): the original
            # retry-only-on-quota design died when a schema-validation error
            # hit Groq and Gemini was quota-dead in the same breath. For a
            # kids' app, availability beats purity - but NEVER silently:
            # every fallback is logged with both reasons.
            if fallback_model is None:
                raise
            log.warning("primary model failed (%s: %s) - falling back",
                        type(exc).__name__, str(exc)[:200])
            response = fallback_model.invoke(messages)
        return {"messages": [response]}

    return agent_node


def tools_node(state: TutorState, config: RunnableConfig):
    """Execute the model's tool requests with OUR code. Hand-rolled on
    purpose (langgraph.prebuilt.ToolNode exists) - visible beats convenient.
    IDENTITY INJECTION: student_id comes from the server-side config and is
    merged into every call's args - the model never supplies (or forges)
    who it is acting for. Errors become tool results, not crashes; even
    READING .tool_calls is armored (malformed args JSON raises there)."""
    last = state["messages"][-1]
    student = (config.get("configurable") or {}).get("student") or {}
    results = []
    try:
        calls = list(last.tool_calls or [])
    except Exception as exc:  # malformed tool_call from the model
        return {"messages": [ToolMessage(
            content=json.dumps({"error": f"malformed tool call: {exc}"}),
            tool_call_id="parse_error")]}
    for call in calls:
        tool = TOOL_MAP.get(call["name"])
        try:
            if tool is None:
                raise ValueError(f"unknown tool: {call['name']}")
            args = dict(call["args"])
            if "student_id" in getattr(tool, "args", {}):
                args["student_id"] = student.get("id", 0)  # server-injected
            out = tool.invoke(args)
            content = json.dumps(out, default=str)  # tools return dicts
        except Exception as exc:
            content = json.dumps({"error": str(exc)}, default=str)
        results.append(ToolMessage(content=content, tool_call_id=call["id"]))
    return {"messages": results}


# ---------------------------------------------------------------- routing

def route_after_guardrail(state: TutorState) -> str:
    return END if state.get("blocked") else "agent"


def route_after_agent(state: TutorState) -> str:
    last = state["messages"][-1]
    # NOTE: getattr(x, "tool_calls", None) does NOT catch exceptions raised
    # INSIDE the property - malformed args JSON raises here. Route to tools
    # anyway: tools_node is armored and will hand the parse error back to
    # the model as a ToolMessage so it can retry.
    try:
        calls = last.tool_calls
    except Exception:
        return "tools"
    # tool_calls is [] (not None) when the model wants no tools
    return "tools" if calls else END


def _build_graph(checkpointer, model_with_tools=None):
    # model injectable for deterministic fake-model tests (test_agent.py)
    if model_with_tools is None:
        model_with_tools = get_chat_model().bind_tools(TOOLS)

    graph = StateGraph(TutorState)
    graph.add_node("guardrail", guardrail_node)
    graph.add_node("agent", make_agent_node(model_with_tools))
    graph.add_node("tools", tools_node)

    graph.add_edge(START, "guardrail")
    graph.add_conditional_edges("guardrail", route_after_guardrail, ["agent", END])
    graph.add_conditional_edges("agent", route_after_agent, ["tools", END])
    graph.add_edge("tools", "agent")  # the loop: results go back to the model

    return graph.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------- streaming

async def stream_tutor_turn(student: dict, message: str,
                            ) -> AsyncIterator[dict]:
    """Run one full turn (possibly several tool hops) and yield SSE payloads.

    Dual-mode streaming:
    - "messages" mode gives us TOKENS as the model generates (agent node only)
    - "updates" mode gives us node-level events: tool_calls requested by the
      agent (-> {"tool": name} chips in the UI) and guardrail refusals.

    History is NOT in the inputs - the checkpointer restores it from the
    student's thread. The client sends only the new message.
    """
    if graph is None:
        raise RuntimeError("graph not initialized - call init_graph() at startup")

    config = {
        "recursion_limit": RECURSION_LIMIT,
        "configurable": {
            "thread_id": f"student-{student['id']}",
            "student": {"id": student["id"], "name": student["name"],
                        "grade": student["grade"]},
        },
    }
    inputs = {"messages": [HumanMessage(content=message)]}

    # OUTPUT AUDIT (P9.1): streamed tokens cannot be un-sent, so a full
    # output classifier on free text is a documented future piece (needs
    # buffered streaming). What we CAN do cheaply: audit the assembled
    # reply against the same blocklist as the input screen. A hit cannot
    # retract the text, but it is logged loudly - for a pilot, silent
    # failures are the real enemy; every hit becomes a data point to
    # tighten prompts/screens.
    final_text: list[str] = []
    audit_hit = False

    async for mode, payload in graph.astream(inputs, config=config,
                                             stream_mode=["messages", "updates"]):
        if mode == "messages":
            chunk, meta = payload
            # only tokens from the agent node (guardrail emits none anyway,
            # but being explicit documents intent)
            if isinstance(chunk, AIMessageChunk) and meta.get("langgraph_node") == "agent":
                text = extract_text(chunk.content)
                if text:
                    final_text.append(text)
                    yield {"delta": text}
        else:  # updates: {node_name: state_delta}
            for node, update in (payload or {}).items():
                if not isinstance(update, dict):
                    continue
                if node == "guardrail" and update.get("blocked"):
                    # refusal message from the guardrail (no LLM was called)
                    for m in update.get("messages", []):
                        yield {"delta": m.content}
                if node == "agent":
                    # surface tool activity the moment the model requests it
                    # (same property-access caveat as route_after_agent)
                    for m in update.get("messages", []):
                        try:
                            calls = m.tool_calls or []
                        except Exception:
                            calls = []
                        for tc in calls:
                            yield {"tool": tc["name"]}
                if node == "tools":
                    # STRUCTURED QUIZ DELIVERY (P9.1 architecture fix): when
                    # generate_quiz runs, emit the quiz itself as an event.
                    # The frontend renders cards from DATA, not from model
                    # prose - the model physically cannot hallucinate or
                    # reformat the questions. Answers are stripped here:
                    # the key stays server-side (in the model's tool result)
                    # and NEVER reaches the browser.
                    for m in update.get("messages", []):
                        if m.__class__.__name__ != "ToolMessage":
                            continue
                        try:
                            blob = json.loads(m.content)
                        except (TypeError, json.JSONDecodeError):
                            continue
                        if isinstance(blob, dict) and blob.get("questions"):
                            yield {"quiz": {
                                "chapter": blob.get("chapter"),
                                "difficulty": blob.get("difficulty"),
                                "questions": [
                                    {"num": i + 1,
                                     "text": q["question_text"],
                                     "options": q["options"]}
                                    for i, q in enumerate(blob["questions"])
                                ],
                            }}

    # post-turn output audit (see comment at final_text declaration)
    if is_blocked("".join(final_text)):
        log.warning("OUTPUT AUDIT: model reply tripped the blocklist; "
                    "prompt/screens need tightening (thread=%s)",
                    config["configurable"]["thread_id"])
        yield {"audit": "flagged"}
