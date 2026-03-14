"""Tests for skills/claude_runner.py — prompt building and output verification.

These are unit tests that do NOT spawn Claude subprocesses. They test the
internal helper functions: _build_prompt, _build_command, and _check_outputs.
"""
import sys
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parent.parent / "skills"
if str(SKILLS) not in sys.path:
    sys.path.insert(0, str(SKILLS))

from claude_runner import _build_prompt, _build_command, _check_outputs


# ---------------------------------------------------------------------------
# _build_prompt
# ---------------------------------------------------------------------------

class TestBuildPrompt:
    def test_basic_prompt(self, tmp_path):
        result = _build_prompt(
            "Write a report", tmp_path, "test",
            system=None, artifacts_inline=None,
            expected_outputs=None, output_file=None,
        )
        assert "Write a report" in result

    def test_system_prompt_prepended(self, tmp_path):
        result = _build_prompt(
            "Write a report", tmp_path, "test",
            system="You are a researcher.", artifacts_inline=None,
            expected_outputs=None, output_file=None,
        )
        lines = result.split("\n")
        assert lines[0] == "You are a researcher."
        assert "Write a report" in result

    def test_inline_artifacts(self, tmp_path):
        art_path = tmp_path / "artifacts" / "data.json"
        art_path.parent.mkdir(parents=True, exist_ok=True)
        art_path.write_text('{"key": "value"}')

        result = _build_prompt(
            "Analyze the data", tmp_path, "test",
            system=None,
            artifacts_inline=["artifacts/data.json"],
            expected_outputs=None, output_file=None,
        )
        assert "INLINE ARTIFACTS" in result
        assert '{"key": "value"}' in result

    def test_skips_large_artifacts(self, tmp_path):
        art_path = tmp_path / "big.txt"
        art_path.write_text("x" * 60_000)

        result = _build_prompt(
            "task", tmp_path, "test",
            system=None, artifacts_inline=["big.txt"],
            expected_outputs=None, output_file=None,
        )
        # Large artifact should be skipped
        assert "x" * 60_000 not in result

    def test_skips_missing_artifacts(self, tmp_path):
        result = _build_prompt(
            "task", tmp_path, "test",
            system=None, artifacts_inline=["nonexistent.json"],
            expected_outputs=None, output_file=None,
        )
        assert "nonexistent.json" not in result or "Skipping" not in result

    def test_save_instructions_for_expected_outputs(self, tmp_path):
        outputs = {
            "report": {"path": "artifacts/report.md", "format": "md"},
        }
        result = _build_prompt(
            "Write the report", tmp_path, "test",
            system=None, artifacts_inline=None,
            expected_outputs=outputs, output_file=None,
        )
        assert 'Save your output for "report" to artifacts/report.md' in result

    def test_no_duplicate_save_instructions(self, tmp_path):
        """If the output path is already in the prompt, don't add save instructions."""
        outputs = {
            "report": {"path": "artifacts/report.md", "format": "md"},
        }
        result = _build_prompt(
            "Write to artifacts/report.md", tmp_path, "test",
            system=None, artifacts_inline=None,
            expected_outputs=outputs, output_file=None,
        )
        assert result.count("Save your output") == 0

    def test_output_file_mode(self, tmp_path):
        result = _build_prompt(
            "Generate data", tmp_path, "test",
            system=None, artifacts_inline=None,
            expected_outputs=None, output_file="artifacts/data.json",
        )
        assert 'Save your output for "output" to artifacts/data.json' in result


# ---------------------------------------------------------------------------
# _build_command
# ---------------------------------------------------------------------------

