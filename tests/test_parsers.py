import pytest

import parsers
from errors import ToolError


def test_supported_extensions_derived_from_registry():
    assert parsers.SUPPORTED_EXTENSIONS == frozenset(parsers.PARSERS)
    assert {".txt", ".md", ".pdf", ".docx"} == set(parsers.PARSERS)


def test_extract_text_dispatches_by_extension(resumes):
    text, meta = parsers.extract_text(resumes / "carol.PDF")
    assert "Data scientist" in text and meta["page_count"] == 1
    assert "Alice" in parsers.extract_text(resumes / "alice.txt")[0]


def test_extract_text_rejects_unknown_missing_and_dir(resumes):
    with pytest.raises(ToolError, match="Unsupported"):
        parsers.extract_text(resumes / "notes.csv")
    with pytest.raises(ToolError, match="not found"):
        parsers.extract_text(resumes / "nope.txt")
    with pytest.raises(ToolError, match="Not a file"):
        parsers.extract_text(resumes)


def test_new_parser_only_needs_a_registry_entry(resumes, monkeypatch):
    monkeypatch.setitem(parsers.PARSERS, ".csv", lambda p: (p.read_text().upper(), {}))
    assert parsers.extract_text(resumes / "notes.csv")[0] == "A,B\n"
