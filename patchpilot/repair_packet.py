"""Build structured repair packets from a DiagnoseResult for consumption by the patch agent."""

import shlex
from dataclasses import dataclass, field

from patchpilot.models import ASTContext, DiagnoseResult, RootCause

REPAIRS_FILE = "patchpilot_repairs.json"


@dataclass
class RepairConfidence:
    parser: str
    ast_context: str
    test_context: str

    def to_dict(self) -> dict:
        return {
            "parser": self.parser,
            "ast_context": self.ast_context,
            "test_context": self.test_context,
        }


@dataclass
class RepairConstraints:
    allowed_files: list[str]
    return_unified_diff_only: bool = True
    touch_source_file_only: bool = True
    allow_test_edits: bool = False
    prefer_minimal_patch: bool = True
    max_files_changed: int = 1

    def to_dict(self) -> dict:
        return {
            "return_unified_diff_only": self.return_unified_diff_only,
            "touch_source_file_only": self.touch_source_file_only,
            "allowed_files": self.allowed_files,
            "allow_test_edits": self.allow_test_edits,
            "prefer_minimal_patch": self.prefer_minimal_patch,
            "max_files_changed": self.max_files_changed,
        }


@dataclass
class RepairVerification:
    targeted_test_command: str
    full_test_command: str

    def to_dict(self) -> dict:
        return {
            "targeted_test_command": self.targeted_test_command,
            "full_test_command": self.full_test_command,
        }


@dataclass
class RepairPacket:
    root_cause_id: str
    confidence: RepairConfidence
    error: dict
    target: dict
    source_context: str
    imports: list[str]
    tests: list[str]
    constraints: RepairConstraints
    verification: RepairVerification

    def to_dict(self) -> dict:
        return {
            "root_cause_id": self.root_cause_id,
            "confidence": self.confidence.to_dict(),
            "error": self.error,
            "target": self.target,
            "source_context": self.source_context,
            "imports": self.imports,
            "tests": self.tests,
            "constraints": self.constraints.to_dict(),
            "verification": self.verification.to_dict(),
        }


def build_repair_packets(result: DiagnoseResult) -> list[RepairPacket]:
    """
    Convert a DiagnoseResult into one RepairPacket per root cause that has AST context.
    Root causes without AST context are skipped.
    """
    ctx_by_id: dict[str, ASTContext] = {
        ctx.root_cause_id: ctx for ctx in result.ast_contexts
    }

    packets: list[RepairPacket] = []

    for rc in result.root_causes:
        ctx = ctx_by_id.get(rc.id)
        if ctx is None:
            continue

        packets.append(_build_packet(rc, ctx, result.command))

    return packets


def _build_packet(rc: RootCause, ctx: ASTContext, original_command: str) -> RepairPacket:
    targeted_cmd = _build_targeted_command(original_command, rc.affected_tests)

    return RepairPacket(
        root_cause_id=rc.id,
        confidence=_build_confidence(rc, ctx),
        error={
            "type": rc.error_type,
            "message": rc.message,
        },
        target={
            "file": rc.source_file,
            "line": rc.source_line,
            "function": rc.source_function,
            "node_type": ctx.node_type,
            "expression": ctx.target_expression,
        },
        source_context=ctx.function_source,
        imports=ctx.imports,
        tests=[tf.source for tf in ctx.test_functions],
        constraints=RepairConstraints(
            allowed_files=[rc.source_file],
        ),
        verification=RepairVerification(
            targeted_test_command=targeted_cmd,
            full_test_command=original_command,
        ),
    )


def _build_confidence(rc: RootCause, ctx: ASTContext) -> RepairConfidence:
    ast_context_confidence = (
        "high"
        if ctx.function_source and ctx.target_expression
        else "medium"
        if ctx.function_source
        else "low"
    )

    test_context_confidence = "high" if ctx.test_functions else "low"

    return RepairConfidence(
        parser=rc.parser_confidence,
        ast_context=ast_context_confidence,
        test_context=test_context_confidence,
    )


def _build_targeted_command(original_command: str, test_ids: list[str]) -> str:
    """
    Build a pytest command targeting only the affected test IDs.
    Preserves the pytest executable form when possible.
    """
    parts = shlex.split(original_command)

    if not parts:
        return f"pytest {' '.join(test_ids)} -q"

    # Handles: python -m pytest ...
    if len(parts) >= 3 and parts[1] == "-m" and parts[2] == "pytest":
        base = " ".join(parts[:3])

    # Handles: pytest ...
    elif "pytest" in parts:
        pytest_idx = parts.index("pytest")
        base = " ".join(parts[: pytest_idx + 1])

    else:
        base = parts[0]

    return f"{base} {' '.join(test_ids)} -q"