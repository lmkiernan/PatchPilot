import json
import sys
from pathlib import Path

import click

from patchpilot.failure_parser import parse_pytest_output
from patchpilot.models import DiagnoseResult
from patchpilot.runner import RunnerError, run_tests

OUTPUT_DIR = ".patchpilot"
OUTPUT_FILE = "patchpilot_failures.json"


@click.group()
def main():
    """PatchPilot — AST-guided CI repair agent for Python/pytest projects."""


@main.command()
@click.option(
    "--test-command",
    required=True,
    help='Test command to run, e.g. "pytest -q"',
)
@click.option(
    "--project-root",
    default=".",
    show_default=True,
    type=click.Path(exists=True, file_okay=False),
    help="Root directory of the project under test.",
)
def diagnose(test_command: str, project_root: str) -> None:
    """Run the test suite and write structured failure data to .patchpilot/patchpilot_failures.json."""
    root = Path(project_root).resolve()

    click.echo(f"Running: {test_command}")

    try:
        exit_code, stdout, stderr = run_tests(test_command)
    except RunnerError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(1)

    passed = exit_code == 0
    failures = [] if passed else parse_pytest_output(stdout, stderr)

    result = DiagnoseResult(
        command=test_command,
        exit_code=exit_code,
        passed=passed,
        failures=failures,
    )

    out_dir = root / OUTPUT_DIR
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / OUTPUT_FILE

    out_path.write_text(json.dumps(result.to_dict(), indent=2))

    if passed:
        click.echo("All tests passed. No failures to report.")
    else:
        click.echo(f"Found {len(failures)} failure(s).")
        for f in failures:
            click.echo(f"  {f.test}")
            click.echo(f"    {f.error_type}: {f.message}")
            if f.file:
                click.echo(f"    {f.file}:{f.line} in {f.function}()")

    click.echo(f"\nWrote {out_path.relative_to(Path.cwd())}")
