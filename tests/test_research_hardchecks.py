"""Tests for research.py hard check and manifest logic.

Covers: run_hard_checks, write_hard_critique, write_manifest (via run_db mock),
and the collect_custom_prompts flow.
"""
import json
import sys
from pathlib import Path

import pytest

# Make research.py importable
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SKILLS = ROOT / "skills"
if str(SKILLS) not in sys.path:
    sys.path.insert(0, str(SKILLS))

from research import run_hard_checks, write_hard_critique


# ---------------------------------------------------------------------------
# run_hard_checks
# ---------------------------------------------------------------------------

class TestRunHardChecks:
    def test_min_length_pass(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("A" * 500)
        results = run_hard_checks(f, ["min_length: 100"])
        assert len(results) == 1
        assert results[0]["passed"] is True

    def test_min_length_fail(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("short")
        results = run_hard_checks(f, ["min_length: 100"])
        assert results[0]["passed"] is False
        assert "below minimum" in results[0]["message"]

    def test_max_length_pass(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("short")
        results = run_hard_checks(f, ["max_length: 100"])
        assert results[0]["passed"] is True

    def test_max_length_fail(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("A" * 200)
        results = run_hard_checks(f, ["max_length: 100"])
        assert results[0]["passed"] is False
        assert "exceeds maximum" in results[0]["message"]

    def test_startswith_pass(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("# Title\n\nBody text here.")
        results = run_hard_checks(f, ["startswith: # Title"])
        assert results[0]["passed"] is True

    def test_startswith_skips_blank_lines(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("\n\n# Title\n\nBody text here.")
        results = run_hard_checks(f, ["startswith: # Title"])
        assert results[0]["passed"] is True

    def test_startswith_fail(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("Not a heading\nMore text")
        results = run_hard_checks(f, ["startswith: # "])
        assert results[0]["passed"] is False

    def test_contains_pass(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("This report covers the financial analysis of ACME Corp.")
        results = run_hard_checks(f, ["contains: financial analysis"])
        assert results[0]["passed"] is True

    def test_contains_fail(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("No matching content here.")
        results = run_hard_checks(f, ["contains: financial analysis"])
        assert results[0]["passed"] is False

    def test_regex_pass(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("Revenue: $1.5B in FY2025")
        results = run_hard_checks(f, [r"regex: \$\d+\.\d+[BMK]"])
        assert results[0]["passed"] is True

    def test_regex_fail(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("No dollar figures here")
        results = run_hard_checks(f, [r"regex: \$\d+\.\d+[BMK]"])
        assert results[0]["passed"] is False

    def test_multiple_checks(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("# Report\n\n" + "Analysis content. " * 50)
        results = run_hard_checks(f, [
            "startswith: # Report",
            "min_length: 100",
            "contains: Analysis content",
        ])
        assert len(results) == 3
        assert all(r["passed"] for r in results)

    def test_unknown_rule(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("content")
        results = run_hard_checks(f, ["unknown_rule: value"])
        assert results[0]["passed"] is False
        assert "Unknown check rule" in results[0]["message"]

    def test_malformed_check(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("content")
        results = run_hard_checks(f, ["no separator here"])
        assert results[0]["passed"] is False
        assert "Malformed check" in results[0]["message"]


# ---------------------------------------------------------------------------
# write_hard_critique
# ---------------------------------------------------------------------------

class TestWriteHardCritique:
    def test_writes_critique_file(self, tmp_path):
        failures = [
            {"check": "min_length: 500", "passed": False, "message": "Content length 50 is below minimum 500"},
            {"check": "startswith: # Title", "passed": False, "message": "First line is: 'Hello'"},
        ]
        path = write_hard_critique(tmp_path, "section_intro", 1, failures)
        assert path == "drafts/section_intro_hard_critic_1.md"
        full_path = tmp_path / path
        assert full_path.exists()
        content = full_path.read_text()
        assert "HARD CHECK FAILURES" in content
        assert "min_length" in content
        assert "startswith" in content

    def test_critique_includes_actionable_guidance(self, tmp_path):
        failures = [
            {"check": "contains: revenue", "passed": False, "message": "Does not contain 'revenue'"},
        ]
        path = write_hard_critique(tmp_path, "section_fin", 2, failures)
        content = (tmp_path / path).read_text()
        assert "Include the text" in content

    def test_creates_drafts_dir(self, tmp_path):
        failures = [{"check": "min_length: 10", "passed": False, "message": "Too short"}]
        write_hard_critique(tmp_path, "sec", 1, failures)
        assert (tmp_path / "drafts").is_dir()
