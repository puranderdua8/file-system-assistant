"""Chat with your resume folder: a Gemini-powered assistant that calls the file tools.

Usage:
    python llm_file_assistant.py                       # interactive session
    python llm_file_assistant.py "Find resumes mentioning Python experience"

The loop is written out explicitly (no automatic function calling) so every step
is visible: the model asks for a tool, we run it, and we send the result back.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Callable
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

import fs_tools
from errors import error_response

DEFAULT_MODEL = "gemini-flash-latest"
MAX_STEPS = 10

SYSTEM_PROMPT = """\
You are a file assistant for a folder of resumes (PDF, DOCX, TXT, MD).
Use the provided tools to answer; never guess what a file contains.

- Resumes live in the `resumes` folder unless the user says otherwise.
- Call list_files first when you don't know exact file names. Never invent file names.
- To find resumes mentioning a skill, run search_in_file on each candidate file,
  then report which files matched, with a short quoted snippet for each.
- To summarise a resume, read_file it, then save the summary with write_file under
  `summaries/` (e.g. summaries/resume_john_doe_summary.txt) and say where it was saved.
- If a tool returns an error, explain it plainly and try a sensible alternative.
- Be concise. Refer to people and files by name.
"""

# --- Tool interface -----------------------------------------------------------
# The declarations are what the model sees; the registry is what we actually run.

_STR = types.Type.STRING

TOOL_DECLARATIONS = [
    types.FunctionDeclaration(
        name="read_file",
        description=(
            "Read a resume file (.pdf, .docx, .txt, .md) and return its text content "
            "plus metadata (size, modified date, word count, page count)."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={"filepath": types.Schema(type=_STR, description="Path to the file.")},
            required=["filepath"],
        ),
    ),
    types.FunctionDeclaration(
        name="list_files",
        description=(
            "List the files in a directory with name, size and modified date. "
            "Optionally keep only files with one extension."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "directory": types.Schema(type=_STR, description="Directory path, e.g. 'resumes'."),
                "extension": types.Schema(
                    type=_STR, description="Optional extension filter such as '.pdf' or 'txt'."
                ),
            },
            required=["directory"],
        ),
    ),
    types.FunctionDeclaration(
        name="write_file",
        description=(
            "Write text to a file (.txt, .md, .json or .csv), creating folders as needed. "
            "Overwrites an existing file. Use it to save summaries and reports."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "filepath": types.Schema(type=_STR, description="Destination path."),
                "content": types.Schema(type=_STR, description="Text to write."),
            },
            required=["filepath", "content"],
        ),
    ),
    types.FunctionDeclaration(
        name="search_in_file",
        description=(
            "Case-insensitive keyword search inside one file. Returns every match with "
            "its line number and the surrounding text."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "filepath": types.Schema(type=_STR, description="Path to the file to search."),
                "keyword": types.Schema(type=_STR, description="Word or phrase to look for."),
            },
            required=["filepath", "keyword"],
        ),
    ),
]

TOOL_REGISTRY: dict[str, Callable[..., Any]] = {
    "read_file": fs_tools.read_file,
    "list_files": fs_tools.list_files,
    "write_file": fs_tools.write_file,
    "search_in_file": fs_tools.search_in_file,
}


def execute_tool(name: str, args: dict[str, Any] | None) -> dict[str, Any]:
    """Run a tool requested by the model; always returns a dict the model can read."""
    func = TOOL_REGISTRY.get(name)
    if func is None:
        return {"success": False, "error": f"Unknown tool '{name}'"}
    try:
        result = func(**(args or {}))
    except Exception as exc:  # bad/missing arguments from the model, or a tool bug
        return error_response(exc)
    # Gemini function responses must be objects, but list_files returns a list.
    return result if isinstance(result, dict) else {"result": result}


# --- Model calls --------------------------------------------------------------


def _is_transient(exc: BaseException) -> bool:
    return isinstance(exc, genai_errors.APIError) and (exc.code == 429 or (exc.code or 0) >= 500)


MAX_RETRY_WAIT = 60.0
_backoff = wait_exponential(multiplier=1, max=10)


def server_retry_delay(exc: BaseException) -> float | None:
    """Seconds the API asked us to wait (RetryInfo.retryDelay or 'retry in 44.4s')."""
    if not isinstance(exc, genai_errors.APIError):
        return None
    body = exc.details  # the parsed error body, e.g. {"error": {"details": [...]}}
    if isinstance(body, dict):
        body = (body.get("error") or {}).get("details") or []
    for item in body if isinstance(body, list) else []:
        match = re.fullmatch(r"([\d.]+)s", str(item.get("retryDelay", "")))
        if match:
            return float(match.group(1))
    match = re.search(r"retry in ([\d.]+)s", str(exc.message or ""), re.IGNORECASE)
    return float(match.group(1)) if match else None


def _wait(state: RetryCallState) -> float:
    """Honor the server's retry hint when given; otherwise back off exponentially."""
    hinted = server_retry_delay(state.outcome.exception()) if state.outcome else None
    if hinted is not None:
        return min(hinted + 1, MAX_RETRY_WAIT)
    return _backoff(state)


