"""
Verifies a candidate diff by applying it and running the test suite.

Pipeline:
  validate → apply → targeted tests → full tests → leave applied
                  ↘ revert on any test failure

The verifier never calls the LLM and never parses pytest output beyond exit codes.
"""

from dataclasses import dataclass, field
from pathlib import Path

from patchpilot.patch_apply import apply_patch, revert_patch
from patchpilot.patch_validator import validate_patch
from patchpilot.runner import RunnerError, run_tests


@dataclass
class VerifyResult:
    ok: bool
    # Last pipeline stage reached: "validation" | "apply" | "targeted" | "full"
    stage: str
    # True when the patch is currently live in the working tree.
    patch_applied: bool
    violations: list[str] = field(default_factory=list)  # validation failures
    apply_error: str = ""                                  # non-empty when apply failed
    targeted_exit_code: int | None = None
    targeted_stdout: str = ""
    targeted_stderr: str = ""
    full_exit_code: int | None = None
    full_stdout: str = ""
    full_stderr: str = ""
    # True when the patch was applied but rollback itself failed — needs human attention.
    revert_failed: bool = False
    revert_error: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "stage": self.stage,
            "patch_applied": self.patch_applied,
            "violations": self.violations,
            "apply_error": self.apply_error,
            "targeted_exit_code": self.targeted_exit_code,
            "targeted_stdout": self.targeted_stdout,
            "targeted_stderr": self.targeted_stderr,
            "full_exit_code": self.full_exit_code,
            "full_stdout": self.full_stdout,
            "full_stderr": self.full_stderr,
            "revert_failed": self.revert_failed,
            "revert_error": self.revert_error,
        }


def verify_patch(
    diff: str,
    packet: dict,
    project_root: Path,
    *,
    max_diff_lines: int = 80,
) -> VerifyResult:
    """
    Run the full verification pipeline for one candidate diff.
    Leaves the patch applied only when every test stage passes.
    """
    project_root = project_root.resolve()
    verification = packet.get("verification", {})
    targeted_cmd = verification.get("targeted_test_command", "")
    full_cmd = verification.get("full_test_command") or "pytest -q"

    # ── 1. Validate ────────────────────────────────────────────────────────────
    val = validate_patch(diff, packet, project_root, max_diff_lines=max_diff_lines)
    if not val.ok:
        return VerifyResult(
            ok=False, stage="validation", patch_applied=False,
            violations=val.violations,
        )

    # ── 2. Apply ───────────────────────────────────────────────────────────────
    apply_result = apply_patch(diff, project_root)
    if not apply_result.ok:
        return VerifyResult(
            ok=False, stage="apply", patch_applied=False,
            apply_error=apply_result.stderr,
        )

    # ── 3. Targeted tests ──────────────────────────────────────────────────────
    t_exit: int | None = None
    t_out = t_err = ""

    if targeted_cmd:
        try:
            t_exit, t_out, t_err = run_tests(targeted_cmd, cwd=str(project_root))
        except RunnerError as e:
            return _revert_and_fail(
                diff, project_root, "targeted",
                targeted_stderr=str(e),
            )
        if t_exit != 0:
            return _revert_and_fail(
                diff, project_root, "targeted",
                targeted_exit_code=t_exit, targeted_stdout=t_out, targeted_stderr=t_err,
            )

    # ── 4. Full tests ──────────────────────────────────────────────────────────
    try:
        f_exit, f_out, f_err = run_tests(full_cmd, cwd=str(project_root))
    except RunnerError as e:
        return _revert_and_fail(
            diff, project_root, "full",
            targeted_exit_code=t_exit, targeted_stdout=t_out, targeted_stderr=t_err,
            full_stderr=str(e),
        )

    if f_exit != 0:
        return _revert_and_fail(
            diff, project_root, "full",
            targeted_exit_code=t_exit, targeted_stdout=t_out, targeted_stderr=t_err,
            full_exit_code=f_exit, full_stdout=f_out, full_stderr=f_err,
        )

    # ── All passed — patch stays applied ──────────────────────────────────────
    return VerifyResult(
        ok=True, stage="full", patch_applied=True,
        targeted_exit_code=t_exit, targeted_stdout=t_out, targeted_stderr=t_err,
        full_exit_code=f_exit, full_stdout=f_out, full_stderr=f_err,
    )


# ── Internal ───────────────────────────────────────────────────────────────────


def _revert_and_fail(
    diff: str,
    project_root: Path,
    stage: str,
    *,
    apply_error: str = "",
    targeted_exit_code: int | None = None,
    targeted_stdout: str = "",
    targeted_stderr: str = "",
    full_exit_code: int | None = None,
    full_stdout: str = "",
    full_stderr: str = "",
) -> VerifyResult:
    revert_result = revert_patch(diff, project_root)
    return VerifyResult(
        ok=False,
        stage=stage,
        patch_applied=not revert_result.ok,
        revert_failed=not revert_result.ok,
        revert_error=revert_result.stderr if not revert_result.ok else "",
        apply_error=apply_error,
        targeted_exit_code=targeted_exit_code,
        targeted_stdout=targeted_stdout,
        targeted_stderr=targeted_stderr,
        full_exit_code=full_exit_code,
        full_stdout=full_stdout,
        full_stderr=full_stderr,
    )
