"""
Model-agnostic patch agent.

Reads patchpilot_repairs.json, calls an LLM for each repair packet,
and writes candidate_patch_{root_cause_id}.diff to .patchpilot/.
The patch is NOT applied here — that is the verifier's job.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Protocol, runtime_checkable

try:
    from dotenv import load_dotenv
    load_dotenv()  # Load .env file if it exists
except ImportError:
    pass  # python-dotenv not installed, continue without .env loading

CANDIDATE_PATCH_PREFIX = "candidate_patch_"


# ── Provider protocol ──────────────────────────────────────────────────────────


@runtime_checkable
class LLMClient(Protocol):
    """Minimal interface any LLM provider must implement."""

    def complete(self, system: str, user: str) -> str: ...


class AnthropicClient:
    def __init__(self, model: str = "claude-sonnet-4-6", api_key: str | None = None):
        try:
            import anthropic
        except ImportError:
            raise RuntimeError(
                "anthropic package is not installed. Run: pip install anthropic"
            )
        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Export it or pass --api-key."
            )
        self._client = anthropic.Anthropic(api_key=key)
        self._model = model

    def complete(self, system: str, user: str) -> str:
        message = self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        if not message.content:
            raise RuntimeError("LLM returned an empty response")
        return message.content[0].text


def get_client(provider: str = "anthropic", model: str | None = None) -> LLMClient:
    """
    Factory — returns an LLMClient for the named provider.
    Model resolution order: --model flag > PATCHPILOT_MODEL env var > provider default.
    """
    if provider == "anthropic":
        resolved = model or os.environ.get("PATCHPILOT_MODEL") or "claude-sonnet-4-6"
        return AnthropicClient(model=resolved)
    raise ValueError(
        f"Unknown provider: {provider!r}. Supported providers: anthropic"
    )


# ── Core generation ────────────────────────────────────────────────────────────


def generate_patch(packet: dict, client: LLMClient) -> str:
    """
    Call the LLM with a repair packet and return a clean unified diff string.
    The packet is the dict form of a RepairPacket.
    """
    system = _build_system_prompt(packet)
    user = _build_user_message(packet)
    raw = client.complete(system=system, user=user)
    return _extract_diff(raw)


def write_candidate_patch(diff: str, root_cause_id: str, out_dir: Path) -> Path:
    """Write the diff to .patchpilot/candidate_patch_{root_cause_id}.diff."""
    out_path = out_dir / f"{CANDIDATE_PATCH_PREFIX}{root_cause_id}.diff"
    out_path.write_text(diff)
    return out_path


def render_prompt(packet: dict) -> tuple[str, str]:
    """Return (system, user) prompt strings without calling the LLM."""
    return _build_system_prompt(packet), _build_user_message(packet)


# ── Diff cache ─────────────────────────────────────────────────────────────────


CACHE_DIR_NAME = "cache"


def _packet_hash(packet: dict) -> str:
    """16-char SHA-256 hash of the LLM-visible fields. Stable across runs."""
    llm_fields = {
        k: packet[k]
        for k in ("error", "target", "source_context", "imports", "tests", "constraints")
        if k in packet
    }
    content = json.dumps(llm_fields, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def load_cached_diff(packet: dict, out_dir: Path) -> str | None:
    """Return a previously cached diff for this packet, or None if not cached."""
    cache_file = out_dir / CACHE_DIR_NAME / f"{_packet_hash(packet)}.diff"
    return cache_file.read_text() if cache_file.exists() else None


def save_cached_diff(diff: str, packet: dict, out_dir: Path) -> None:
    """Cache a generated diff keyed by the packet's content hash."""
    cache_dir = out_dir / CACHE_DIR_NAME
    cache_dir.mkdir(exist_ok=True)
    (cache_dir / f"{_packet_hash(packet)}.diff").write_text(diff)


# ── Prompt construction ────────────────────────────────────────────────────────


def _build_system_prompt(packet: dict) -> str:
    constraints = packet.get("constraints", {})
    max_files = constraints.get("max_files_changed", 1)
    allowed = constraints.get("allowed_files", [])
    allow_test_edits = constraints.get("allow_test_edits", False)
    allowed_str = f"Only modify these file(s): {', '.join(allowed)}." if allowed else ""

    if allow_test_edits:
        test_rule = "Prefer fixing the source over modifying the test."
    else:
        test_rule = "Do not modify test files. Fix the source only."

    return f"""\
You are a CI repair agent. Your sole job is to repair a failing pytest test by generating the minimal patch.

OUTPUT RULE: Respond with a git-style unified diff and nothing else. No explanation, no markdown \
fences, no preamble, no trailing commentary. Your response must start with "diff --git" on the first line.

PATCH RULES:
- Touch at most {max_files} file(s). {allowed_str}
- {test_rule}
- Make the smallest possible change — no refactoring, renaming, or unrelated cleanup
- Preserve all existing function signatures and public APIs
- Do not touch lockfiles, CI config, environment files, or files unrelated to the failure

DIFF FORMAT (follow exactly):
diff --git a/path/to/file.py b/path/to/file.py
--- a/path/to/file.py
+++ b/path/to/file.py
@@ -N,M +N,M @@
 context line
-removed line
+added line"""


def _build_user_message(packet: dict) -> str:
    # Strip operational metadata before sending to the LLM —
    # confidence and verification are for the pipeline, not the model.
    llm_context = {
        k: packet[k]
        for k in ("error", "target", "source_context", "imports", "tests", "constraints")
        if k in packet
    }
    return f"Repair packet:\n\n{json.dumps(llm_context, indent=2)}\n\nOutput the unified diff now."


# ── Diff extraction ────────────────────────────────────────────────────────────


def _extract_diff(raw: str) -> str:
    """
    Return a clean diff string from raw LLM output.
    Strips markdown fences and leading prose; prefers "diff --git" header,
    falls back to the first "---" / "+++" pair.
    """
    text = raw.strip()

    # Strip opening fence line and closing ``` if present
    if text.startswith("```"):
        lines = text.splitlines()
        start = 1  # skip the ```diff or ``` line
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        text = "\n".join(lines[start:end]).strip()

    lines = text.splitlines()

    # Preferred: git-style header "diff --git a/... b/..."
    for i, line in enumerate(lines):
        if line.startswith("diff --git "):
            return "\n".join(lines[i:]).strip()

    # Fallback: plain unified diff "--- ..." immediately followed by "+++"
    for i, line in enumerate(lines):
        if line.startswith("---") and i + 1 < len(lines) and lines[i + 1].startswith("+++"):
            return "\n".join(lines[i:]).strip()

    # Return whatever we have — the validator will catch a malformed diff
    return text
