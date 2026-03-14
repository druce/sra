"""Tests for skills/final_assembly.py and skills/assemble_text.py.

Covers: build_peers_list, build_technical_context, load_json, load_text.
Also covers assemble_text.py main() via subprocess.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parent.parent / "skills"
if str(SKILLS) not in sys.path:
    sys.path.insert(0, str(SKILLS))

from final_assembly import build_peers_list, build_technical_context, load_json, load_text


# ---------------------------------------------------------------------------
# load_json / load_text
# ---------------------------------------------------------------------------

class TestLoadHelpers:
    def test_load_json_valid(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text('{"key": "value"}')
        result = load_json(f)
        assert result == {"key": "value"}

    def test_load_json_missing(self, tmp_path):
        result = load_json(tmp_path / "missing.json")
        assert result is None

    def test_load_json_invalid(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not json")
        result = load_json(f)
        assert result is None

    def test_load_text_valid(self, tmp_path):
        f = tmp_path / "text.md"
        f.write_text("  Hello World  \n")
        result = load_text(f)
        assert result == "Hello World"

    def test_load_text_missing(self, tmp_path):
        result = load_text(tmp_path / "missing.md")
        assert result is None


# ---------------------------------------------------------------------------
# build_peers_list
# ---------------------------------------------------------------------------

class TestBuildPeersList:
    def test_basic_peers(self):
        peers_data = {
            "symbol": ["MSFT", "GOOG"],
            "name": ["Microsoft", "Alphabet"],
            "price": [400.50, 170.25],
            "market_cap": [3_000_000_000_000, 2_100_000_000_000],
        }
        result = build_peers_list(peers_data)
        assert len(result) == 2
        assert result[0]["symbol"] == "MSFT"
        assert result[0]["name"] == "Microsoft"
        assert result[0]["price"] == "400.50"
        assert result[0]["market_cap"] == "3.00T"

    def test_empty_peers_data(self):
        assert build_peers_list({}) == []
        assert build_peers_list(None) == []

    def test_missing_symbol_key(self):
        assert build_peers_list({"name": ["MSFT"]}) == []


# ---------------------------------------------------------------------------
# build_technical_context
# ---------------------------------------------------------------------------

class TestBuildTechnicalContext:
    def test_basic(self):
        tech_data = {
            "close": 150.25,
            "indicators": {
                "sma_20": 148.0,
                "sma_50": 145.0,
                "sma_200": 140.0,
                "rsi": 55.0,
                "macd": 2.5,
                "atr": 3.2,
                "volume_avg_20d": 1_000_000,
            },
            "trend_signals": {
                "above_sma20": True,
                "above_sma50": True,
                "above_sma200": True,
                "macd_bullish": True,
                "golden_cross": True,
            },
        }
        result = build_technical_context(tech_data)
        assert result is not None
        assert result["latest_price"] == 150.25
        assert result["indicators"]["rsi_14"] == 55.0
        assert result["trend_signals"]["above_200sma"] is True

    def test_none_returns_none(self):
        assert build_technical_context(None) is None

    def test_empty_dict_returns_none(self):
        assert build_technical_context({}) is None


# ---------------------------------------------------------------------------
# assemble_text.py main (subprocess)
# ---------------------------------------------------------------------------

class TestAssembleText:
    def test_assembles_sections(self, tmp_path):
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        (artifacts / "intro.md").write_text("# Introduction\n\nThis is the intro.")
        (artifacts / "assembled_body.md").write_text("# Body\n\nMain analysis here.")
        (artifacts / "conclusion.md").write_text("# Conclusion\n\nFinal thoughts.")

        script = str(Path(__file__).resolve().parent.parent / "skills" / "assemble_text.py")
        result = subprocess.run(
            ["uv", "run", "python", script, "TEST", "--workdir", str(tmp_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        manifest = json.loads(result.stdout)
        assert manifest["status"] == "complete"

        output = (artifacts / "report_body.md").read_text()
        assert "Introduction" in output
        assert "Main analysis" in output
        assert "Final thoughts" in output

    def test_fails_on_missing_section(self, tmp_path):
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        (artifacts / "intro.md").write_text("# Intro")
        # Missing body and conclusion

        script = str(Path(__file__).resolve().parent.parent / "skills" / "assemble_text.py")
        result = subprocess.run(
            ["uv", "run", "python", script, "TEST", "--workdir", str(tmp_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 1
