#!/usr/bin/env python3
"""Final assembly — render Jinja2 template with all collected data into final report."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import jinja2

_SKILLS_DIR = Path(__file__).resolve().parent
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

from utils import format_market_cap, format_number, setup_logging

logger = setup_logging(__name__)



def load_json(path: Path) -> dict | list | None:
    """Load JSON file, return None on failure."""
    if not path.exists():
        logger.warning(f"File not found: {path}")
        return None
    try:
        return json.loads(path.read_text())
    except Exception as e:
        logger.warning(f"Failed to load {path}: {e}")
        return None


def load_text(path: Path) -> str | None:
    """Load text file, return None on failure."""
    if not path.exists():
        logger.warning(f"File not found: {path}")
        return None
    return path.read_text().strip()


def build_peers_list(peers_data: dict) -> list[dict]:
    """Build list of peer dicts for template."""
    if not peers_data or "symbol" not in peers_data:
        return []
    peers = []
    symbols = peers_data["symbol"]
    names = peers_data["name"]
    prices = peers_data["price"]
    market_caps = peers_data["market_cap"]
    for i in range(len(symbols)):
        peers.append({
            "symbol": symbols[i],
            "name": names[i],
            "price": f"{prices[i]:.2f}",
            "market_cap": format_market_cap(market_caps[i]),
            "pe_ratio": "N/A",
            "revenue": "N/A",
            "profit_margin": "N/A",
            "roe": "N/A",
        })
    return peers


def build_technical_context(tech_data: dict) -> dict | None:
    """Reshape technical analysis data for template compatibility."""
    if not tech_data:
        return None
    indicators = tech_data.get("indicators", {})
    signals = tech_data.get("trend_signals", {})
    return {
        "latest_price": tech_data.get("close", "N/A"),
        "indicators": {
            "sma_20": indicators.get("sma_20", 0),
            "sma_50": indicators.get("sma_50", 0),
            "sma_200": indicators.get("sma_200", 0),
            "rsi_14": indicators.get("rsi", 0),
            "macd": indicators.get("macd", 0),
            "atr_14": indicators.get("atr", 0),
            "avg_volume_20d": indicators.get("volume_avg_20d", 0),
        },
        "trend_signals": {
            "above_20sma": signals.get("above_sma20", False),
            "above_50sma": signals.get("above_sma50", False),
            "above_200sma": signals.get("above_sma200", False),
            "macd_bullish": signals.get("macd_bullish", False),
            "sma_50_200_bullish": signals.get("golden_cross", False),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Final report assembly via Jinja2")
    parser.add_argument("ticker", help="Ticker symbol")
    parser.add_argument("--workdir", required=True, help="Working directory")
    args = parser.parse_args()

    workdir = Path(args.workdir)
    artifacts = workdir / "artifacts"
    root_dir = Path(__file__).resolve().parent.parent
    template_dir = root_dir / "templates"

    # Load data
    profile = load_json(artifacts / "profile.json") or {}
    tech_data = load_json(artifacts / "technical_analysis.json")
    peers_data = load_json(artifacts / "peers_list.json")
    report_body = load_text(artifacts / "report_body_final.md")

    if not report_body:
        logger.error("No polished report body found")
        manifest = {"status": "failed", "artifacts": [], "error": "Missing report_body_final.md"}
        print(json.dumps(manifest, indent=2))
        return 1

    # Build template context
    technical_analysis = build_technical_context(tech_data)
    peers = build_peers_list(peers_data) if peers_data else []

    context = {
        "symbol": profile.get("symbol", args.ticker),
        "company_name": profile.get("company_name", args.ticker),
        "sector": profile.get("sector", "N/A"),
        "industry": profile.get("industry", "N/A"),
        "latest_price": f"${float(profile.get('current_price', 0)):.2f}" if profile.get('current_price') else "N/A",
        "market_cap": format_market_cap(profile.get("market_cap", 0)),
        "timestamp": datetime.now().strftime("%Y-%m-%d"),
        "technical_analysis": technical_analysis,
        "peers": peers,
        "trailing_pe": "N/A",
        "revenue": "N/A",
        "profit_margin": "N/A",
        "roe": "N/A",
        "chart_path": "chart.png",
        "income_statement_sankey_path": "income_statement_sankey.png",
        "deep_summary": None,
        "deep_research_output": report_body,
        "deep_conclusion": None,
    }

    # Render template
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(template_dir)),
        undefined=jinja2.Undefined,
    )
    env.filters["format_number"] = format_number

    template = env.get_template("final_report.md.j2")
    rendered = template.render(**context)

    output_path = artifacts / "final_report.md"
    output_path.write_text(rendered)
    logger.info(f"Final report written to {output_path} ({len(rendered)} chars)")

    manifest = {
        "status": "complete",
        "artifacts": [
            {
                "name": "final_report",
                "path": "artifacts/final_report.md",
                "format": "md",
                "source": "jinja2",
                "summary": f"Final formatted report ({len(rendered)} chars) with charts, tables, and analysis",
            }
        ],
        "error": None,
    }
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
