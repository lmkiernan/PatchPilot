"""Extract AST context for a failing source location."""

import ast
from pathlib import Path

from patchpilot.models import ASTContext, TestFunction

# Node types ranked by repair relevance — first match on a line wins.
_NODE_PRIORITY: list[tuple[type, str]] = [
    (ast.Attribute, "attribute_access"),
    (ast.Subscript, "subscript"),
    (ast.Call, "function_call"),
    (ast.Return, "return_statement"),
    (ast.Assign, "assignment"),
    (ast.AugAssign, "augmented_assignment"),
    (ast.Assert, "assertion"),
    (ast.Raise, "raise_statement"),
    (ast.If, "conditional"),
    (ast.Expr, "expression"),
]


def build_context(
    root_cause_id: str,
    source_file: str,
    target_line: int,
    related_tests: list[str],
    project_root: Path,
) -> ASTContext | None:
    """
    Build AST context for a root cause.
    Returns None if the file doesn't exist or can't be parsed.
    """
    file_path = project_root / source_file
    if not file_path.exists():
        return None

    source = file_path.read_text()
    source_lines = source.splitlines()

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return None

    func_node, class_node = _find_enclosing_scope(tree, target_line)
    if func_node is None:
        return None

    func_source = ast.get_source_segment(source, func_node) or ""
    target_source_line = (
        source_lines[target_line - 1].rstrip()
        if 1 <= target_line <= len(source_lines)
        else ""
    )
    func_end = getattr(func_node, "end_lineno", func_node.lineno)

    return ASTContext(
        root_cause_id=root_cause_id,
        source_file=source_file,
        target_line=target_line,
        enclosing_function=func_node.name,
        enclosing_class=class_node.name if class_node else None,
        function_span=[func_node.lineno, func_end],
        function_source=func_source,
        target_source_line=target_source_line,
        node_type=_classify_node(tree, target_line),
        target_expression=_extract_expression(tree, source, target_line),
        imports=_extract_imports(tree, source_lines),
        related_tests=related_tests,
        test_functions=_extract_test_functions(related_tests, project_root),
    )


# ── private helpers ────────────────────────────────────────────────────────────


def _find_enclosing_scope(
    tree: ast.AST, target_line: int
) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef | None, ast.ClassDef | None]:
    """Return (innermost FunctionDef containing target_line, enclosing ClassDef or None)."""
    # Build a child→parent map so we can walk upward
    parent_map: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent_map[child] = node

    # Collect all function nodes that span target_line
    candidates: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            if node.lineno <= target_line <= end:
                candidates.append(node)

    if not candidates:
        return None, None

    # Innermost = smallest span
    best = min(candidates, key=lambda n: getattr(n, "end_lineno", n.lineno) - n.lineno)

    # Walk upward to find an enclosing class
    current: ast.AST = best
    while current in parent_map:
        current = parent_map[current]
        if isinstance(current, ast.ClassDef):
            return best, current

    return best, None


def _classify_node(tree: ast.AST, target_line: int) -> str:
    """Return the most repair-relevant node type name for the given line."""
    nodes_on_line = [
        node
        for node in ast.walk(tree)
        if hasattr(node, "lineno") and node.lineno == target_line
    ]
    for node_type, label in _NODE_PRIORITY:
        if any(isinstance(n, node_type) for n in nodes_on_line):
            return label
    if nodes_on_line:
        return type(nodes_on_line[0]).__name__.lower()
    return "unknown"


def _extract_expression(tree: ast.AST, source: str, target_line: int) -> str:
    """
    Return the source text of the innermost interesting expression on target_line.
    Prefers Attribute > Subscript > Call.  Returns empty string if none found.
    """
    for node_type, _ in _NODE_PRIORITY[:3]:  # Attribute, Subscript, Call
        for node in ast.walk(tree):
            if (
                hasattr(node, "lineno")
                and node.lineno == target_line
                and isinstance(node, node_type)
            ):
                segment = ast.get_source_segment(source, node)
                if segment:
                    return segment
    return ""


def _extract_imports(tree: ast.AST, source_lines: list[str]) -> list[str]:
    """Return top-level (col_offset == 0) import statement source lines."""
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)) and getattr(node, "col_offset", 1) == 0:
            line = source_lines[node.lineno - 1].strip()
            imports.append(line)
    return imports


def _extract_test_functions(related_tests: list[str], project_root: Path) -> list[TestFunction]:
    """
    For each test ID (e.g. "tests/foo.py::test_bar"), parse the test file and
    extract the test function's source.
    """
    results: list[TestFunction] = []
    seen: set[str] = set()

    for test_id in related_tests:
        if "::" not in test_id:
            continue
        # Handle class-based IDs (file::Class::method) and parameterized IDs (file::test[p])
        parts = test_id.split("::")
        file_part = parts[0]
        func_name = parts[-1].split("[", 1)[0]
        if test_id in seen:
            continue
        seen.add(test_id)

        test_path = project_root / file_part
        if not test_path.exists():
            continue

        source = test_path.read_text()
        try:
            tree = ast.parse(source, filename=str(test_path))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                func_source = ast.get_source_segment(source, node) or ""
                results.append(
                    TestFunction(
                        test_id=test_id,
                        file=file_part,
                        function_name=func_name,
                        source=func_source,
                    )
                )
                break

    return results
