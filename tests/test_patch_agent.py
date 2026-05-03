"""Tests for patch_agent.py — all run without an ANTHROPIC_API_KEY."""

import pytest

from patchpilot.patch_agent import (
    _extract_diff,
    _packet_hash,
    generate_patch,
    load_cached_diff,
    render_prompt,
    save_cached_diff,
    write_candidate_patch,
)
from tests.conftest import MOCK_DIFF


# ── generate_patch ─────────────────────────────────────────────────────────────


def test_generate_patch_returns_diff(mock_client, sample_packet):
    diff = generate_patch(sample_packet, mock_client)
    assert diff.startswith("diff --git")


def test_generate_patch_contains_target_file(mock_client, sample_packet):
    diff = generate_patch(sample_packet, mock_client)
    assert "src/pricing/calc.py" in diff


# ── write_candidate_patch ──────────────────────────────────────────────────────


def test_write_candidate_patch_creates_file(tmp_path, sample_packet, mock_client):
    diff = generate_patch(sample_packet, mock_client)
    out_path = write_candidate_patch(diff, "root_001", tmp_path)
    assert out_path.exists()
    assert out_path.name == "candidate_patch_root_001.diff"


def test_write_candidate_patch_content(tmp_path, sample_packet, mock_client):
    diff = generate_patch(sample_packet, mock_client)
    out_path = write_candidate_patch(diff, "root_001", tmp_path)
    assert out_path.read_text() == diff


# ── _extract_diff ──────────────────────────────────────────────────────────────


def test_extract_diff_clean_input():
    result = _extract_diff(MOCK_DIFF)
    assert result.startswith("diff --git")


def test_extract_diff_strips_markdown_fence():
    fenced = f"```diff\n{MOCK_DIFF}\n```"
    result = _extract_diff(fenced)
    assert result.startswith("diff --git")
    assert "```" not in result


def test_extract_diff_strips_preamble():
    with_preamble = f"Here is the fix:\n\n{MOCK_DIFF}"
    result = _extract_diff(with_preamble)
    assert result.startswith("diff --git")


def test_extract_diff_fallback_to_plain_unified():
    plain = (
        "--- a/src/pricing/calc.py\n"
        "+++ b/src/pricing/calc.py\n"
        "@@ -1,2 +1,4 @@\n"
        " def compute_discount(price, discount):\n"
        "+    if discount is None:\n"
        "+        return price\n"
        "     return price * discount.percent"
    )
    result = _extract_diff(plain)
    assert result.startswith("---")


# ── render_prompt ──────────────────────────────────────────────────────────────


def test_render_prompt_returns_two_strings(sample_packet):
    system, user = render_prompt(sample_packet)
    assert isinstance(system, str) and len(system) > 0
    assert isinstance(user, str) and len(user) > 0


def test_render_prompt_system_contains_output_rule(sample_packet):
    system, _ = render_prompt(sample_packet)
    assert "diff --git" in system


def test_render_prompt_no_test_edits_rule(sample_packet):
    system, _ = render_prompt(sample_packet)
    assert "Do not modify test files" in system


def test_render_prompt_user_contains_error_type(sample_packet):
    _, user = render_prompt(sample_packet)
    assert "AttributeError" in user


def test_render_prompt_strips_confidence_and_verification(sample_packet):
    _, user = render_prompt(sample_packet)
    assert "confidence" not in user
    assert "verification" not in user


# ── diff cache ─────────────────────────────────────────────────────────────────


def test_packet_hash_is_stable(sample_packet):
    assert _packet_hash(sample_packet) == _packet_hash(sample_packet)


def test_packet_hash_differs_for_different_packets(sample_packet):
    other = {**sample_packet, "error": {"type": "KeyError", "message": "'id'"}}
    assert _packet_hash(sample_packet) != _packet_hash(other)


def test_cache_round_trip(tmp_path, sample_packet):
    save_cached_diff(MOCK_DIFF, sample_packet, tmp_path)
    loaded = load_cached_diff(sample_packet, tmp_path)
    assert loaded == MOCK_DIFF


def test_cache_miss_returns_none(tmp_path, sample_packet):
    assert load_cached_diff(sample_packet, tmp_path) is None


def test_cache_creates_cache_subdir(tmp_path, sample_packet):
    save_cached_diff(MOCK_DIFF, sample_packet, tmp_path)
    assert (tmp_path / "cache").is_dir()
