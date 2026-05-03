"""Parse pytest --tb=short output into structured failure data."""

import re

from patchpilot.models import ParsedFailure, TracebackFrame

# Matches the short summary line: "FAILED tests/foo.py::test_bar - ErrorType: message"
_FAILED_LINE_RE = re.compile(
    r"^FAILED\s+([\w/\-.]+::[\w\[\],. -]+?)(?:\s+-\s+(.+))?$",
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


def parse_pytest_output(stdout: str, stderr: str = "") -> list[ParsedFailure]:
    output = stdout + stderr
    failures: list[ParsedFailure] = []

    failed_matches = list(_FAILED_LINE_RE.finditer(output))
    if not failed_matches:
        return failures

    # Split the FAILURES block into per-test sections keyed by divider name
    sections = _split_into_sections(output)

    for m in failed_matches:
        test_id = m.group(1).strip()
        error_hint = m.group(2).strip() if m.group(2) else ""

        # The divider name is the test function (last segment after "::"), possibly parameterized
        test_func = test_id.split("::")[-1]

        section_content = _find_section(sections, test_func) or output

        frames = _parse_frames(section_content)
        error_type, message = _parse_error(section_content)

        if not error_type and error_hint:
            parts = error_hint.split(":", 1)
            error_type = parts[0].strip()
            message = parts[1].strip() if len(parts) > 1 else ""

        inner = frames[-1] if frames else None

        failures.append(
            ParsedFailure(
                test=test_id,
                error_type=error_type,
                message=message,
                file=inner.file if inner else "",
                line=inner.line if inner else 0,
                function=inner.function if inner else "",
                raw_traceback=section_content.strip(),
                frames=frames,
            )
        )

    return failures


def _split_into_sections(output: str) -> dict[str, str]:
    """Return {divider_name: section_text} for every ___ name ___ block."""
    dividers = list(_DIVIDER_RE.finditer(output))
    
    # Find where the short test summary starts (matches the line with the separator)
    summary_match = re.search(r"^=+\s+short test summary info\s+=+", output, re.MULTILINE)
    summary_start = summary_match.start() if summary_match else -1
    
    sections: dict[str, str] = {}
    for i, div in enumerate(dividers):
        name = div.group(1).strip()
        start = div.end()
        # Next divider or summary section, whichever comes first
        if i + 1 < len(dividers):
            end = dividers[i + 1].start()
        else:
            # Last divider: stop at summary section if it exists
            end = summary_start if summary_start != -1 else len(output)
        sections[name] = output[start:end]
    return sections


def _find_section(sections: dict[str, str], test_func: str) -> str | None:
    """Find the section whose name matches or contains the test function name."""
    for name, content in sections.items():
        if test_func in name or name in test_func:
            return content
    return None


def _parse_frames(section: str) -> list[TracebackFrame]:
    frames: list[TracebackFrame] = []
    lines = section.splitlines()

    for i, raw in enumerate(lines):
        line = raw.strip()
        m = _SHORT_FRAME_RE.match(line)
        if m:
            next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
            # Skip error lines that immediately follow (E prefix)
            source = "" if next_line.startswith("E ") or next_line.startswith("E\t") else next_line
            frames.append(
                TracebackFrame(
                    file=m.group(1),
                    line=int(m.group(2)),
                    function=m.group(3),
                    source_line=source,
                )
            )

    if not frames:
        for m in _PY_FRAME_RE.finditer(section):
            frames.append(
                TracebackFrame(file=m.group(1), line=int(m.group(2)), function=m.group(3))
            )

    return frames


def _parse_error(section: str) -> tuple[str, str]:
    m = _ERROR_LINE_RE.search(section)
    if m:
        return m.group(1), m.group(2).strip()

    # Fallback: bare "ErrorType: message" (non-pytest-prefixed traceback)
    bare = re.search(r"([A-Za-z]\w*(?:Error|Exception|Warning|Interrupt)):\s*(.+)", section)
    if bare:
        return bare.group(1), bare.group(2).strip()

    return "", ""
