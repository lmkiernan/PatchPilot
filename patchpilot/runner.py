import shlex
import shutil
import subprocess


class RunnerError(Exception):
    """Raised when the test command itself cannot execute (setup problem, not test failure)."""


# pytest exit codes where the command ran successfully but tests may have failed
_VALID_EXIT_CODES = {0, 1, 5}

# Maps pytest exit codes to human-readable reasons for setup errors
_SETUP_EXIT_REASONS = {
    2: "interrupted (KeyboardInterrupt or similar)",
    3: "internal pytest error",
    4: "command-line usage error — check your test command syntax",
}


def run_tests(command: str) -> tuple[int, str, str]:
    """
    Run the test command and return (exit_code, stdout, stderr).

    Appends --tb=short if no --tb flag is present, for reliable traceback parsing.
    Raises RunnerError if the command itself cannot be run or pytest errors out.
    """
    parts = shlex.split(command)
    executable = parts[0]

    if not shutil.which(executable):
        raise RunnerError(
            f"Command not found: {executable!r}\n"
            "Make sure it is installed and available on your PATH."
        )

    if "--tb" not in command:
        parts.extend(["--tb=short"])

    result = subprocess.run(parts, capture_output=True, text=True)

    if result.returncode not in _VALID_EXIT_CODES:
        reason = _SETUP_EXIT_REASONS.get(result.returncode, f"exit code {result.returncode}")
        output = (result.stdout + result.stderr).strip()
        raise RunnerError(
            f"Test command failed to run properly: {reason}.\n\nOutput:\n{output}"
        )

    return result.returncode, result.stdout, result.stderr
