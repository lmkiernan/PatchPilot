"""
Validates a candidate unified diff before it is applied.

Checks are purely static — the diff is never written to disk here.
The verifier is responsible for actually applying it.
"""

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")

# Files that must never be patched regardless of constraints.
_FORBIDDEN_RE = [
    re.compile(r"(^|/)\.env(\b|$)"),        # .env, .env.local, .env.production
    re.compile(r"\.lock$"),                  # poetry.lock, package-lock.json …
    re.compile(r"(^|/)\.github/"),           # Actions, CODEOWNERS
    re.compile(r"(^|/)\.circleci/"),
    re.compile(r"(^|/)\.travis\.yml$"),
    re.compile(r"(^|/)\.gitlab-ci\.yml$"),
]


@dataclass
class ValidationResult:
    ok: bool
    violations: list[str] = field(default_factory=list)


def validate_patch(
    diff: str,
    packet: dict,
    project_root: Path,
    *,
    max_diff_lines: int = 80,
) -> ValidationResult:
    """
    Run all static safety checks against a candidate diff.
    Returns a ValidationResult with ok=True only when every check passes.
    """
    violations: list[str] = []

    # ── 0. Must be a git-style diff ────────────────────────────────────────────
    if not diff.lstrip().startswith("diff --git "):
        return ValidationResult(
            ok=False,
            violations=["diff must start with 'diff --git' (plain unified diffs are not accepted)"],
        )

    # ── 0b. No new-file or deleted-file hunks ─────────────────────────────────
    if "/dev/null" in diff:
        return ValidationResult(
            ok=False,
            violations=["diff creates or deletes files (only in-place edits are allowed in Phase 1)"],
        )

    changed_files, file_hunks = _parse_diff(diff)

    if not changed_files:
        return ValidationResult(ok=False, violations=["diff is empty or unparseable"])

    constraints = packet.get("constraints", {})
    target_file = packet.get("target", {}).get("file", "")
    project_root_resolved = project_root.resolve()

    # ── 1. max files changed ───────────────────────────────────────────────────
    max_files = constraints.get("max_files_changed", 1)
    if len(changed_files) > max_files:
        violations.append(
            f"touches {len(changed_files)} file(s); constraint allows {max_files}"
        )

    # ── 2. max diff lines (added + removed, not context) ──────────────────────
    delta_lines = [
        ln
        for ln in diff.splitlines()
        if (ln.startswith("+") or ln.startswith("-"))
        and not ln.startswith("+++")
        and not ln.startswith("---")
    ]
    if len(delta_lines) > max_diff_lines:
        violations.append(
            f"diff has {len(delta_lines)} changed lines; limit is {max_diff_lines}"
        )

    # ── 3. forbidden files ─────────────────────────────────────────────────────
    for f in changed_files:
        if any(pat.search(f) for pat in _FORBIDDEN_RE):
            violations.append(f"modifies forbidden file: {f}")

    # ── 4. no test files when allow_test_edits=False or touch_source_file_only ─
    block_test_edits = (
        not constraints.get("allow_test_edits", True)
        or constraints.get("touch_source_file_only", False)
    )
    if block_test_edits:
        for f in changed_files:
            if _is_test_file(f):
                violations.append(
                    f"modifies test file '{f}' but test edits are not allowed"
                )

    # ── 5a. allowed_files whitelist ────────────────────────────────────────────
    allowed = constraints.get("allowed_files", [])
    if allowed:
        for f in changed_files:
            if not any(_paths_match(f, a) for a in allowed):
                violations.append(f"'{f}' is not in the allowed_files list")

    # ── 5b. must touch the localized target file ───────────────────────────────
    if target_file and not any(_paths_match(f, target_file) for f in changed_files):
        violations.append(f"does not touch the target file: {target_file}")

    # ── 6. path traversal + Python syntax check ────────────────────────────────
    for f in changed_files:
        # Reject paths that escape the project root.
        try:
            resolved = (project_root / f).resolve()
            resolved.relative_to(project_root_resolved)
        except ValueError:
            violations.append(f"path traversal detected: '{f}'")
            continue

        if not f.endswith(".py"):
            continue

        patched = _apply_hunks(f, file_hunks.get(f, []), project_root)
        if patched is None:
            violations.append(f"could not reconstruct patched content for '{f}'")
            continue
        if isinstance(patched, str) and patched.startswith("__hunk_mismatch__:"):
            violations.append(patched[len("__hunk_mismatch__:"):])
            continue
        try:
            ast.parse(patched)
        except SyntaxError as exc:
            violations.append(f"'{f}' has a syntax error after patching: {exc}")

    return ValidationResult(ok=not violations, violations=violations)


# ── Diff parsing ───────────────────────────────────────────────────────────────


def _parse_diff(diff: str) -> tuple[list[str], dict[str, list[dict]]]:
    """Return (ordered changed_files, hunks_by_file)."""
    changed_files: list[str] = []
    file_hunks: dict[str, list[dict]] = {}
    current_file: str | None = None
    current_hunk: dict | None = None

    for line in diff.splitlines():
        if line.startswith("+++ "):
            raw = line[4:].strip()
            path = raw[2:] if raw.startswith("b/") else raw
            current_file = path
            if current_file not in changed_files:
                changed_files.append(current_file)
            file_hunks.setdefault(current_file, [])
            current_hunk = None
        elif line.startswith("@@ ") and current_file is not None:
            m = _HUNK_RE.match(line)
            if m:
                current_hunk = {"old_start": int(m.group(1)), "lines": []}
                file_hunks[current_file].append(current_hunk)
        elif current_hunk is not None and line and line[0] in (" ", "+", "-"):
            current_hunk["lines"].append(line)

    return changed_files, file_hunks


def _apply_hunks(file_path: str, hunks: list[dict], project_root: Path) -> str | None:
    """
    Reconstruct post-patch file content without touching disk.
    Returns a sentinel string starting with '__hunk_mismatch__:' when context
    lines in the diff do not match the actual source, so the caller can surface
    a precise violation rather than a generic "could not reconstruct" message.
    """
    source = project_root / file_path
    if not source.exists():
        return None
    lines = source.read_text().splitlines()

    offset = 0
    for hunk in hunks:
        idx = hunk["old_start"] - 1 + offset  # 1-based → 0-based + running offset
        removed: list[str] = []
        added: list[str] = []
        for raw in hunk["lines"]:
            ch, content = raw[0], raw[1:]
            if ch == " ":
                removed.append(content)
                added.append(content)
            elif ch == "-":
                removed.append(content)
            elif ch == "+":
                added.append(content)

        actual = lines[idx : idx + len(removed)]
        if actual != removed:
            return (
                f"__hunk_mismatch__:hunk at line {hunk['old_start']} of '{file_path}' "
                f"does not match source — patch may be stale"
            )

        lines[idx : idx + len(removed)] = added
        offset += len(added) - len(removed)

    return "\n".join(lines)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _is_test_file(path: str) -> bool:
    normalized = path.replace("\\", "/")
    name = Path(path).name
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or "/tests/" in normalized
        or normalized.startswith("tests/")
    )


def _paths_match(diff_path: str, target: str) -> bool:
    """Loose match: handles leading repo-relative prefixes on either side."""
    return diff_path == target or diff_path.endswith(target) or target.endswith(diff_path)
