# File System Assistant

Sandboxed file-system tools for working with resume files (PDF, DOCX, TXT/MD),
designed to be exposed to an LLM through function calling.

> Status: Part A (the tools) is complete. The LLM integration (`llm_file_assistant.py`) is next.

## Setup

Requires **Python 3.10+** (the dependencies need it; macOS's system `python3` is older, so use the venv).

```bash
python3 -m venv .venv           # must be Python >= 3.10: check with `python3 --version`
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env           # then add your GEMINI_API_KEY (needed for Part B only)
```

## Layout

| File | Responsibility |
|---|---|
| `fs_tools.py` | The four tools: `read_file`, `list_files`, `write_file`, `search_in_file` |
| `parsers.py` | Text extraction per file type; add a type by adding a function + a `PARSERS` entry |
| `sandbox.py` | Confines every path to `FS_ROOT` (default: current directory) |
| `errors.py` | `ToolError` and `error_response` (the standard failure shape) |
| `utils.py` | Small generic helpers |
| `resumes/` | Fictional sample resumes (`.pdf`, `.txt`, `.docx`) |
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
- **Real resumes:** put them in `resumes/private/` (git-ignored) to keep personal data out of the repo.

## Development

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```
