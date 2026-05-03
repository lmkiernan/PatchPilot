from dataclasses import dataclass, field


@dataclass
class TracebackFrame:
    file: str
    line: int
    function: str
    source_line: str = ""


@dataclass
class ParsedFailure:
    id: str
    test: str
    error_type: str
    message: str
    source_file: str
    source_line: int
    source_function: str
    raw_traceback: str
    frames: list[TracebackFrame] = field(default_factory=list)
    confidence: str = "high"  # "high" | "medium" | "low"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "test": self.test,
            "error_type": self.error_type,
            "message": self.message,
            "source_file": self.source_file,
            "source_line": self.source_line,
            "source_function": self.source_function,
            "parser_confidence": self.confidence,
            "raw_traceback": self.raw_traceback,
        }


@dataclass
class RootCause:
    id: str
    source_file: str
    source_line: int
    source_function: str
    error_type: str
    message: str
    affected_failure_ids: list[str]
    affected_tests: list[str]
    parser_confidence: str  # "high" | "medium" | "low"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source_file": self.source_file,
            "source_line": self.source_line,
            "source_function": self.source_function,
            "error_type": self.error_type,
            "message": self.message,
            "affected_failure_ids": self.affected_failure_ids,
            "affected_tests": self.affected_tests,
            "parser_confidence": self.parser_confidence,
        }


@dataclass
class DiagnoseResult:
    command: str
    exit_code: int
    passed: bool
    failures: list[ParsedFailure] = field(default_factory=list)
    root_causes: list[RootCause] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "schema_version": "0.1",
            "framework": "pytest",
            "command": self.command,
            "exit_code": self.exit_code,
            "passed": self.passed,
            "failure_count": len(self.failures),
            "root_cause_count": len(self.root_causes),
            "failures": [f.to_dict() for f in self.failures],
            "root_causes": [r.to_dict() for r in self.root_causes],
        }
