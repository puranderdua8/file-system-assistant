"""Tests for the tool-calling loop using a scripted fake client (no network)."""

import pytest
from google.genai import errors as genai_errors
from google.genai import types

import llm_file_assistant as lfa


def model_turn(*parts):
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=list(parts)))]
    )


def call(name, **args):
    return types.Part(function_call=types.FunctionCall(name=name, args=args))


def say(text):
    return model_turn(types.Part(text=text))


class FakeClient:
    """Returns scripted responses in order and records what it was sent."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.sent = []
        self.models = self

    def generate_content(self, *, model, contents, config):
        self.sent.append([c.model_copy(deep=True) for c in contents])
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    monkeypatch.setattr(lfa._generate.retry, "sleep", lambda s: None)


# ---------- declarations & execute_tool ----------
def test_declarations_match_registry():
    assert {d.name for d in lfa.TOOL_DECLARATIONS} == set(lfa.TOOL_REGISTRY)
    for d in lfa.TOOL_DECLARATIONS:
        assert d.description and d.parameters.required


def test_execute_tool_runs_real_tool(resumes):
    out = lfa.execute_tool("read_file", {"filepath": "resumes/alice.txt"})
    assert out["success"] and "Alice" in out["content"]


def test_execute_tool_wraps_list_results(resumes):
    out = lfa.execute_tool("list_files", {"directory": "resumes", "extension": "pdf"})
    assert [f["name"] for f in out["result"]] == ["carol.PDF"]


def test_execute_tool_unknown_and_bad_args():
    assert lfa.execute_tool("delete_everything", {})["success"] is False
    bad = lfa.execute_tool("read_file", {"wrong_arg": 1})
    assert bad["success"] is False and "TypeError" in bad["error"]
    assert lfa.execute_tool("read_file", None)["success"] is False


# ---------- run_agent loop ----------
def test_plain_answer_without_tools():
    client = FakeClient(say("Hello!"))
    assert lfa.run_agent("hi", client) == "Hello!"


def test_tool_roundtrip_and_history(resumes):
    client = FakeClient(
        model_turn(call("list_files", directory="resumes")),
        model_turn(call("search_in_file", filepath="resumes/alice.txt", keyword="python")),
        say("Alice mentions Python."),
    )
    seen, history = [], []
    answer = lfa.run_agent(
        "who knows python?", client, history, on_tool_call=lambda n, a: seen.append(n)
    )

    assert answer == "Alice mentions Python."
    assert seen == ["list_files", "search_in_file"]
    # user, model, tool-result, model, tool-result, model
    assert [c.role for c in history] == ["user", "model", "user", "model", "user", "model"]
    result = history[2].parts[0].function_response
    assert result.name == "list_files" and len(result.response["result"]) == 4
    assert history[4].parts[0].function_response.response["match_count"] == 2


def test_parallel_calls_answered_in_one_turn(resumes):
    client = FakeClient(
        model_turn(
            call("read_file", filepath="resumes/alice.txt"),
            call("read_file", filepath="resumes/bob.docx"),
        ),
        say("done"),
    )
    history = []
    lfa.run_agent("read both", client, history, on_tool_call=lambda n, a: None)
    responses = history[2].parts
    assert [p.function_response.name for p in responses] == ["read_file", "read_file"]


def test_tool_error_is_passed_back_to_model(resumes):
    client = FakeClient(model_turn(call("read_file", filepath="../secret.txt")), say("Can't."))
    history = []
    lfa.run_agent("read it", client, history, on_tool_call=lambda n, a: None)
    assert history[2].parts[0].function_response.response["success"] is False


def test_write_summary_via_tool(resumes, root):
    client = FakeClient(
        model_turn(call("write_file", filepath="summaries/a.txt", content="Alice summary")),
        say("Saved."),
    )
    lfa.run_agent("summarise", client, on_tool_call=lambda n, a: None)
    assert (root / "summaries/a.txt").read_text() == "Alice summary"


def test_history_persists_across_turns():
    client = FakeClient(say("one"), say("two"))
    history = []
    lfa.run_agent("first", client, history)
    lfa.run_agent("second", client, history)
    assert len(client.sent[1]) == 3  # user, model, user(second)


def test_max_steps_guard(resumes):
    looping = [model_turn(call("list_files", directory="resumes")) for _ in range(3)]
    out = lfa.run_agent("loop", FakeClient(*looping), max_steps=3, on_tool_call=lambda n, a: None)
    assert "Stopped after 3" in out


def test_empty_candidates_handled():
    out = lfa.run_agent("hi", FakeClient(types.GenerateContentResponse(candidates=[])))
    assert "no answer" in out


# ---------- retry ----------
def test_retries_transient_errors_then_succeeds():
    boom = genai_errors.ServerError(503, {"error": {"message": "overloaded"}})
    client = FakeClient(boom, boom, say("ok"))
    assert lfa.run_agent("hi", client) == "ok"


def test_does_not_retry_client_errors():
    bad = genai_errors.ClientError(400, {"error": {"message": "bad request"}})
    client = FakeClient(bad, say("never reached"))
    with pytest.raises(genai_errors.ClientError):
        lfa.run_agent("hi", client)


# ---------- honoring the server's retry hint ----------
def _quota_error(delay=None, message="quota exceeded"):
    err = {"message": message}
    if delay:
        err["details"] = [
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}
        ]
    return genai_errors.ClientError(429, {"error": err})


def test_server_retry_delay_parsing():
    assert lfa.server_retry_delay(_quota_error("44s")) == 44.0
    assert lfa.server_retry_delay(_quota_error(message="Please retry in 35.1s.")) == 35.1
    assert lfa.server_retry_delay(_quota_error()) is None
    assert lfa.server_retry_delay(ValueError("x")) is None


def test_429_waits_for_server_hint_and_retries(monkeypatch):
    sleeps = []
    monkeypatch.setattr(lfa._generate.retry, "sleep", sleeps.append)
    client = FakeClient(_quota_error("44s"), say("ok"))
    assert lfa.run_agent("hi", client) == "ok"
    assert sleeps == [45.0]


def test_server_hint_is_capped(monkeypatch):
    sleeps = []
    monkeypatch.setattr(lfa._generate.retry, "sleep", sleeps.append)
    lfa.run_agent("hi", FakeClient(_quota_error("600s"), say("ok")))
    assert sleeps == [lfa.MAX_RETRY_WAIT]
