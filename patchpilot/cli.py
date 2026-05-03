import json
import sys
from pathlib import Path

import click

from patchpilot.ast_context import build_context
from patchpilot.failure_parser import group_root_causes, parse_pytest_output
from patchpilot.models import DiagnoseResult
from patchpilot.patch_agent import (
    generate_patch,
    get_client,
    load_cached_diff,
    render_prompt,
    save_cached_diff,
    write_candidate_patch,
)
from patchpilot.repair_packet import REPAIRS_FILE, build_repair_packets
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
        exit_code, stdout, stderr = run_tests(test_command, cwd=str(root))
    except RunnerError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(1)

    passed = exit_code == 0
    failures = [] if passed else parse_pytest_output(stdout, stderr)
    root_causes = group_root_causes(failures)

    ast_contexts = []
    for rc in root_causes:
        try:
            ctx = build_context(
                root_cause_id=rc.id,
                source_file=rc.source_file,
                target_line=rc.source_line,
                related_tests=rc.affected_tests,
                project_root=root,
            )
        except Exception as e:
            click.echo(f"  warning: AST context failed for {rc.id}: {e}", err=True)
            ctx = None
        if ctx is not None:
            ast_contexts.append(ctx)

    result = DiagnoseResult(
        command=test_command,
        exit_code=exit_code,
        passed=passed,
        failures=failures,
        root_causes=root_causes,
        ast_contexts=ast_contexts,
    )

    out_dir = root / OUTPUT_DIR
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / OUTPUT_FILE

    out_path.write_text(json.dumps(result.to_dict(), indent=2))

    if not passed and result.ast_contexts:
        repairs = build_repair_packets(result)
        repairs_data = {"schema_version": "0.1", "repairs": [r.to_dict() for r in repairs]}
        (out_dir / REPAIRS_FILE).write_text(json.dumps(repairs_data, indent=2))

    if passed:
        click.echo("All tests passed. No failures to report.")
    else:
        click.echo(f"Found {len(failures)} failure(s) across {len(root_causes)} root cause(s).")
        for rc in root_causes:
            ctx = next((c for c in ast_contexts if c.root_cause_id == rc.id), None)
            click.echo(f"  [{rc.id}] {rc.source_file}:{rc.source_line} in {rc.source_function}()")
            click.echo(f"    {rc.error_type}: {rc.message}")
            click.echo(f"    affects: {', '.join(rc.affected_failure_ids)}")
            if ctx:
                click.echo(f"    node_type: {ctx.node_type}")
                if ctx.target_expression:
                    click.echo(f"    expression: {ctx.target_expression}")

    try:
        display_path = out_path.relative_to(Path.cwd())
    except ValueError:
        display_path = out_path
    click.echo(f"\nWrote {display_path}")


@main.command("propose-patch")
@click.option("--provider", default="anthropic", show_default=True, help="LLM provider to use.")
@click.option(
    "--model",
    default=None,
    help="Model override (PATCHPILOT_MODEL env var or provider default if omitted).",
)
@click.option(
    "--project-root",
    default=".",
    show_default=True,
    type=click.Path(exists=True, file_okay=False),
    help="Root directory containing .patchpilot/.",
)
@click.option(
    "--dry-run-prompt",
    is_flag=True,
    help="Print the rendered system/user prompt for the first packet without calling the API.",
)
@click.option(
    "--no-cache",
    is_flag=True,
    help="Skip the diff cache and always call the LLM.",
)
def propose_patch(
    provider: str,
    model: str | None,
    project_root: str,
    dry_run_prompt: bool,
    no_cache: bool,
) -> None:
    """Generate a candidate diff per root cause and write it to .patchpilot/ — does not apply it."""
    root = Path(project_root).resolve()
    out_dir = root / OUTPUT_DIR
    repairs_path = out_dir / REPAIRS_FILE

    if not repairs_path.exists():
        click.echo(
            f"error: {repairs_path} not found — run 'patchpilot diagnose' first.", err=True
        )
        sys.exit(1)

    repairs_data = json.loads(repairs_path.read_text())
    packets = repairs_data.get("repairs", [])

    if not packets:
        click.echo("No repair packets found. Nothing to patch.")
        return

    if dry_run_prompt:
        system, user = render_prompt(packets[0])
        click.echo("── SYSTEM PROMPT ──────────────────────────────────────")
        click.echo(system)
        click.echo("\n── USER MESSAGE ───────────────────────────────────────")
        click.echo(user)
        return

    try:
        client = get_client(provider=provider, model=model)
    except (RuntimeError, ValueError) as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(1)

    for packet in packets:
        rc_id = packet.get("root_cause_id", "unknown")
        target = packet.get("target", {})
        click.echo(
            f"Generating patch for {rc_id} "
            f"({target.get('file', '?')}:{target.get('line', '?')} "
            f"in {target.get('function', '?')}())"
        )

        if not no_cache:
            cached = load_cached_diff(packet, out_dir)
            if cached:
                click.echo("  cache hit — reusing cached diff")
                out_path = write_candidate_patch(cached, rc_id, out_dir)
                try:
                    display = out_path.relative_to(Path.cwd())
                except ValueError:
                    display = out_path
                click.echo(f"  wrote {display}")
                continue

        try:
            diff = generate_patch(packet, client)
        except Exception as e:
            click.echo(f"  error: LLM call failed for {rc_id}: {e}", err=True)
            continue

        if not diff:
            click.echo(f"  warning: empty diff returned for {rc_id}", err=True)
            continue

        if not no_cache:
            save_cached_diff(diff, packet, out_dir)

        out_path = write_candidate_patch(diff, rc_id, out_dir)
        try:
            display = out_path.relative_to(Path.cwd())
        except ValueError:
            display = out_path
        click.echo(f"  wrote {display}")
