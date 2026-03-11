#!/usr/bin/env python3
"""
Render the final equity research report from collected artifacts.

Loads profile, technical analysis, peers, ratios, and written report sections,
maps them to the variables expected by final_report.md.j2, and renders it.

Usage:
    ./skills/render_final.py --workdir work/AMD_20260225

Exit codes:
    0  success
    2  error
"""

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from jinja2 import Environment, FileSystemLoader, TemplateError

# Add skills directory to path for local imports
_SKILLS_DIR = Path(__file__).resolve().parent
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

from config import TEMPLATES_DIR  # noqa: E402
from utils import ensure_directory, format_market_cap, format_number, setup_logging  # noqa: E402

logger = setup_logging(__name__)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_json(path: Path) -> Optional[Dict[str, Any]]:
    """Load a JSON file, returning None if missing."""
    if not path.exists():
        logger.warning("JSON file not found: %s", path)
        return None
    with path.open("r") as f:
        return json.load(f)


def load_text(path: Path) -> Optional[str]:
    """Load a text file, returning None if missing."""
    if not path.exists():
        logger.warning("Text file not found: %s", path)
        return None
    with path.open("r") as f:
        return f.read()


def load_ratios_csv(path: Path) -> Dict[str, Dict[str, str]]:
    """
    Load key_ratios.csv into a nested dict keyed by ticker then metric.

    CSV format: Category,Metric,TICKER1,TICKER2,...
    Returns: {"HOOD": {"Trailing P/E": "37.00", ...}, "SCHW": {"Trailing P/E": "20.47", ...}}
    """
    if not path.exists():
        logger.warning("Ratios CSV not found: %s", path)
        return {}
    all_ratios: Dict[str, Dict[str, str]] = {}
    with path.open("r") as f:
        reader = csv.DictReader(f)
        ticker_cols = [k for k in (reader.fieldnames or []) if k not in ("Category", "Metric")]
        for col in ticker_cols:
            all_ratios[col] = {}
        for row in reader:
            metric = row.get("Metric", "")
            for col in ticker_cols:
                all_ratios[col][metric] = row.get(col, "N/A")
    return all_ratios


# ---------------------------------------------------------------------------
# Transformers
# ---------------------------------------------------------------------------

