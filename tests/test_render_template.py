"""Tests for skills/render_template.py — Jinja2 template rendering.

Covers: load_json_vars, parse_file_spec, render_template, and main via subprocess.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parent.parent / "skills"
if str(SKILLS) not in sys.path:
    sys.path.insert(0, str(SKILLS))

from render_template import load_json_vars, parse_file_spec, render_template


# ---------------------------------------------------------------------------
# load_json_vars
# ---------------------------------------------------------------------------

class TestLoadJsonVars:
    def test_loads_dict(self, tmp_path):
        f = tmp_path / "vars.json"
        f.write_text('{"name": "ACME", "sector": "Tech"}')
        result = load_json_vars(f)
        assert result == {"name": "ACME", "sector": "Tech"}


# ---------------------------------------------------------------------------
# parse_file_spec
# ---------------------------------------------------------------------------

class TestParseFileSpec:
    def test_valid_spec(self, tmp_path):
        f = tmp_path / "intro.md"
        f.write_text("# Introduction\n\nContent here.")
        key, content = parse_file_spec(f"intro={f}")
        assert key == "intro"
        assert "Introduction" in content

    def test_missing_equals(self):
        with pytest.raises(ValueError, match="key=path"):
            parse_file_spec("noequals")

    def test_missing_file(self):
        with pytest.raises(FileNotFoundError):
            parse_file_spec("key=/nonexistent/path.md")


# ---------------------------------------------------------------------------
# render_template
# ---------------------------------------------------------------------------

class TestRenderTemplate:
    def test_basic_render(self, tmp_path):
        template = tmp_path / "test.md.j2"
        template.write_text("Hello {{ name }}, sector: {{ sector }}")
        output = tmp_path / "output.md"

        render_template(template, output, {"name": "ACME", "sector": "Tech"})
        assert output.read_text() == "Hello ACME, sector: Tech"

    def test_creates_output_directory(self, tmp_path):
        template = tmp_path / "test.md.j2"
        template.write_text("Content: {{ body }}")
        output = tmp_path / "deep" / "nested" / "output.md"

        render_template(template, output, {"body": "Hello"})
        assert output.exists()
        assert output.read_text() == "Content: Hello"

    def test_preserves_newline(self, tmp_path):
        template = tmp_path / "test.md.j2"
        template.write_text("Line 1\nLine 2\n")
        output = tmp_path / "output.md"

        render_template(template, output, {})
        assert output.read_text().endswith("\n")


# ---------------------------------------------------------------------------
# CLI (subprocess)
# ---------------------------------------------------------------------------

class TestRenderTemplateCLI:
    def test_basic_cli(self, tmp_path):
        template = tmp_path / "test.md.j2"
        template.write_text("Report for {{ symbol }}")
        output = tmp_path / "output.md"
        json_vars = tmp_path / "vars.json"
        json_vars.write_text('{"symbol": "AAPL"}')

        script = str(SKILLS / "render_template.py")
        result = subprocess.run(
            [
                "uv", "run", "python", script,
                "--template", str(template),
                "--output", str(output),
                "--json", str(json_vars),
            ],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        manifest = json.loads(result.stdout)
        assert manifest["status"] == "complete"
        assert output.read_text() == "Report for AAPL"

    def test_missing_template(self, tmp_path):
        script = str(SKILLS / "render_template.py")
        result = subprocess.run(
            [
                "uv", "run", "python", script,
                "--template", "/nonexistent/template.j2",
                "--output", str(tmp_path / "out.md"),
            ],
            capture_output=True, text=True,
        )
        assert result.returncode == 2
