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
