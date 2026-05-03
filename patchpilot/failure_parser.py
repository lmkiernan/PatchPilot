"""Parse pytest --tb=short output into structured failure data."""

import re
from collections import defaultdict

from patchpilot.models import ParsedFailure, RootCause, TracebackFrame

# Matches the short summary line: "FAILED tests/foo.py::test_bar - ErrorType: message"
# Permissive test-id match to handle parameterized names, spaces, and unusual characters.
_FAILED_LINE_RE = re.compile(
    r"^FAILED\s+(.+?)(?:\s+-\s+(.+))?$",
    re.MULTILINE,
)

# Section divider: "___ test_name ___" (3+ underscores, any width)
_DIVIDER_RE = re.compile(r"^_{3,}\s+(.+?)\s+_{3,}\s*$", re.MULTILINE)

# pytest --tb=short frame: "path/file.py:42: in function_name"
_SHORT_FRAME_RE = re.compile(r"^([\w/\-.]+\.py):(\d+):\s+in\s+(\w+)\s*$")

# Standard Python traceback frame: 'File "path", line 42, in function_name'
_PY_FRAME_RE = re.compile(r'File "([^"]+)", line (\d+), in (\w+)')

# Error line: "E   ErrorType: message" (pytest-prefixed)
_ERROR_LINE_RE = re.compile(
    r"^E\s+([A-Za-z]\w*(?:Error|Exception|Warning|Interrupt)):\s*(.*)\s*$",
    re.MULTILINE,
)

_CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}


def parse_pytest_output(stdout: str, stderr: str = "") -> list[ParsedFailure]:
    output = stdout + stderr
    failures: list[ParsedFailure] = []

    failed_matches = list(_FAILED_LINE_RE.finditer(output))
    if not failed_matches:
        return failures

    sections = _split_into_sections(output)

    for i, m in enumerate(failed_matches, 1):
        test_id = m.group(1).strip()
        error_hint = m.group(2).strip() if m.group(2) else ""
        test_func = test_id.split("::")[-1]

        section_content = _find_section(sections, test_func) or output

        frames, confidence = _parse_frames(section_content)
        error_type, message = _parse_error(section_content)

        if not error_type and error_hint:
            parts = error_hint.split(":", 1)
            error_type = parts[0].strip()
            message = parts[1].strip() if len(parts) > 1 else ""
            # Error came from the summary line fallback, not directly parsed
            confidence = _downgrade(confidence)

        inner = frames[-1] if frames else None

        failures.append(
            ParsedFailure(
                id=f"failure_{i:03d}",
                test=test_id,
                error_type=error_type,
                message=message,
                source_file=inner.file if inner else "",
                source_line=inner.line if inner else 0,
                source_function=inner.function if inner else "",
                raw_traceback=section_content.strip(),
                frames=frames,
                confidence=confidence,
            )
        )

    return failures


def group_root_causes(failures: list[ParsedFailure]) -> list[RootCause]:
    """Group failures that share the same root location into RootCause objects."""
    groups: dict[tuple, list[ParsedFailure]] = defaultdict(list)
    for f in failures:
        key = (f.source_file, f.source_line, f.source_function, f.error_type, f.message)
        groups[key].append(f)

    root_causes: list[RootCause] = []
    for i, (key, group) in enumerate(groups.items(), 1):
        source_file, source_line, source_function, error_type, message = key
        confidence = _lowest_confidence(f.confidence for f in group)
        root_causes.append(
            RootCause(
                id=f"root_{i:03d}",
                source_file=source_file,
                source_line=source_line,
                source_function=source_function,
                error_type=error_type,
                message=message,
                affected_failure_ids=[f.id for f in group],
                affected_tests=[f.test for f in group],
                parser_confidence=confidence,
            )
        )

    return root_causes


# ── private helpers ────────────────────────────────────────────────────────────


def _split_into_sections(output: str) -> dict[str, str]:
    dividers = list(_DIVIDER_RE.finditer(output))
    summary_match = re.search(r"^=+\s+short test summary info\s+=+", output, re.MULTILINE)
    summary_start = summary_match.start() if summary_match else -1

    sections: dict[str, str] = {}
    for i, div in enumerate(dividers):
        name = div.group(1).strip()
        start = div.end()
        if i + 1 < len(dividers):
            end = dividers[i + 1].start()
        else:
            end = summary_start if summary_start != -1 else len(output)
        sections[name] = output[start:end]
    return sections


def _find_section(sections: dict[str, str], test_func: str) -> str | None:
    for name, content in sections.items():
        if test_func in name or name in test_func:
            return content
    return None


def _parse_frames(section: str) -> tuple[list[TracebackFrame], str]:
    frames: list[TracebackFrame] = []
    lines = section.splitlines()

    for i, raw in enumerate(lines):
        line = raw.strip()
        m = _SHORT_FRAME_RE.match(line)
        if m:
            next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
            source = "" if next_line.startswith("E ") or next_line.startswith("E\t") else next_line
            frames.append(
                TracebackFrame(
                    file=m.group(1),
                    line=int(m.group(2)),
                    function=m.group(3),
                    source_line=source,
                )
            )

    if frames:
        return frames, "high"

    for m in _PY_FRAME_RE.finditer(section):
        frames.append(TracebackFrame(file=m.group(1), line=int(m.group(2)), function=m.group(3)))

    return frames, ("medium" if frames else "low")


def _parse_error(section: str) -> tuple[str, str]:
    m = _ERROR_LINE_RE.search(section)
    if m:
        return m.group(1), m.group(2).strip()

    bare = re.search(r"([A-Za-z]\w*(?:Error|Exception|Warning|Interrupt)):\s*(.+)", section)
    if bare:
        return bare.group(1), bare.group(2).strip()

    return "", ""


def _downgrade(confidence: str) -> str:
    rank = _CONFIDENCE_RANK.get(confidence, 2)
    upgraded = min(rank + 1, 2)
    return ["high", "medium", "low"][upgraded]


def _lowest_confidence(confidences) -> str:
    return max(confidences, key=lambda c: _CONFIDENCE_RANK.get(c, 2))
