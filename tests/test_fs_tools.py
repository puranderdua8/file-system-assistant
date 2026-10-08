import fs_tools as fs


# ---------- read_file ----------
def test_read_txt(resumes):
    r = fs.read_file("resumes/alice.txt")
    assert r["success"] and r["file_type"] == "txt"
    assert "Alice Smith" in r["content"]
    assert r["metadata"]["word_count"] > 3 and "modified" in r["metadata"]


def test_read_docx_includes_tables(resumes):
    r = fs.read_file("resumes/bob.docx")
    assert r["success"]
    assert "Bob Jones" in r["content"] and "Java, Go" in r["content"]


def test_read_pdf(resumes):
    r = fs.read_file("resumes/carol.PDF")
    assert r["success"], r
    assert "Data scientist" in r["content"]
    assert r["metadata"]["page_count"] == 1


def test_read_missing_and_unsupported_and_dir(resumes):
    assert not fs.read_file("resumes/nope.txt")["success"]
    assert "Unsupported" in fs.read_file("resumes/notes.csv")["error"]
    assert not fs.read_file("resumes")["success"]


def test_read_corrupt_pdf_does_not_raise(resumes):
    (resumes / "bad.pdf").write_bytes(b"not a pdf")
    assert fs.read_file("resumes/bad.pdf")["success"] is False


def test_read_truncates_and_warns_on_empty(resumes):
    (resumes / "big.txt").write_text("x" * (fs.MAX_CONTENT_CHARS + 10))
    (resumes / "empty.txt").write_text("")
    big = fs.read_file("resumes/big.txt")
    assert big["truncated"] and len(big["content"]) == fs.MAX_CONTENT_CHARS
    assert "warning" in fs.read_file("resumes/empty.txt")


def test_latin1_fallback(resumes):
    (resumes / "l1.txt").write_bytes("Café".encode("latin-1"))
    assert fs.read_file("resumes/l1.txt")["content"] == "Café"


# ---------- list_files ----------
def test_list_all_sorted(resumes):
    names = [f["name"] for f in fs.list_files("resumes")]
    assert names == ["alice.txt", "bob.docx", "carol.PDF", "notes.csv"]
    assert {"size_bytes", "modified", "path"} <= set(fs.list_files("resumes")[0])


def test_list_extension_filter_variants(resumes):
    for ext in ("pdf", ".pdf", ".PDF"):
        assert [f["name"] for f in fs.list_files("resumes", ext)] == ["carol.PDF"]


def test_list_skips_subdirs_and_handles_missing(resumes):
    (resumes / "sub").mkdir()
    assert "sub" not in [f["name"] for f in fs.list_files("resumes")]
    res = fs.list_files("missing")
    assert res[0]["success"] is False


# ---------- write_file ----------
def test_write_creates_dirs_and_overwrites(root):
    r = fs.write_file("a/b/out.txt", "hello")
    assert r["success"] and r["overwrote"] is False
    assert (root / "a/b/out.txt").read_text() == "hello"
    r2 = fs.write_file("a/b/out.txt", "bye")
    assert r2["overwrote"] and (root / "a/b/out.txt").read_text() == "bye"
    assert not list((root / "a/b").glob(".tmp-*"))


def test_write_rejects_bad_extension_and_dir(root):
    assert not fs.write_file("x.pdf", "fake")["success"]
    (root / "d.txt").mkdir()
    assert not fs.write_file("d.txt", "x")["success"]


# ---------- search_in_file ----------
def test_search_case_insensitive_with_context(resumes):
    r = fs.search_in_file("resumes/alice.txt", "PYTHON")
    assert r["success"] and r["match_count"] == 2
    assert {m["match"] for m in r["matches"]} == {"Python", "python"}
    assert "Skills" in r["matches"][0]["context"] and r["matches"][0]["line_number"] == 3


def test_search_pdf_docx_and_no_hits(resumes):
    assert fs.search_in_file("resumes/carol.PDF", "python")["match_count"] == 1
    assert fs.search_in_file("resumes/bob.docx", "kotlin")["match_count"] == 1
    r = fs.search_in_file("resumes/alice.txt", "rust")
    assert r["success"] and r["match_count"] == 0 and r["matches"] == []


def test_search_edges(resumes):
    assert not fs.search_in_file("resumes/alice.txt", "  ")["success"]
    assert not fs.search_in_file("resumes/missing.txt", "x")["success"]
    (resumes / "many.txt").write_text("a " * (fs.MAX_MATCHES + 5))
    r = fs.search_in_file("resumes/many.txt", "a")
    assert r["match_count"] == fs.MAX_MATCHES + 5 and r["truncated"]
    assert len(r["matches"]) == fs.MAX_MATCHES


def test_search_regex_chars_are_literal(resumes):
    (resumes / "c.txt").write_text("I know C++ and .NET")
    assert fs.search_in_file("resumes/c.txt", "C++")["match_count"] == 1


# ---------- sandbox ----------
def test_sandbox_blocks_escape(root):
    for call in (
        lambda: fs.read_file("../etc/passwd"),
        lambda: fs.read_file("/etc/passwd"),
        lambda: fs.write_file("../evil.txt", "x"),
        lambda: fs.search_in_file("/etc/passwd", "root"),
    ):
        r = call()
        assert r["success"] is False and "outside" in r["error"]
    assert fs.list_files("..")[0]["success"] is False


# ---------- encrypted PDFs ----------
def _encrypt(src, dst, user_password):
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(str(src)))
    writer.encrypt(user_password=user_password, owner_password="owner")
    with open(dst, "wb") as fh:
        writer.write(fh)


def test_pdf_encrypted_with_empty_password_is_readable(resumes):
    _encrypt(resumes / "carol.PDF", resumes / "locked-empty.pdf", "")
    r = fs.read_file("resumes/locked-empty.pdf")
    assert r["success"], r
    assert "Data scientist" in r["content"]


def test_pdf_with_real_password_is_rejected(resumes):
    _encrypt(resumes / "carol.PDF", resumes / "locked.pdf", "secret")
    r = fs.read_file("resumes/locked.pdf")
    assert r["success"] is False and "password" in r["error"]
