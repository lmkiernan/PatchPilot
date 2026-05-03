"""Tests for patch_apply.py — use a real git repo in tmp_path."""

from pathlib import Path

import pytest

from patchpilot.patch_apply import (
    ApplyResult,
    apply_patch,
    changed_files,
    check_patch,
    check_worktree_clean,
    revert_patch,
)

ORIGINAL = "def compute_discount(price, discount):\n    return price * discount.percent\n"
PATCHED = "def compute_discount(price, discount):\n    return price * discount.percent if discount is not None else price\n"

VALID_DIFF = """\
diff --git a/src/pricing/calc.py b/src/pricing/calc.py
--- a/src/pricing/calc.py
+++ b/src/pricing/calc.py
@@ -1,2 +1,2 @@
 def compute_discount(price, discount):
-    return price * discount.percent
+    return price * discount.percent if discount is not None else price
"""


def _calc(repo: Path) -> Path:
    return repo / "src" / "pricing" / "calc.py"


# ── ApplyResult type ───────────────────────────────────────────────────────────


def test_apply_result_is_dataclass(git_repo):
    result = apply_patch(VALID_DIFF, git_repo)
    assert isinstance(result, ApplyResult)
    assert hasattr(result, "ok")
    assert hasattr(result, "stdout")
    assert hasattr(result, "stderr")


# ── check_patch ────────────────────────────────────────────────────────────────


def test_check_patch_succeeds_for_valid_diff(git_repo):
    result = check_patch(VALID_DIFF, git_repo)
    assert result.ok


def test_check_patch_does_not_modify_files(git_repo):
    check_patch(VALID_DIFF, git_repo)
    assert _calc(git_repo).read_text() == ORIGINAL


def test_check_patch_fails_for_stale_diff(git_repo):
    stale = VALID_DIFF.replace(
        "-    return price * discount.percent\n",
        "-    return price * discount.pct\n",  # wrong context line
    )
    result = check_patch(stale, git_repo)
    assert not result.ok
    assert result.stderr  # git explains the mismatch


# ── apply_patch ────────────────────────────────────────────────────────────────


def test_apply_patch_modifies_file(git_repo):
    result = apply_patch(VALID_DIFF, git_repo)
    assert result.ok
    assert _calc(git_repo).read_text() == PATCHED


def test_apply_patch_returns_ok_true_on_success(git_repo):
    result = apply_patch(VALID_DIFF, git_repo)
    assert result.ok


def test_apply_patch_fails_without_modifying_for_stale_diff(git_repo):
    stale = VALID_DIFF.replace(
        "-    return price * discount.percent\n",
        "-    return price * discount.pct\n",
    )
    result = apply_patch(stale, git_repo)
    assert not result.ok
    assert _calc(git_repo).read_text() == ORIGINAL  # file untouched


def test_apply_patch_populates_stderr_on_failure(git_repo):
    bad = "diff --git a/nonexistent.py b/nonexistent.py\n--- a/nonexistent.py\n+++ b/nonexistent.py\n@@ -1 +1 @@\n-old\n+new\n"
    result = apply_patch(bad, git_repo)
    assert not result.ok
    assert result.stderr


# ── revert_patch ───────────────────────────────────────────────────────────────


def test_revert_restores_original(git_repo):
    apply_patch(VALID_DIFF, git_repo)
    result = revert_patch(VALID_DIFF, git_repo)
    assert result.ok
    assert _calc(git_repo).read_text() == ORIGINAL


def test_revert_fails_when_patch_not_applied(git_repo):
    result = revert_patch(VALID_DIFF, git_repo)
    assert not result.ok


def test_apply_then_revert_is_idempotent(git_repo):
    apply_patch(VALID_DIFF, git_repo)
    revert_patch(VALID_DIFF, git_repo)
    apply_patch(VALID_DIFF, git_repo)
    assert _calc(git_repo).read_text() == PATCHED


# ── check_worktree_clean ───────────────────────────────────────────────────────


def test_clean_repo_is_clean(git_repo):
    result = check_worktree_clean(git_repo)
    assert result.ok


def test_dirty_repo_is_not_clean(git_repo):
    _calc(git_repo).write_text("# modified\n")
    result = check_worktree_clean(git_repo)
    assert not result.ok
    assert result.stderr  # describes what's dirty


def test_dirty_repo_message_mentions_stash_or_commit(git_repo):
    _calc(git_repo).write_text("# modified\n")
    result = check_worktree_clean(git_repo)
    assert "dirty" in result.stderr or "stash" in result.stderr or "commit" in result.stderr


def test_not_a_git_repo_fails(tmp_path):
    result = check_worktree_clean(tmp_path)
    assert not result.ok
    assert result.stderr


def test_apply_patch_blocked_on_dirty_worktree(git_repo):
    _calc(git_repo).write_text("# unsaved edit\n")
    result = apply_patch(VALID_DIFF, git_repo)
    assert not result.ok
    assert "dirty" in result.stderr or "stash" in result.stderr or "commit" in result.stderr


# ── changed_files ──────────────────────────────────────────────────────────────


def test_changed_files_empty_on_clean_repo(git_repo):
    assert changed_files(git_repo) == []


def test_changed_files_lists_modified_file_after_apply(git_repo):
    apply_patch(VALID_DIFF, git_repo)
    files = changed_files(git_repo)
    assert "src/pricing/calc.py" in files


def test_changed_files_empty_after_revert(git_repo):
    apply_patch(VALID_DIFF, git_repo)
    revert_patch(VALID_DIFF, git_repo)
    assert changed_files(git_repo) == []