def transpose_peers(peers_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Transpose column-oriented peers JSON to list of row dicts.

    Input:  {"symbol": ["NVDA", "INTC"], "price": [195, 46], ...}
    Output: [{"symbol": "NVDA", "price": 195}, ...]
    """
    list_keys = [k for k, v in peers_json.items() if isinstance(v, list)]
    if not list_keys:
        return []

    n = len(peers_json[list_keys[0]])
    rows: List[Dict[str, Any]] = []
    for i in range(n):
        row = {}
        for key in list_keys:
            values = peers_json[key]
            row[key] = values[i] if i < len(values) else None
        rows.append(row)
    return rows


def map_technical(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Map technical_analysis.json keys to template-expected structure."""
    indicators = raw.get("indicators", {})
    signals = raw.get("trend_signals", {})

    return {
        "latest_price": raw.get("close"),
        "indicators": {
            "sma_20": indicators.get("sma_20"),
            "sma_50": indicators.get("sma_50"),
            "sma_200": indicators.get("sma_200"),
            "rsi_14": indicators.get("rsi"),
            "macd": indicators.get("macd"),
            "atr_14": indicators.get("atr"),
            "avg_volume_20d": indicators.get("volume_avg_20d"),
        },
        "trend_signals": {
            "above_20sma": signals.get("above_sma20"),
            "above_50sma": signals.get("above_sma50"),
            "above_200sma": signals.get("above_sma200"),
            "macd_bullish": signals.get("macd_bullish"),
            "sma_50_200_bullish": signals.get("golden_cross"),
        },
    }



def extract_ratio(ratios: Dict[str, str], metric: str) -> str:
    """Extract a ratio value, stripping trailing % if present."""
    val = ratios.get(metric, "N/A")
    if val == "N/A":
        return val
    return val.rstrip("%")


# ---------------------------------------------------------------------------
# Text cleanup
# ---------------------------------------------------------------------------

def strip_leading_header(text: str) -> str:
    """Remove leading markdown title and metadata lines before first ## section."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("## "):
            return "\n".join(lines[i:]).strip()
    return text


# ---------------------------------------------------------------------------
# Variable assembly
# ---------------------------------------------------------------------------

def build_variables(artifacts_dir: Path) -> Dict[str, Any]:
    """Load all artifacts and build the template variable dict."""
    variables: Dict[str, Any] = {}

    # --- Profile ---
    profile = load_json(artifacts_dir / "profile.json")
    if profile:
        variables["symbol"] = profile.get("symbol", "")
        variables["company_name"] = profile.get("company_name", "")
        variables["sector"] = profile.get("sector", "")
        variables["industry"] = profile.get("industry", "")
        raw_price = profile.get("current_price")
        variables["latest_price"] = f"${float(raw_price):.2f}" if raw_price else "N/A"
        variables["market_cap"] = format_market_cap(profile.get("market_cap"))
        variables["timestamp"] = profile.get("timestamp", "")

    # --- Charts (relative paths so they resolve from the artifacts dir) ---
    chart_path = artifacts_dir / "chart.png"
    variables["chart_path"] = "chart.png" if chart_path.exists() else None

    sankey_path = artifacts_dir / "income_statement_sankey.png"
    variables["income_statement_sankey_path"] = "income_statement_sankey.png" if sankey_path.exists() else None

    # --- Technical Analysis ---
    tech_raw = load_json(artifacts_dir / "technical_analysis.json")
    variables["technical_analysis"] = map_technical(tech_raw) if tech_raw else None

    # --- Key Ratios (all tickers) ---
    all_ratios = load_ratios_csv(artifacts_dir / "key_ratios.csv")
    symbol = variables.get("symbol", "")
    ratios = all_ratios.get(symbol, {})
    variables["trailing_pe"] = extract_ratio(ratios, "Trailing P/E")
    variables["forward_pe"] = extract_ratio(ratios, "Forward P/E")
    variables["profit_margin"] = extract_ratio(ratios, "Profit Margin")
    variables["roe"] = extract_ratio(ratios, "Return on Equity")
    variables["revenue"] = ratios.get("Revenue (ttm)", "N/A")

    # --- Peers (enriched with ratios from CSV) ---
    peers_raw = load_json(artifacts_dir / "peers_list.json")
    if peers_raw:
        peers = transpose_peers(peers_raw)
        for peer in peers:
            peer_sym = peer.get("symbol", "")
            peer_ratios = all_ratios.get(peer_sym, {})
            peer["pe_ratio"] = peer_ratios.get("Trailing P/E", "N/A")
            peer["revenue"] = peer_ratios.get("Revenue (ttm)", "N/A")
            peer["profit_margin"] = peer_ratios.get("Profit Margin", "N/A")
            peer["roe"] = peer_ratios.get("Return on Equity", "N/A")
            peer["market_cap"] = format_market_cap(peer.get("market_cap"))
        variables["peers"] = peers
    else:
        variables["peers"] = []

    # --- Written report sections ---
    variables["deep_research_output"] = (
        load_text(artifacts_dir / "report_body_final.md")
        or load_text(artifacts_dir / "report_body.md")
        or load_text(artifacts_dir / "assembled_body.md")
    )
    if variables.get("deep_research_output"):
        variables["deep_research_output"] = strip_leading_header(
            variables["deep_research_output"]
        )
    variables["deep_conclusion"] = load_text(
        artifacts_dir / "conclusion.md"
    )

    return variables


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render(template_path: Path, variables: Dict[str, Any]) -> str:
    """Render a Jinja2 template with custom filters and variables."""
    env = Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        keep_trailing_newline=True,
    )
    env.filters["format_number"] = format_number

    template = env.get_template(template_path.name)
    return template.render(**variables)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description="Render final equity research report from artifacts.",
    )
    parser.add_argument(
        "--workdir", required=True,
        help="Work directory (e.g. work/AMD_20260225)",
    )
    parser.add_argument(
        "--template", default=None,
        help="Template path (default: templates/final_report.md.j2)",
    )
    parser.add_argument(
        "--output", default=None,
        help="Output path (default: {workdir}/artifacts/final_report.md)",
    )
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()

    if args.verbose:
        import logging
        logging.getLogger().setLevel(logging.DEBUG)

    workdir = Path(args.workdir)
    artifacts_dir = workdir / "artifacts"

    if not artifacts_dir.exists():
        logger.error("Artifacts directory not found: %s", artifacts_dir)
        print(json.dumps({
            "status": "error", "artifacts": [],
            "error": f"Artifacts dir not found: {artifacts_dir}",
        }))
        return 2

    # Resolve template path
    project_root = Path(__file__).resolve().parent.parent
    template_path = (
        Path(args.template)
        if args.template
        else project_root / TEMPLATES_DIR / "final_report.md.j2"
    )
    if not template_path.exists():
        logger.error("Template not found: %s", template_path)
        print(json.dumps({
            "status": "error", "artifacts": [],
            "error": f"Template not found: {template_path}",
        }))
        return 2

    output_path = (
        Path(args.output) if args.output else artifacts_dir / "final_report.md"
    )

    # Build variables from artifacts
    logger.info("Loading artifacts from %s", artifacts_dir)
    variables = build_variables(artifacts_dir)
    logger.info("Loaded %d template variables", len(variables))

    # Render
    try:
        rendered = render(template_path, variables)
    except TemplateError as e:
        logger.error("Template rendering failed: %s", e)
        print(json.dumps({
            "status": "error", "artifacts": [],
            "error": f"Template error: {e}",
        }))
        return 2

    ensure_directory(output_path.parent)
    with output_path.open("w") as f:
        f.write(rendered)

    logger.info("✓ Final report written to %s", output_path)

    artifacts = [
        {"name": "final_report", "path": str(output_path), "format": "md"},
    ]

    # --- Convert to HTML via pandoc ---
    html_path = output_path.with_suffix(".html")
    title = f"{variables.get('company_name', '')} Equity Research Report"
    css_path = project_root / TEMPLATES_DIR / "report.css"
    pandoc_cmd = [
        "pandoc", output_path.name,
        "-o", html_path.name,
        "--standalone",
        "--metadata", f"title={title}",
    ]
    if css_path.exists():
        pandoc_cmd.extend(["--include-in-header", str(css_path)])
    try:
        subprocess.run(
            pandoc_cmd,
            check=True, capture_output=True, cwd=str(artifacts_dir),
        )
        logger.info("✓ HTML report written to %s", html_path)
        artifacts.append(
            {"name": "final_report_html", "path": str(html_path), "format": "html"})
    except FileNotFoundError:
        logger.warning("pandoc not found — skipping HTML/PDF conversion")
    except subprocess.CalledProcessError as e:
        logger.warning("HTML conversion failed: %s", e.stderr.decode()[:500])

    # --- Convert to PDF via weasyprint (from HTML) ---
    pdf_path = output_path.with_suffix(".pdf")
    if html_path.exists():
        try:
            # weasyprint uses cffi to load GLib/Pango; on macOS with Homebrew
            # the .dylib files live under /opt/homebrew/lib and may not be on
            # the default search path.
            import os
            _brew_lib = "/opt/homebrew/lib"
            if Path(_brew_lib).is_dir():
                os.environ.setdefault("DYLD_LIBRARY_PATH", _brew_lib)
                existing = os.environ["DYLD_LIBRARY_PATH"]
                if _brew_lib not in existing:
                    os.environ["DYLD_LIBRARY_PATH"] = f"{_brew_lib}:{existing}"
            from weasyprint import HTML
            HTML(filename=str(html_path), base_url=str(artifacts_dir)).write_pdf(str(pdf_path))
            logger.info("✓ PDF report written to %s", pdf_path)
            artifacts.append(
                {"name": "final_report_pdf", "path": str(pdf_path), "format": "pdf"})
        except Exception as e:
            logger.warning("PDF conversion failed: %s", e)

    manifest = {
        "status": "complete",
        "artifacts": artifacts,
        "error": None,
    }
    print(json.dumps(manifest))

    return 0


if __name__ == "__main__":
    sys.exit(main())
