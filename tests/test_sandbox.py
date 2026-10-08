import pytest

import fs_tools as fs
import sandbox
from errors import ToolError


@pytest.fixture
def no_root(monkeypatch):
    monkeypatch.delenv("FS_ROOT", raising=False)


def test_unset_means_no_sandbox(no_root):
    assert sandbox.get_root() is None


def test_empty_value_counts_as_unset(monkeypatch):
    monkeypatch.setenv("FS_ROOT", "")
    assert sandbox.get_root() is None


def test_unrestricted_direct_calls_accept_any_absolute_path(no_root, tmp_path):
    f = tmp_path / "cv.txt"
    f.write_text("Direct call outside any sandbox")
    assert fs.read_file(str(f))["success"]
    assert fs.write_file(str(tmp_path / "out" / "x.txt"), "ok")["success"]
    assert [i["name"] for i in fs.list_files(str(tmp_path))][0] == "cv.txt"


def test_unrestricted_relative_paths_use_cwd(no_root, tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("hi")
    monkeypatch.chdir(tmp_path)
    assert fs.read_file("a.txt")["content"] == "hi"


def test_root_set_anchors_relative_paths_and_blocks_escape(root):
    assert sandbox.get_root() == root.resolve()
    assert sandbox.resolve_path("x/y.txt") == root.resolve() / "x" / "y.txt"
    with pytest.raises(ToolError, match="outside"):
        sandbox.resolve_path("../elsewhere.txt")
    with pytest.raises(ToolError, match="outside"):
        sandbox.resolve_path("/etc/passwd")
