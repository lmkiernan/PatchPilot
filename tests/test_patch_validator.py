"""Tests for patch_validator.py — all run without network access."""

from pathlib import Path

import pytest

from patchpilot.patch_validator import ValidationResult, _is_test_file, validate_patch
from tests.conftest import MOCK_DIFF

PRICING_ROOT = Path("examples/broken_pricing_repo")


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_diff(file_path: str, old_line: str, new_line: str) -> str:
    return (
        f"diff --git a/{file_path} b/{file_path}\n"
        f"--- a/{file_path}\n"
        f"+++ b/{file_path}\n"
        "@@ -1,1 +1,1 @@\n"
        f"-{old_line}\n"
        f"+{new_line}\n"
    )


# ── Happy path ─────────────────────────────────────────────────────────────────


def test_valid_patch_passes(sample_packet):
    result = validate_patch(MOCK_DIFF, sample_packet, PRICING_ROOT)
    assert result.ok
    assert result.violations == []


def test_returns_validation_result_type(sample_packet):
    assert isinstance(validate_patch(MOCK_DIFF, sample_packet, PRICING_ROOT), ValidationResult)


# ── Rule 0: must be git-style diff ────────────────────────────────────────────


def test_plain_unified_diff_rejected(sample_packet):
    plain = (
        "--- a/src/pricing/calc.py\n"
        "+++ b/src/pricing/calc.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def compute_discount(price, discount):\n"
        "-    return price * discount.percent\n"
        "+    return price if discount is None else price * discount.percent\n"
    )
    result = validate_patch(plain, sample_packet, PRICING_ROOT)
    assert not result.ok
    assert any("diff --git" in v for v in result.violations)


def test_empty_diff_rejected(sample_packet):
    result = validate_patch("", sample_packet, PRICING_ROOT)
    assert not result.ok


def test_garbage_string_rejected(sample_packet):
    result = validate_patch("not a diff at all", sample_packet, PRICING_ROOT)
    assert not result.ok


# ── Rule 0b: no /dev/null (new/deleted files) ─────────────────────────────────


def test_new_file_diff_rejected(sample_packet):
    new_file = (
        "diff --git a/src/pricing/new.py b/src/pricing/new.py\n"
        "--- /dev/null\n"
        "+++ b/src/pricing/new.py\n"
        "@@ -0,0 +1,1 @@\n"
        "+x = 1\n"
    )
    packet = {
        **sample_packet,
        "constraints": {**sample_packet["constraints"], "allowed_files": []},
        "target": {**sample_packet["target"], "file": "src/pricing/new.py"},
    }
    result = validate_patch(new_file, packet, PRICING_ROOT)
    assert not result.ok
    assert any("delete" in v or "creat" in v for v in result.violations)


def test_deleted_file_diff_rejected(sample_packet):
    deleted = (
        "diff --git a/src/pricing/calc.py b/src/pricing/calc.py\n"
        "--- a/src/pricing/calc.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-def compute_discount(price, discount):\n"
        "-    return price * discount.percent\n"
    )
    result = validate_patch(deleted, sample_packet, PRICING_ROOT)
    assert not result.ok
    assert any("delete" in v or "creat" in v for v in result.violations)


# ── Rule 1: max files changed ──────────────────────────────────────────────────


def test_too_many_files_fails(sample_packet, tmp_path):
    (tmp_path / "src" / "pricing").mkdir(parents=True)
    (tmp_path / "src" / "pricing" / "calc.py").write_text(
        "def compute_discount(price, discount):\n    return price * discount.percent\n"
    )
    (tmp_path / "src" / "pricing" / "other.py").write_text("x = 1\n")
    extra = _make_diff("src/pricing/other.py", "x = 1", "x = 2")
    two_file_diff = MOCK_DIFF + "\n" + extra
    packet = {
        **sample_packet,
        "constraints": {
            **sample_packet["constraints"],
            "max_files_changed": 1,
            "allowed_files": [],
        },
    }
    result = validate_patch(two_file_diff, packet, tmp_path)
    assert not result.ok
    assert any("file" in v for v in result.violations)


# ── Rule 2: max diff lines ─────────────────────────────────────────────────────


def test_diff_within_line_limit(sample_packet):
    result = validate_patch(MOCK_DIFF, sample_packet, PRICING_ROOT, max_diff_lines=80)
    assert result.ok


def test_diff_exceeds_line_limit(sample_packet):
    # Append fake added lines to the diff body to exceed the limit.
    bloated = MOCK_DIFF + "\n" + "\n".join(f"+# filler {i}" for i in range(100))
    result = validate_patch(bloated, sample_packet, PRICING_ROOT, max_diff_lines=5)
    assert not result.ok
    assert any("limit" in v for v in result.violations)


