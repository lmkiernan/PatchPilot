"""
Applies and reverts candidate diffs using git apply.

This module only handles patch application and rollback.
The caller is responsible for running validate_patch() before calling apply_patch().
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ApplyResult:
    ok: bool
    stdout: str
    stderr: str


def check_worktree_clean(project_root: Path) -> ApplyResult:
    """
    Return ok=True when the working tree has no uncommitted changes.
    Also serves as an implicit check that project_root is inside a git repo.
    """
    project_root = project_root.resolve()
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=project_root,
        )
        if proc.returncode != 0:
            return ApplyResult(
                ok=False,
                stdout=proc.stdout,
                stderr=proc.stderr or "git status failed — is this a git repository?",
            )
        dirty = proc.stdout.strip()
        if dirty:
            return ApplyResult(
                ok=False,
                stdout=proc.stdout,
                stderr=f"working tree is dirty — commit or stash changes before applying a patch:\n{dirty}",
            )
        return ApplyResult(ok=True, stdout="", stderr="")
    except FileNotFoundError:
        return ApplyResult(ok=False, stdout="", stderr="git executable not found in PATH")


def check_patch(diff: str, project_root: Path) -> ApplyResult:
    """Dry-run: verify the patch would apply cleanly without modifying any files."""
    project_root = project_root.resolve()
    return _git_apply(diff, project_root, extra_args=["--check"])


def apply_patch(diff: str, project_root: Path) -> ApplyResult:
    """
    Apply a unified diff to the working tree.
    Requires a clean working tree, then runs git apply --check before the real apply.
    """
    project_root = project_root.resolve()
    clean = check_worktree_clean(project_root)
    if not clean.ok:
        return clean
    preflight = _git_apply(diff, project_root, extra_args=["--check"])
    if not preflight.ok:
        return preflight
    return _git_apply(diff, project_root)


def revert_patch(diff: str, project_root: Path) -> ApplyResult:
    """
    Reverse a previously applied patch.
    Only call this after apply_patch() succeeded.
    """
    project_root = project_root.resolve()
    return _git_apply(diff, project_root, extra_args=["--reverse"])


def changed_files(project_root: Path) -> list[str]:
    """Return paths of files modified in the working tree relative to HEAD."""
    project_root = project_root.resolve()
    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True,
            text=True,
            cwd=project_root,
        )
        if proc.returncode != 0:
            return []
        return [line for line in proc.stdout.splitlines() if line]
    except FileNotFoundError:
        return []


# ── Internal ───────────────────────────────────────────────────────────────────


def _git_apply(diff: str, project_root: Path, extra_args: list[str] | None = None) -> ApplyResult:
    """Run 'git apply [extra_args] -' with the diff on stdin."""
    cmd = ["git", "apply"] + (extra_args or []) + ["-"]
    try:
        proc = subprocess.run(
            cmd,
            input=diff,
            capture_output=True,
            text=True,
            cwd=project_root,
        )
        return ApplyResult(ok=proc.returncode == 0, stdout=proc.stdout, stderr=proc.stderr)
    except FileNotFoundError:
        return ApplyResult(ok=False, stdout="", stderr="git executable not found in PATH")
