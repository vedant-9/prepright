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
from pathlib import Path
from typing import AsyncIterator

from langchain_core.messages import (AIMessage, AIMessageChunk, HumanMessage,
                                     SystemMessage, ToolMessage)
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, MessagesState, StateGraph

from app.agent.guardrails import SAFE_REFUSAL, is_blocked
from app.agent.prompts import SYSTEM_TEMPLATE
from app.llm.gateway import extract_text, get_chat_model
from app.tools import TOOLS

TOOL_MAP = {t.name: t for t in TOOLS}

# Loop cap: one full teaching turn can legitimately spend 3 tool rounds
# (profile -> curriculum -> quiz) = 8 super-steps (guardrail + agent/tools
# pairs + final agent reply). 10 leaves headroom while making runaway
# loops impossible. Set per request via config; measured in P6 E2E.
RECURSION_LIMIT = 10


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
    """Topic screen BEFORE any model spend. Blocked -> safe refusal + flag.

    (PII redaction already happened at the API boundary - it must never be
    stored in checkpoints. This node is the routing example: notice no LLM.)
    """
    last = state["messages"][-1]
    text = last.content if isinstance(last.content, str) else str(last.content)
    if is_blocked(text):
        return {"blocked": True, "messages": [AIMessage(content=SAFE_REFUSAL)]}
    return {"blocked": False}


def make_agent_node(model_with_tools):
    """The tutor's brain. Student identity rides in via config (NOT stored in
    state) so the system prompt is rebuilt fresh each request and never
    accumulates in checkpointed history."""

    def agent_node(state: TutorState, config: RunnableConfig):
        student = (config.get("configurable") or {}).get("student") or {}
        system = SystemMessage(content=SYSTEM_TEMPLATE.format(
            name=student.get("name", "friend"),
            grade=student.get("grade", 10),
        ))
        response = model_with_tools.invoke([system, *state["messages"]])
        return {"messages": [response]}

    return agent_node


def tools_node(state: TutorState):
    """Execute the model's tool requests with OUR code. Hand-rolled on
    purpose (langgraph.prebuilt.ToolNode exists) - visible beats convenient,
    and it's 15 lines. Errors become tool results, not crashes - the agent
    can apologize/retry instead of the request dying."""
    last = state["messages"][-1]
    results = []
    for call in last.tool_calls:
        tool = TOOL_MAP.get(call["name"])
        try:
            if tool is None:
                raise ValueError(f"unknown tool: {call['name']}")
            out = tool.invoke(call["args"])
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
    # tool_calls is [] (not None) when the model wants no tools
    return "tools" if getattr(last, "tool_calls", None) else END


def _build_graph(checkpointer):
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
            "student": {"name": student["name"], "grade": student["grade"]},
        },
    }
    inputs = {"messages": [HumanMessage(content=message)]}

    async for mode, payload in graph.astream(inputs, config=config,
                                             stream_mode=["messages", "updates"]):
        if mode == "messages":
            chunk, meta = payload
            # only tokens from the agent node (guardrail emits none anyway,
            # but being explicit documents intent)
            if isinstance(chunk, AIMessageChunk) and meta.get("langgraph_node") == "agent":
                text = extract_text(chunk.content)
                if text:
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
                    for m in update.get("messages", []):
                        for tc in getattr(m, "tool_calls", None) or []:
                            yield {"tool": tc["name"]}