# ── Rule 3: forbidden files ────────────────────────────────────────────────────


def test_env_file_blocked(sample_packet):
    env_diff = _make_diff(".env", "SECRET=old", "SECRET=new")
    packet = {
        **sample_packet,
        "constraints": {
            **sample_packet["constraints"],
            "max_files_changed": 2,
            "allowed_files": [],
        },
        "target": {**sample_packet["target"], "file": ".env"},
    }
    result = validate_patch(env_diff, packet, PRICING_ROOT)
    assert not result.ok
    assert any("forbidden" in v for v in result.violations)


def test_lock_file_blocked(sample_packet):
    lock_diff = _make_diff("poetry.lock", "old", "new")
    packet = {
        **sample_packet,
        "constraints": {
            **sample_packet["constraints"],
            "max_files_changed": 2,
            "allowed_files": [],
        },
        "target": {**sample_packet["target"], "file": "poetry.lock"},
    }
    result = validate_patch(lock_diff, packet, PRICING_ROOT)
    assert not result.ok
    assert any("forbidden" in v for v in result.violations)


def test_github_actions_blocked(sample_packet):
    ci_diff = _make_diff(".github/workflows/ci.yml", "old: 1", "new: 2")
    packet = {
        **sample_packet,
        "constraints": {
            **sample_packet["constraints"],
            "max_files_changed": 2,
            "allowed_files": [],
        },
        "target": {**sample_packet["target"], "file": ".github/workflows/ci.yml"},
    }
    result = validate_patch(ci_diff, packet, PRICING_ROOT)
    assert not result.ok
    assert any("forbidden" in v for v in result.violations)


# ── Rule 4: test file edits ────────────────────────────────────────────────────


def test_test_file_blocked_by_touch_source_only(sample_packet):
    test_diff = _make_diff("tests/test_pricing.py", "# old", "# new")
    packet = {
        **sample_packet,
        "constraints": {
            **sample_packet["constraints"],
            "touch_source_file_only": True,
            "allow_test_edits": True,  # touch_source_file_only wins
            "max_files_changed": 2,
            "allowed_files": [],
        },
        "target": {**sample_packet["target"], "file": "tests/test_pricing.py"},
    }
    result = validate_patch(test_diff, packet, PRICING_ROOT)
    assert not result.ok
    assert any("test" in v for v in result.violations)


def test_test_file_blocked_by_allow_test_edits_false(sample_packet):
    test_diff = _make_diff("tests/test_pricing.py", "# old", "# new")
    packet = {
        **sample_packet,
        "constraints": {
            **sample_packet["constraints"],
            "touch_source_file_only": False,
            "allow_test_edits": False,  # this alone should block test edits
            "max_files_changed": 2,
            "allowed_files": [],
        },
        "target": {**sample_packet["target"], "file": "tests/test_pricing.py"},
    }
    result = validate_patch(test_diff, packet, PRICING_ROOT)
    assert not result.ok
    assert any("test" in v for v in result.violations)


