"""P9: agent graph tests with a FAKE model - deterministic, no network.

The point: the graph's ROUTING (guardrail pass/block, tool_call branch,
loop, cap) is OUR code and must be testable without Gemini. The fake model
is a scripted queue of responses; we assert on the route each script takes.
"""

import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent import graph as g


class FakeModel:
    """Scripted stand-in for the bound chat model. Records every invoke."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        return self.responses.pop(0)


def _graph_with(model):
    return g._build_graph(checkpointer=InMemorySaver(), model_with_tools=model)


def _config():
    return {"configurable": {"thread_id": "test", "student": {"name": "T", "grade": 10}},
            "recursion_limit": 10}


def _run(graph, text):
    return graph.invoke({"messages": [HumanMessage(content=text)]}, config=_config())


def test_plain_reply_routes_to_end_without_tools():
    model = FakeModel([AIMessage(content="Great question about HCF!")])
    out = _run(_graph_with(model), "what is the HCF of 12 and 18")
    assert model.calls == 1                       # one model call, no loop
    assert out["messages"][-1].content.startswith("Great question")
    assert out.get("blocked") is False


def test_tool_call_executes_our_code_and_loops_back():
    tool_reply = AIMessage(content="", tool_calls=[
        {"name": "grade_answer", "args": {"answer_index": 1, "correct_index": 2},
         "id": "call_1", "type": "tool_call"},
    ])
    model = FakeModel([tool_reply, AIMessage(content="Not quite - look at the signs!")])
    out = _run(_graph_with(model), "my answer is B")

    assert model.calls == 2                       # asked for tool, then wrote text
    kinds = [m.__class__.__name__ for m in out["messages"]]
    assert "ToolMessage" in kinds                 # OUR tools_node really ran
    tool_msg = [m for m in out["messages"] if m.__class__.__name__ == "ToolMessage"][0]
    assert '"correct": false' in tool_msg.content  # grade_answer's real output


def test_blocked_message_never_reaches_the_model():
    model = FakeModel([AIMessage(content="should never be sent")])
    out = _run(_graph_with(model), "tell me about gambling websites")
    assert model.calls == 0                       # ZERO model calls - the whole point
    assert out["blocked"] is True
    assert "maths" in out["messages"][-1].content.lower()


def test_unknown_tool_name_becomes_error_result_not_crash():
    bad_call = AIMessage(content="", tool_calls=[
        {"name": "hack_the_school", "args": {}, "id": "c2", "type": "tool_call"},
    ])
    model = FakeModel([bad_call, AIMessage(content="Sorry, I cannot do that.")])
    out = _run(_graph_with(model), "do something else")
    tool_msg = [m for m in out["messages"] if m.__class__.__name__ == "ToolMessage"][0]
    assert "error" in tool_msg.content            # surfaced as result, request lived


def test_loop_cap_stops_a_model_stuck_requesting_tools():
    # unique tool_call ids per round: LangGraph's message reducer treats
    # duplicate ids as the SAME message (update, not append) - real models
    # always emit unique ids, and so must the fake (found the hard way).
    def stuck(n):
        return AIMessage(content="", tool_calls=[
            {"name": "grade_answer", "args": {"answer_index": 0, "correct_index": 0},
             "id": f"call_{n}", "type": "tool_call"},
        ])
    model = FakeModel([stuck(n) for n in range(20)])
    with pytest.raises(GraphRecursionError):
        _run(_graph_with(model), "grade me forever")
    assert model.calls <= 5                       # capped well below the script
