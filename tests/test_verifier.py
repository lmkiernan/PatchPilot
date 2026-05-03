"""Tests for verifier.py — uses real git apply but mocks run_tests."""

from pathlib import Path
from unittest.mock import patch

from patchpilot.patch_apply import ApplyResult
from patchpilot.runner import RunnerError
from patchpilot.verifier import VerifyResult, verify_patch

ORIGINAL = (
    "def compute_discount(price, discount):\n"
    "    return price * discount.percent\n"
)
PATCHED = (
    "def compute_discount(price, discount):\n"
    "    return price * discount.percent if discount is not None else price\n"
)

VALID_DIFF = """\
diff --git a/src/pricing/calc.py b/src/pricing/calc.py
--- a/src/pricing/calc.py
+++ b/src/pricing/calc.py
@@ -1,2 +1,2 @@
 def compute_discount(price, discount):
-    return price * discount.percent
+    return price * discount.percent if discount is not None else price
"""

# Tuples used as side_effect list items — mock returns them directly.
_PASS = (0, "1 passed", "")
_FAIL = (1, "1 failed", "FAILED test_no_discount")


def _pass(*_args, **_kwargs):
    return _PASS


def _fail(*_args, **_kwargs):
    return _FAIL


def _calc(repo: Path) -> Path:
    return repo / "src" / "pricing" / "calc.py"


# ── Result structure ───────────────────────────────────────────────────────────


def test_returns_verify_result_type(git_repo, sample_packet):
    with patch("patchpilot.verifier.run_tests", side_effect=_pass):
        result = verify_patch(VALID_DIFF, sample_packet, git_repo)
    assert isinstance(result, VerifyResult)


# ── Stage 1: Validation failure ────────────────────────────────────────────────


def test_validation_failure_returns_ok_false(git_repo, sample_packet):
    result = verify_patch("not a diff", sample_packet, git_repo)
    assert not result.ok
    assert result.stage == "validation"


def test_validation_failure_does_not_apply_patch(git_repo, sample_packet):
    verify_patch("not a diff", sample_packet, git_repo)
    assert _calc(git_repo).read_text() == ORIGINAL


def test_validation_failure_populates_violations(git_repo, sample_packet):
    result = verify_patch("not a diff", sample_packet, git_repo)
    assert result.violations


def test_validation_failure_patch_not_applied_flag(git_repo, sample_packet):
    result = verify_patch("not a diff", sample_packet, git_repo)
    assert not result.patch_applied


# ── Stage 2: Apply failure ─────────────────────────────────────────────────────


def test_apply_failure_on_dirty_worktree(git_repo, sample_packet):
    # Append a trailing line so the file is dirty but the diff context still matches.
    _calc(git_repo).write_text(ORIGINAL + "# extra\n")
    result = verify_patch(VALID_DIFF, sample_packet, git_repo)
    assert not result.ok
    assert result.stage == "apply"
    assert not result.patch_applied
    assert result.apply_error


# ── Stage 3: Targeted tests fail ──────────────────────────────────────────────


def test_targeted_fail_returns_ok_false(git_repo, sample_packet):
    with patch("patchpilot.verifier.run_tests", side_effect=_fail):
        result = verify_patch(VALID_DIFF, sample_packet, git_repo)
    assert not result.ok
    assert result.stage == "targeted"


def test_targeted_fail_reverts_patch(git_repo, sample_packet):
    with patch("patchpilot.verifier.run_tests", side_effect=_fail):
        verify_patch(VALID_DIFF, sample_packet, git_repo)
    assert _calc(git_repo).read_text() == ORIGINAL


def test_targeted_fail_patch_applied_false(git_repo, sample_packet):
    with patch("patchpilot.verifier.run_tests", side_effect=_fail):
        result = verify_patch(VALID_DIFF, sample_packet, git_repo)
    assert not result.patch_applied


def test_targeted_fail_captures_exit_code(git_repo, sample_packet):
    with patch("patchpilot.verifier.run_tests", side_effect=_fail):
        result = verify_patch(VALID_DIFF, sample_packet, git_repo)
    assert result.targeted_exit_code == 1


def test_targeted_runner_error_reverts(git_repo, sample_packet):
    with patch("patchpilot.verifier.run_tests", side_effect=RunnerError("timeout")):
        result = verify_patch(VALID_DIFF, sample_packet, git_repo)
    assert not result.ok
    assert result.stage == "targeted"
    assert _calc(git_repo).read_text() == ORIGINAL


# ── Stage 4: Full tests fail ───────────────────────────────────────────────────


def test_full_fail_returns_ok_false(git_repo, sample_packet):
    with patch("patchpilot.verifier.run_tests", side_effect=[_PASS, _FAIL]):
        result = verify_patch(VALID_DIFF, sample_packet, git_repo)
    assert not result.ok
    assert result.stage == "full"


def test_full_fail_reverts_patch(git_repo, sample_packet):
    with patch("patchpilot.verifier.run_tests", side_effect=[_PASS, _FAIL]):
        verify_patch(VALID_DIFF, sample_packet, git_repo)
    assert _calc(git_repo).read_text() == ORIGINAL


