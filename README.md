# File System Assistant

Sandboxed file-system tools for working with resume files (PDF, DOCX, TXT/MD),
designed to be exposed to an LLM through function calling.

> Status: both parts are implemented: the tools (Part A) and the Gemini assistant (Part B).

## Setup

Requires **Python 3.10+** (the dependencies need it; macOS's system `python3` is older, so use the venv).

```bash
python3 -m venv .venv           # must be Python >= 3.10: check with `python3 --version`
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env           # then add your GEMINI_API_KEY (needed for Part B only)
```

## Adding resumes

`resumes/` starts empty. Either:

- **Test with the samples:** copy the fictional resumes from `sample_resumes/` into it:

  ```bash
  cp sample_resumes/* resumes/
  ```

- **Use your own:** drop your resume files (`.pdf`, `.docx`, `.txt`, `.md`) into `resumes/`.

Anything you put in `resumes/` is git-ignored, so your documents are never committed.

## Layout

| File | Responsibility |
|---|---|
| `llm_file_assistant.py` | Gemini assistant: tool schemas, tool-calling loop, CLI |
| `fs_tools.py` | The four tools: `read_file`, `list_files`, `write_file`, `search_in_file` |
| `parsers.py` | Text extraction per file type; add a type by adding a function + a `PARSERS` entry |
| `sandbox.py` | Confines every path to `FS_ROOT` (default: current directory) |
| `errors.py` | `ToolError` and `error_response` (the standard failure shape) |
| `utils.py` | Small generic helpers |
| `resumes/` | **Input:** put the resumes to work on here (contents are git-ignored) |
| `summaries/` | **Output:** files the assistant writes (contents are git-ignored) |
| `sample_resumes/` | Fictional sample resumes (`.pdf`, `.txt`, `.docx`) to copy into `resumes/` for testing |
| `tests/` | pytest suite (no network needed) |

## The tools

Every tool returns JSON-serialisable data and **never raises**. Failures look like
`{"success": False, "error": "..."}` (for `list_files`, a one-item list containing that dict).

```python
from fs_tools import read_file, list_files, write_file, search_in_file

list_files("resumes", ".pdf")
# [{'name': 'resume_john_doe.pdf', 'path': '...', 'size_bytes': 1417, 'modified': '2026-10-08T06:38:59+00:00'}]

read_file("resumes/resume_john_doe.pdf")
# {'success': True, 'filename': ..., 'file_type': 'pdf', 'content': '...', 'truncated': False,
#  'metadata': {'size_bytes': ..., 'modified': ..., 'char_count': ..., 'word_count': ..., 'page_count': 1}}

search_in_file("resumes/resume_john_doe.pdf", "python")  # case-insensitive
# {'success': True, 'match_count': 3, 'matches': [{'line_number': 7, 'match': 'Python', 'context': '...'}], ...}

write_file("summaries/john_doe.txt", "Summary ...")  # creates directories; .txt/.md/.json/.csv only
# {'success': True, 'filepath': '...', 'bytes_written': 11, 'overwrote': False}
```

Design notes:
- **Sandbox:** paths outside `FS_ROOT` (e.g. `../x`, `/etc/passwd`) are refused, so a model can't roam the disk.
- **Output limits:** `read_file` truncates at 20,000 characters (`truncated: True`); `search_in_file` returns at most 50 matches (`match_count` is still the full total).
- **Writes** are atomic (temp file + rename) and limited to text formats, so the model can't produce fake PDFs/DOCX.
- **Scanned PDFs** have no text layer; `read_file` succeeds with a `warning` rather than doing OCR.
- **Privacy:** `resumes/` and `summaries/` exist in the repo only as empty folders (`.gitkeep`); everything inside them is git-ignored, so real resumes and generated summaries are never committed.

## The assistant (Part B)

```bash
.venv/bin/python llm_file_assistant.py                                  # interactive chat
.venv/bin/python llm_file_assistant.py "Find resumes mentioning Python experience"
```

Optional environment variables (in `.env`): `GEMINI_MODEL` (default `gemini-flash-latest`) and `FS_ROOT`.

Example queries, with the tool calls the model chose (shown on stderr):

| Query | Tool calls |
|---|---|
| "Read all resumes in the resumes folder" | `list_files` -> `read_file` x 3 |
| "Find resumes mentioning Python experience" | `list_files` -> `search_in_file` x 3 |
| "Create a summary file for resume_john_doe.pdf" | `list_files` -> `read_file` -> `write_file` (`summaries/resume_john_doe_summary.txt`) |

How it works:
- Each tool has a hand-written `FunctionDeclaration` (name, description, JSON schema) that the model sees; `TOOL_REGISTRY` maps names to the real functions.
- `run_agent` is an explicit loop: send the conversation -> if the model asks for tools, run them and send the results back -> repeat until it answers in text. Automatic function calling is switched off so each step is visible and capped (`MAX_STEPS = 10`).
- Tool errors (bad arguments, sandbox violations) go back to the model as ordinary results, so it can recover.
- API errors 429/5xx are retried; for 429 the wait follows the server's `retryDelay` hint (the free tier allows ~5 requests/minute per model, and every tool step is one request, so expect pauses on multi-step queries).
- Conversation history is kept across turns in interactive mode.
- Tests use a scripted fake client, so the suite needs no API key or network.

## Development

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```
