"""The comment ratchet: no file may gain comment lines (checks/comment_lint.py)."""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LINT = os.path.join(ROOT, "checks", "comment_lint.py")


def _run(*args):
    return subprocess.run([sys.executable, LINT, *args], cwd=ROOT, capture_output=True, text=True)


def test_controls():
    r = _run("selftest")
    assert r.returncode == 0, r.stdout


def test_no_file_gained_comments():
    r = _run("check", "--root", ROOT)
    assert r.returncode == 0, r.stdout


def test_a_new_comment_is_refused(tmp_path):
    for name, text in (("a.py", "x = 1  # one\n"), ("comment-lint.json", "{}"), ("comment-lint.baseline.json", '{"a.py": 1}')):
        (tmp_path / name).write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert _run("check", "--root", str(tmp_path)).returncode == 0
    (tmp_path / "a.py").write_text("x = 1  # one\n# two\n", encoding="utf-8")
    assert _run("check", "--root", str(tmp_path)).returncode == 1