def test_full_fail_targeted_results_preserved(git_repo, sample_packet):
    with patch("patchpilot.verifier.run_tests", side_effect=[_PASS, _FAIL]):
        result = verify_patch(VALID_DIFF, sample_packet, git_repo)
    assert result.targeted_exit_code == 0
    assert result.full_exit_code == 1


def test_full_runner_error_reverts(git_repo, sample_packet):
    with patch("patchpilot.verifier.run_tests", side_effect=[_PASS, RunnerError("timeout")]):
        result = verify_patch(VALID_DIFF, sample_packet, git_repo)
    assert not result.ok
    assert result.stage == "full"
    assert _calc(git_repo).read_text() == ORIGINAL


# ── All tests pass ─────────────────────────────────────────────────────────────


def test_all_pass_returns_ok_true(git_repo, sample_packet):
    with patch("patchpilot.verifier.run_tests", side_effect=_pass):
        result = verify_patch(VALID_DIFF, sample_packet, git_repo)
    assert result.ok
    assert result.stage == "full"


def test_all_pass_patch_stays_applied(git_repo, sample_packet):
    with patch("patchpilot.verifier.run_tests", side_effect=_pass):
        verify_patch(VALID_DIFF, sample_packet, git_repo)
    assert _calc(git_repo).read_text() == PATCHED


def test_all_pass_both_exit_codes_zero(git_repo, sample_packet):
    with patch("patchpilot.verifier.run_tests", side_effect=_pass):
        result = verify_patch(VALID_DIFF, sample_packet, git_repo)
    assert result.targeted_exit_code == 0
    assert result.full_exit_code == 0


# ── No targeted command ────────────────────────────────────────────────────────


def test_no_targeted_command_skips_to_full_pass(git_repo, sample_packet):
    packet = {**sample_packet, "verification": {"full_test_command": "pytest -q"}}
    with patch("patchpilot.verifier.run_tests", side_effect=_pass) as mock_run:
        result = verify_patch(VALID_DIFF, packet, git_repo)
    assert result.ok
    assert mock_run.call_count == 1  # only full tests ran


def test_no_targeted_command_targeted_exit_code_is_none(git_repo, sample_packet):
    packet = {**sample_packet, "verification": {"full_test_command": "pytest -q"}}
    with patch("patchpilot.verifier.run_tests", side_effect=_pass):
        result = verify_patch(VALID_DIFF, packet, git_repo)
    assert result.targeted_exit_code is None


def test_empty_full_test_command_falls_back_to_pytest(git_repo, sample_packet):
    packet = {**sample_packet, "verification": {"full_test_command": ""}}
    with patch("patchpilot.verifier.run_tests", side_effect=_pass) as mock_run:
        verify_patch(VALID_DIFF, packet, git_repo)
    called_cmd = mock_run.call_args[0][0]
    assert called_cmd == "pytest -q"


# ── revert_failed and revert_error ────────────────────────────────────────────


def test_revert_failed_flag_set_when_rollback_fails(git_repo, sample_packet):
    bad_revert = ApplyResult(ok=False, stdout="", stderr="could not reverse")
    with patch("patchpilot.verifier.run_tests", side_effect=_fail), \
         patch("patchpilot.verifier.revert_patch", return_value=bad_revert):
        result = verify_patch(VALID_DIFF, sample_packet, git_repo)
    assert result.revert_failed
    assert result.patch_applied  # stuck — couldn't revert


def test_revert_error_contains_stderr_on_rollback_failure(git_repo, sample_packet):
    bad_revert = ApplyResult(ok=False, stdout="", stderr="could not reverse")
    with patch("patchpilot.verifier.run_tests", side_effect=_fail), \
         patch("patchpilot.verifier.revert_patch", return_value=bad_revert):
        result = verify_patch(VALID_DIFF, sample_packet, git_repo)
    assert "could not reverse" in result.revert_error


def test_revert_error_empty_on_successful_rollback(git_repo, sample_packet):
    with patch("patchpilot.verifier.run_tests", side_effect=_fail):
        result = verify_patch(VALID_DIFF, sample_packet, git_repo)
    assert result.revert_error == ""


# ── to_dict ────────────────────────────────────────────────────────────────────


def test_to_dict_returns_dict(git_repo, sample_packet):
    with patch("patchpilot.verifier.run_tests", side_effect=_pass):
        result = verify_patch(VALID_DIFF, sample_packet, git_repo)
    assert isinstance(result.to_dict(), dict)


def test_to_dict_contains_expected_keys(git_repo, sample_packet):
    with patch("patchpilot.verifier.run_tests", side_effect=_pass):
        result = verify_patch(VALID_DIFF, sample_packet, git_repo)
    d = result.to_dict()
    for key in ("ok", "stage", "patch_applied", "violations", "apply_error",
                "targeted_exit_code", "targeted_stdout", "targeted_stderr",
                "full_exit_code", "full_stdout", "full_stderr",
                "revert_failed", "revert_error"):
        assert key in d, f"missing key: {key}"


def test_to_dict_values_match_result(git_repo, sample_packet):
    with patch("patchpilot.verifier.run_tests", side_effect=_pass):
        result = verify_patch(VALID_DIFF, sample_packet, git_repo)
    d = result.to_dict()
    assert d["ok"] is True
    assert d["stage"] == "full"
    assert d["patch_applied"] is True
