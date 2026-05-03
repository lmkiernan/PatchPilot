from dataclasses import dataclass, field


@dataclass
class TracebackFrame:
    file: str
    line: int
    function: str
    source_line: str = ""


@dataclass
class ParsedFailure:
    test: str
    error_type: str
    message: str
    file: str
    line: int
    function: str
    raw_traceback: str
    frames: list[TracebackFrame] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "test": self.test,
            "error_type": self.error_type,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "function": self.function,
            "raw_traceback": self.raw_traceback,
        }


@dataclass
class DiagnoseResult:
    command: str
    exit_code: int
    passed: bool
    failures: list[ParsedFailure] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "passed": self.passed,
            "failures": [f.to_dict() for f in self.failures],
        }
