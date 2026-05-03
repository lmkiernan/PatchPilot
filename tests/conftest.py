import subprocess

import pytest


# ── Mock LLM client ────────────────────────────────────────────────────────────

# A realistic git-style diff for the broken_pricing_repo demo fixture.
MOCK_DIFF = """\
diff --git a/src/pricing/calc.py b/src/pricing/calc.py
--- a/src/pricing/calc.py
+++ b/src/pricing/calc.py
@@ -1,2 +1,4 @@
 def compute_discount(price, discount):
+    if discount is None:
+        return price
     return price * discount.percent"""


class MockPatchClient:
    """Deterministic LLM client for tests — never calls the real API."""

    def complete(self, system: str, user: str) -> str:  # noqa: ARG002
        return MOCK_DIFF


# ── Shared fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def mock_client() -> MockPatchClient:
    return MockPatchClient()


# ── Git repo fixture (shared by patch_apply and verifier tests) ────────────────

_CALC_ORIGINAL = (
    "def compute_discount(price, discount):\n"
    "    return price * discount.percent\n"
)


def _git(cwd, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path):
    """Minimal git repo with src/pricing/calc.py committed."""
    src = tmp_path / "src" / "pricing"
    src.mkdir(parents=True)
    (src / "calc.py").write_text(_CALC_ORIGINAL)
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@patchpilot.local")
    _git(tmp_path, "config", "user.name", "PatchPilot Test")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "initial")
    return tmp_path


@pytest.fixture
def sample_packet() -> dict:
    """Minimal repair packet matching the broken_pricing_repo root cause."""
    return {
        "root_cause_id": "root_001",
        "error": {
            "type": "AttributeError",
            "message": "'NoneType' object has no attribute 'percent'",
        },
        "target": {
            "file": "src/pricing/calc.py",
            "line": 2,
            "function": "compute_discount",
            "node_type": "attribute_access",
            "expression": "discount.percent",
        },
        "source_context": (
            "def compute_discount(price, discount):\n"
            "    return price * discount.percent"
        ),
        "imports": [],
        "tests": [
            "def test_no_discount():\n    assert compute_discount(100, None) == 100",
        ],
        "constraints": {
            "return_unified_diff_only": True,
            "touch_source_file_only": True,
            "allow_test_edits": False,
            "prefer_minimal_patch": True,
            "max_files_changed": 1,
            "allowed_files": ["src/pricing/calc.py"],
        },
        "verification": {
            "targeted_test_command": "pytest tests/test_pricing.py::test_no_discount -q",
            "full_test_command": "pytest -q",
        },
    }
