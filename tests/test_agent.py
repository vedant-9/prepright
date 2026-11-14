"""agent graph tests with a FAKE model - deterministic, no network.

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


@pytest.fixture(autouse=True)
def _hermetic_guardrail(monkeypatch):
    """Routing tests must not depend on live embeddings: pin the off-topic
    classifier to a fixed answer (it is tested separately in
    test_guardrails.py). Found the hard way: "do something else" scored
    off-topic against the real API and silently rerouted the test."""
    monkeypatch.setattr("app.agent.graph.is_off_topic", lambda text, **kw: False)


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
    # always emit unique ids, and so must the fake (
    def stuck(n):
        return AIMessage(content="", tool_calls=[
            {"name": "grade_answer", "args": {"answer_index": 0, "correct_index": 0},
             "id": f"call_{n}", "type": "tool_call"},
        ])
    model = FakeModel([stuck(n) for n in range(20)])
    with pytest.raises(GraphRecursionError):
        _run(_graph_with(model), "grade me forever")
    assert model.calls <= 5                       # capped well below the script


# ------------------------------------------------------------ condense (memory compression)

def _graph_with_summarizer(model, summarizer):
    return g._build_graph(checkpointer=InMemorySaver(),
                          model_with_tools=model, summarizer=summarizer)


def _seeded_state(n_messages):
    """Alternating human/ai turns like a real conversation."""
    msgs = []
    for i in range(n_messages // 2):
        msgs.append(HumanMessage(content=f"question {i}"))
        msgs.append(AIMessage(content=f"answer {i}"))
    return msgs


def test_condense_noop_under_threshold():
    model = FakeModel([AIMessage(content="ok")])
    seen = []
    summarizer = lambda text: seen.append(text) or "SUMMARY"   # noqa: E731
    graph = _graph_with_summarizer(model, summarizer)
    # seed history via two turns (4 messages), then a third - all below 24
    for text in ("q1", "q2", "q3"):
        model.responses = [AIMessage(content="ok")]
        graph.invoke({"messages": [HumanMessage(content=text)]}, config=_config())
    assert seen == []                             # summarizer never called
    assert model.calls == 3


def test_condense_folds_old_turns_and_keeps_tail():
    model = FakeModel([AIMessage(content="fresh reply")])
    captured = {}
    original_invoke = model.invoke
    def capturing_invoke(messages):
        captured["messages"] = messages
        return original_invoke(messages)
    model.invoke = capturing_invoke

    def summarizer(text):
        return "compressed memory"

    graph = _graph_with_summarizer(model, summarizer)
    seeded = _seeded_state(30)                     # 30 messages -> over CONDENSE_AFTER
    out = graph.invoke({"messages": [*seeded, HumanMessage(content="new question")]},
                       config=_config())

    assert out["summary"] == "compressed memory"
    assert len(out["messages"]) < 31               # old turns actually removed
    assert out["messages"][-1].content == "fresh reply"
    # the model SAW the summary, injected as a system message under the prompt
    assert any(getattr(m, "content", "").startswith("Summary of earlier turns")
               and "compressed memory" in m.content
               for m in captured["messages"][:3])


def test_condense_never_splits_tool_pairs():
    """AI(tool_calls) + ToolMessage must survive together: seed a history
    where the cut boundary would land between them if naive."""
    from langchain_core.messages import ToolMessage
    pair_mid = [
        AIMessage(content="", tool_calls=[{"name": "calculate",
                     "args": {"expression": "7*8"}, "id": "c1", "type": "tool_call"}]),
        ToolMessage(content='{"result": 56}', tool_call_id="c1"),
    ]
    seeded = _seeded_state(20) + pair_mid + _seeded_state(6)
    seeded.append(HumanMessage(content="next question"))

    model = FakeModel([AIMessage(content="ok")])
    removed_ids = []
    def summarizer(text):
        return "s"
    graph = _graph_with_summarizer(model, summarizer)
    out = graph.invoke({"messages": seeded}, config=_config())

    kinds = [m.__class__.__name__ for m in out["messages"]]
    # any ToolMessage present must still be preceded by its AI tool_call message
    for i, k in enumerate(kinds):
        if k == "ToolMessage":
            assert kinds[i - 1] == "AIMessage"
    assert out["summary"] == "s"


def test_condense_fail_open_on_summarizer_death():
    model = FakeModel([AIMessage(content="still teaching")])
    def dead_summarizer(text):
        raise RuntimeError("provider quota gone")
    graph = _graph_with_summarizer(model, dead_summarizer)
    seeded = _seeded_state(30)
    out = graph.invoke({"messages": [*seeded, HumanMessage(content="q")]}, config=_config())
    # full history proceeded, tutor answered, nothing crashed
    assert out["messages"][-1].content == "still teaching"
    assert len(out["messages"]) == 31 + 1          # 30 seeded + new human + ai reply
    assert not out.get("summary")