def _announce_retry(state: RetryCallState) -> None:
    exc = state.outcome.exception() if state.outcome else None
    wait = state.next_action.sleep if state.next_action else 0
    code = getattr(exc, "code", "?")
    print(f"  ⏳ API returned {code}; retrying in {wait:.0f}s...", file=sys.stderr)


@retry(
    retry=retry_if_exception(_is_transient),
    wait=_wait,
    stop=stop_after_attempt(4),
    before_sleep=_announce_retry,
    reraise=True,
)
def _generate(client: Any, model: str, contents: list[types.Content]) -> Any:
    return client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[types.Tool(function_declarations=TOOL_DECLARATIONS)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        ),
    )


def _log_tool_call(name: str, args: dict[str, Any]) -> None:
    shown = ", ".join(f"{k}={str(v)[:60]!r}" for k, v in args.items())
    print(f"  🔧 {name}({shown})", file=sys.stderr)


def run_agent(
    query: str,
    client: Any,
    history: list[types.Content] | None = None,
    model: str = DEFAULT_MODEL,
    max_steps: int = MAX_STEPS,
    on_tool_call: Callable[[str, dict[str, Any]], None] = _log_tool_call,
) -> str:
    """Answer ``query``, letting the model call tools until it produces a final reply.

    ``history`` is extended in place, so passing the same list across calls gives
    a multi-turn conversation.
    """
    history = history if history is not None else []
    history.append(types.Content(role="user", parts=[types.Part(text=query)]))

    for _ in range(max_steps):
        response = _generate(client, model, history)
        if not response.candidates or response.candidates[0].content is None:
            return "The model returned no answer (it may have been blocked). Try rephrasing."
        content = response.candidates[0].content
        history.append(content)  # keep the model turn exactly as returned

        calls = [p.function_call for p in content.parts or [] if p.function_call]
        if not calls:
            return "".join(p.text for p in content.parts or [] if p.text and not p.thought).strip()

        results = []
        for call in calls:
            args = dict(call.args or {})
            on_tool_call(call.name, args)
            results.append(
                types.Part.from_function_response(
                    name=call.name, response=execute_tool(call.name, args)
                )
            )
        history.append(types.Content(role="user", parts=results))

    return f"Stopped after {max_steps} tool-calling steps without a final answer."


# --- CLI ----------------------------------------------------------------------


def make_client() -> genai.Client:
    load_dotenv()
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
    return genai.Client(api_key=key)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Chat with your resume folder.")
    parser.add_argument("query", nargs="?", help="One-shot question. Omit for interactive mode.")
    parser.add_argument("--model", default=None, help=f"Gemini model (default {DEFAULT_MODEL}).")
    args = parser.parse_args(argv)

    client = make_client()
    model = args.model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL
    history: list[types.Content] = []

    def ask(q: str) -> None:
        try:
            print(run_agent(q, client, history, model=model))
        except genai_errors.APIError as exc:
            print(f"Gemini API error ({exc.code}): {exc.message}", file=sys.stderr)

    if args.query:
        ask(args.query)
        return

    print(f"File assistant ({model}). Type 'exit' to quit.")
    while True:
        try:
            q = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if q.lower() in {"exit", "quit"}:
            break
        if q:
            ask(q)


if __name__ == "__main__":
    main()