class TestBuildCommand:
    def test_basic_command(self):
        cmd = _build_command(
            "/abs/workdir", disallowed_tools=None, model=None,
            max_budget_usd=None, mcp_config=None, debug=False,
            workdir=Path("/abs/workdir"), step_label="test",
        )
        assert "claude" in cmd
        assert "--dangerously-skip-permissions" in cmd
        assert "-d" in cmd
        assert "/abs/workdir" in cmd

    def test_model_override(self):
        cmd = _build_command(
            "/w", disallowed_tools=None, model="claude-sonnet-4-5-20250929",
            max_budget_usd=None, mcp_config=None, debug=False,
            workdir=Path("/w"), step_label="test",
        )
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "claude-sonnet-4-5-20250929"

    def test_disallowed_tools(self):
        cmd = _build_command(
            "/w", disallowed_tools=["WebSearch", "WebFetch"], model=None,
            max_budget_usd=None, mcp_config=None, debug=False,
            workdir=Path("/w"), step_label="test",
        )
        idx = cmd.index("--disallowedTools")
        assert cmd[idx + 1] == "WebSearch,WebFetch"

    def test_max_budget(self):
        cmd = _build_command(
            "/w", disallowed_tools=None, model=None,
            max_budget_usd=1.5, mcp_config=None, debug=False,
            workdir=Path("/w"), step_label="test",
        )
        idx = cmd.index("--max-budget-usd")
        assert cmd[idx + 1] == "1.5"

    def test_mcp_config(self):
        cmd = _build_command(
            "/w", disallowed_tools=None, model=None,
            max_budget_usd=None, mcp_config=["mcp-research.json"], debug=False,
            workdir=Path("/w"), step_label="test",
        )
        idx = cmd.index("--mcp-config")
        assert cmd[idx + 1] == "mcp-research.json"


# ---------------------------------------------------------------------------
# _check_outputs
# ---------------------------------------------------------------------------

class TestCheckOutputs:
    def test_all_outputs_exist(self, tmp_path):
        (tmp_path / "report.md").write_text("content")
        result = _check_outputs(
            {"report": {"path": "report.md", "format": "md"}},
            tmp_path, "test", returncode=0,
        )
        assert result["status"] == "complete"
        assert len(result["artifacts"]) == 1
        assert result["artifacts"][0]["name"] == "report"

    def test_missing_output(self, tmp_path):
        result = _check_outputs(
            {"report": {"path": "report.md", "format": "md"}},
            tmp_path, "test", returncode=0,
        )
        assert result["status"] == "failed"
        assert "Missing output" in result["error"]

    def test_empty_output_not_in_artifacts(self, tmp_path):
        (tmp_path / "report.md").write_text("")
        (tmp_path / "data.json").write_text('{"key": "val"}')
        result = _check_outputs(
            {
                "report": {"path": "report.md", "format": "md"},
                "data": {"path": "data.json", "format": "json"},
            },
            tmp_path, "test", returncode=0,
        )
        # Empty file is not treated as missing (doesn't fail), but excluded from artifacts
        assert result["status"] == "complete"
        assert len(result["artifacts"]) == 1
        assert result["artifacts"][0]["name"] == "data"

    def test_ignores_nonzero_returncode_when_outputs_exist(self, tmp_path):
        """Claude CLI can exit non-zero for transient reasons — we check files not rc."""
        (tmp_path / "report.md").write_text("content")
        result = _check_outputs(
            {"report": {"path": "report.md", "format": "md"}},
            tmp_path, "test", returncode=1,
        )
        assert result["status"] == "complete"

    def test_no_expected_outputs(self, tmp_path):
        result = _check_outputs({}, tmp_path, "test", returncode=0)
        assert result["status"] == "complete"
        assert result["artifacts"] == []

    def test_multiple_outputs(self, tmp_path):
        (tmp_path / "a.md").write_text("content a")
        (tmp_path / "b.json").write_text('{}')
        result = _check_outputs(
            {
                "report": {"path": "a.md", "format": "md"},
                "data": {"path": "b.json", "format": "json"},
            },
            tmp_path, "test", returncode=0,
        )
        assert result["status"] == "complete"
        assert len(result["artifacts"]) == 2