def test_test_file_allowed_when_both_flags_permit(sample_packet, tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_pricing.py").write_text("# old\n")
    test_diff = _make_diff("tests/test_pricing.py", "# old", "# new")
    packet = {
        **sample_packet,
        "constraints": {
            **sample_packet["constraints"],
            "touch_source_file_only": False,
            "allow_test_edits": True,
            "max_files_changed": 2,
            "allowed_files": [],
        },
        "target": {**sample_packet["target"], "file": "tests/test_pricing.py"},
    }
    result = validate_patch(test_diff, packet, tmp_path)
    assert result.ok


# ── Rule 5a: allowed_files whitelist ──────────────────────────────────────────


def test_file_not_in_allowed_list_fails(sample_packet, tmp_path):
    (tmp_path / "src" / "pricing").mkdir(parents=True)
    (tmp_path / "src" / "pricing" / "calc.py").write_text(
        "def compute_discount(price, discount):\n    return price * discount.percent\n"
    )
    (tmp_path / "src" / "pricing" / "utils.py").write_text("x = 1\n")
    sneaky = _make_diff("src/pricing/utils.py", "x = 1", "x = 2")
    two_file_diff = MOCK_DIFF + "\n" + sneaky
    packet = {
        **sample_packet,
        "constraints": {
            **sample_packet["constraints"],
            "max_files_changed": 2,
            "allowed_files": ["src/pricing/calc.py"],
        },
    }
    result = validate_patch(two_file_diff, packet, tmp_path)
    assert not result.ok
    assert any("allowed_files" in v for v in result.violations)


# ── Rule 5b: must touch target file ───────────────────────────────────────────


def test_target_file_not_touched_fails(sample_packet, tmp_path):
    (tmp_path / "src" / "pricing").mkdir(parents=True)
    (tmp_path / "src" / "pricing" / "utils.py").write_text("x = 1\n")
    unrelated = _make_diff("src/pricing/utils.py", "x = 1", "x = 2")
    packet = {
        **sample_packet,
        "constraints": {**sample_packet["constraints"], "allowed_files": []},
    }
    result = validate_patch(unrelated, packet, tmp_path)
    assert not result.ok
    assert any("target" in v for v in result.violations)


# ── Rule 6a: path traversal ────────────────────────────────────────────────────


def test_path_traversal_rejected(sample_packet, tmp_path):
    traversal_diff = (
        "diff --git a/../../etc/passwd b/../../etc/passwd\n"
        "--- a/../../etc/passwd\n"
        "+++ b/../../etc/passwd\n"
        "@@ -1,1 +1,1 @@\n"
        "-root:x:0:0\n"
        "+pwned\n"
    )
    packet = {
        **sample_packet,
        "constraints": {**sample_packet["constraints"], "allowed_files": []},
        "target": {**sample_packet["target"], "file": "../../etc/passwd"},
    }
    result = validate_patch(traversal_diff, packet, tmp_path)
    assert not result.ok
    assert any("traversal" in v for v in result.violations)


# ── Rule 6b: Python syntax after patching ─────────────────────────────────────


def test_syntax_error_after_patch_fails(sample_packet, tmp_path):
    (tmp_path / "src" / "pricing").mkdir(parents=True)
    (tmp_path / "src" / "pricing" / "calc.py").write_text(
        "def compute_discount(price, discount):\n    return price * discount.percent\n"
    )
    bad_diff = (
        "diff --git a/src/pricing/calc.py b/src/pricing/calc.py\n"
        "--- a/src/pricing/calc.py\n"
        "+++ b/src/pricing/calc.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def compute_discount(price, discount):\n"
        "-    return price * discount.percent\n"
        "+    return price * discount.percent if\n"
    )
    packet = {
        **sample_packet,
        "constraints": {**sample_packet["constraints"], "allowed_files": []},
    }
    result = validate_patch(bad_diff, packet, tmp_path)
    assert not result.ok
    assert any("syntax" in v for v in result.violations)


def test_valid_patch_no_syntax_error(sample_packet):
    result = validate_patch(MOCK_DIFF, sample_packet, PRICING_ROOT)
    assert result.ok
    assert not any("syntax" in v for v in result.violations)


# ── Rule 6c: context line mismatch (stale patch) ──────────────────────────────


def test_stale_patch_context_mismatch(sample_packet, tmp_path):
    # Source has been changed since the diff was generated.
    (tmp_path / "src" / "pricing").mkdir(parents=True)
    (tmp_path / "src" / "pricing" / "calc.py").write_text(
        "def compute_discount(price, pct):\n    return price * pct\n"
    )
    # Diff still references the old parameter name.
    stale_diff = (
        "diff --git a/src/pricing/calc.py b/src/pricing/calc.py\n"
        "--- a/src/pricing/calc.py\n"
        "+++ b/src/pricing/calc.py\n"
        "@@ -1,2 +1,3 @@\n"
        " def compute_discount(price, discount):\n"  # context won't match
        "-    return price * discount.percent\n"
        "+    if discount is None:\n"
        "+        return price\n"
        "+    return price * discount.percent\n"
    )
    packet = {
        **sample_packet,
        "constraints": {**sample_packet["constraints"], "allowed_files": []},
    }
    result = validate_patch(stale_diff, packet, tmp_path)
    assert not result.ok
    assert any("stale" in v or "mismatch" in v for v in result.violations)


# ── _is_test_file helper ───────────────────────────────────────────────────────


@pytest.mark.parametrize("path,expected", [
    ("tests/test_pricing.py", True),        # starts with tests/
    ("src/tests/test_foo.py", True),        # /tests/ in middle
    ("test_bar.py", True),                  # test_ prefix
    ("bar_test.py", True),                  # _test suffix
    ("src/pricing/calc.py", False),         # normal source file
    ("src/testing_utils.py", False),        # "testing" in name, not a test file
])
def test_is_test_file(path, expected):
    assert _is_test_file(path) == expected
