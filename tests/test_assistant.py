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


# ---------- API errors ----------
def _quota_error():
    return genai_errors.ClientError(429, {"error": {"message": "quota exceeded"}})


def test_api_errors_propagate_without_retry():
    client = FakeClient(_quota_error(), say("never reached"))
    with pytest.raises(genai_errors.ClientError):
        lfa.run_agent("hi", client)
    assert len(client.sent) == 1  # one attempt only


def test_cli_reports_api_error_and_exits_cleanly(monkeypatch, capsys):
    monkeypatch.setattr(lfa, "make_client", lambda: FakeClient(_quota_error()))
    lfa.main(["hi"])
    assert "Gemini API error (429)" in capsys.readouterr().err


# ---------- sandbox defaults for the assistant ----------
@pytest.fixture
def clean_root_env(monkeypatch):
    monkeypatch.setenv("FS_ROOT", "placeholder")  # so teardown restores the original
    monkeypatch.delenv("FS_ROOT")


def test_assistant_defaults_sandbox_to_data_dir(clean_root_env):
    import os

    lfa.configure_sandbox()
    assert os.environ["FS_ROOT"] == str(lfa.DATA_DIR)
    assert lfa.DATA_DIR.name == "data"


def test_assistant_replaces_empty_fs_root(monkeypatch):
    import os

    monkeypatch.setenv("FS_ROOT", "")
    lfa.configure_sandbox()
    assert os.environ["FS_ROOT"] == str(lfa.DATA_DIR)


def test_assistant_respects_explicit_fs_root(monkeypatch, tmp_path):
    import os

    monkeypatch.setenv("FS_ROOT", str(tmp_path))
    lfa.configure_sandbox()
    assert os.environ["FS_ROOT"] == str(tmp_path)


def test_main_turns_the_sandbox_on(clean_root_env, monkeypatch, capsys):
    import os

    monkeypatch.setattr(lfa, "make_client", lambda: FakeClient(say("hello")))
    lfa.main(["hi"])
    assert os.environ["FS_ROOT"] == str(lfa.DATA_DIR)
    assert "hello" in capsys.readouterr().out
