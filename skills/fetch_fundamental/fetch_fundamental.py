#!/usr/bin/env python3
"""
Fundamental Analysis Skill — financial statements, ratios, recommendations, news.

Fetches fundamental data from yfinance for a given ticker symbol and saves
artifacts into {workdir}/artifacts/.

Usage:
    ./skills/fetch_fundamental/fetch_fundamental.py SYMBOL --workdir DIR [--peers-file PATH]

Output:
    - income_statement.csv           Income statement data
    - income_statement_sankey.html   Interactive Sankey chart (income flow)
    - income_statement_sankey.png    Static Sankey chart (income flow)
    - balance_sheet.csv              Balance sheet data
    - cash_flow.csv                  Cash flow statement
    - key_ratios.csv                 Key financial ratios
    - analyst_recommendations.json   Analyst consensus and recommendations
    - news.json                      Recent news articles

Exit codes:
    0  All tasks succeeded
    1  Partial success (some tasks failed)
    2  Nothing produced (all tasks failed)

Stdout: JSON manifest of produced artifacts
Stderr: All progress/diagnostic output
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

# Add skills directory to path for local imports
_SKILLS_DIR = Path(__file__).resolve().parent.parent
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

from config import (  # noqa: E402
    MAX_ANALYST_RECOMMENDATIONS,
    MAX_NEWS_ARTICLES,
)
from utils import (  # noqa: E402
    setup_logging, validate_symbol, ensure_directory, default_workdir,
    resolve_company_name, load_environment,
)

load_environment()
from fetch_fundamental.sankey import save_income_statement_sankey  # noqa: E402


# ---------------------------------------------------------------------------
# Logging — all output to stderr
# ---------------------------------------------------------------------------
logger = setup_logging(__name__)


def save_financial_statements(symbol: str, work_dir: Path) -> bool:
    """
    Fetch income statement, balance sheet, and cash flow from yfinance.

    Saves each as a CSV file and generates a Sankey diagram for the
    income statement.

    Returns True if at least the core CSVs were saved.
    """
    output_dir = ensure_directory(work_dir / "artifacts")
    logger.info(f"Fetching financial statements for {symbol}...")

    try:
        ticker = yf.Ticker(symbol)

        # Income Statement
        income_stmt = ticker.income_stmt
        if income_stmt is not None and not income_stmt.empty:
            path = output_dir / "income_statement.csv"
            income_stmt.to_csv(path)
            logger.info(
                f"  [ok] Income statement: {income_stmt.shape[0]} rows x {income_stmt.shape[1]} periods")
        else:
            logger.warning("  [FAIL] Income statement: no data returned")
            income_stmt = None

        # Balance Sheet
        balance_sheet = ticker.balance_sheet
        if balance_sheet is not None and not balance_sheet.empty:
            path = output_dir / "balance_sheet.csv"
            balance_sheet.to_csv(path)
            logger.info(
                f"  [ok] Balance sheet: {balance_sheet.shape[0]} rows x {balance_sheet.shape[1]} periods")
        else:
            logger.warning("  [FAIL] Balance sheet: no data returned")

        # Cash Flow
        cash_flow = ticker.cashflow
        if cash_flow is not None and not cash_flow.empty:
            path = output_dir / "cash_flow.csv"
            cash_flow.to_csv(path)
            logger.info(
                f"  [ok] Cash flow: {cash_flow.shape[0]} rows x {cash_flow.shape[1]} periods")
        else:
            logger.warning("  [FAIL] Cash flow: no data returned")

        # Sankey visualization (best effort — don't fail the whole task)
        if income_stmt is not None:
            company_name = resolve_company_name(symbol, work_dir)
            save_income_statement_sankey(
                income_stmt, output_dir, symbol, company_name)

        return True

    except Exception as exc:
        logger.error(
            f"  [FAIL] Financial statements failed: {exc}", exc_info=True)
        return False


# ===================================================================
# 2. Financial Ratios
# ===================================================================

def get_financial_ratios(symbol: str) -> pd.DataFrame:
    """
    Compute comprehensive financial ratios from yfinance ticker.info.

    Returns a DataFrame with columns: Category, Metric, {symbol}.
    """
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
    except Exception as exc:
        logger.warning(f"  [FAIL] Could not fetch info for {symbol}: {exc}")
        return pd.DataFrame(columns=["Category", "Metric", symbol])

    def _get(key: str, fmt: str = "raw"):
        """Extract and optionally format a value from info dict."""
        val = info.get(key)
        if val is None:
            return "N/A"
        try:
            if fmt == "pct":
                return f"{float(val) * 100:.2f}%"
            elif fmt == "currency":
                return f"${float(val):,.2f}"
            elif fmt == "number":
                return f"{float(val):,.0f}"
            elif fmt == "ratio":
                return f"{float(val):.2f}"
            else:
                return val
        except (ValueError, TypeError):
            return val

    rows = [
        # Valuation
        ("Valuation", "Trailing P/E", _get("trailingPE", "ratio")),
        ("Valuation", "Forward P/E", _get("forwardPE", "ratio")),
        ("Valuation", "PEG Ratio", _get("pegRatio", "ratio")),
        ("Valuation", "Price/Sales (ttm)",
         _get("priceToSalesTrailing12Months", "ratio")),
        ("Valuation", "Price/Book", _get("priceToBook", "ratio")),
        ("Valuation", "EV/Revenue", _get("enterpriseToRevenue", "ratio")),
        ("Valuation", "EV/EBITDA", _get("enterpriseToEbitda", "ratio")),

        # Financial Highlights
        ("Financial Highlights", "Market Cap", _get("marketCap", "number")),
        ("Financial Highlights", "Enterprise Value",
         _get("enterpriseValue", "number")),
        ("Financial Highlights", "Revenue (ttm)", _get("totalRevenue", "number")),
        ("Financial Highlights", "EBITDA", _get("ebitda", "number")),
        ("Financial Highlights", "Net Income",
         _get("netIncomeToCommon", "number")),
        ("Financial Highlights", "Total Cash", _get("totalCash", "number")),
        ("Financial Highlights", "Total Debt", _get("totalDebt", "number")),
        ("Financial Highlights", "Revenue Growth (YoY)", _get("revenueGrowth", "pct")),
        ("Financial Highlights", "Earnings Growth (YoY)",
         _get("earningsGrowth", "pct")),

        # Profitability
        ("Profitability", "Gross Margin", _get("grossMargins", "pct")),
        ("Profitability", "Operating Margin", _get("operatingMargins", "pct")),
        ("Profitability", "Profit Margin", _get("profitMargins", "pct")),
        ("Profitability", "Return on Assets", _get("returnOnAssets", "pct")),
        ("Profitability", "Return on Equity", _get("returnOnEquity", "pct")),

        # Liquidity
        ("Liquidity", "Current Ratio", _get("currentRatio", "ratio")),
        ("Liquidity", "Quick Ratio", _get("quickRatio", "ratio")),
        ("Liquidity", "Debt/Equity", _get("debtToEquity", "ratio")),

        # Per Share
        ("Per Share", "EPS (ttm)", _get("trailingEps", "currency")),
        ("Per Share", "EPS (forward)", _get("forwardEps", "currency")),
        ("Per Share", "Book Value", _get("bookValue", "currency")),
        ("Per Share", "Revenue Per Share", _get("revenuePerShare", "currency")),
        ("Per Share", "Dividend Rate", _get("dividendRate", "currency")),
    ]

    df = pd.DataFrame(rows, columns=["Category", "Metric", symbol])
    return df


def save_key_ratios(symbol: str, work_dir: Path, peers_file: str = None) -> bool:
    """
    Get ratios for the ticker and its peers, then save a combined CSV.

    Peers are read from peers_file (JSON with a "symbol" list).
    """
    output_dir = ensure_directory(work_dir / "artifacts")
    logger.info(f"Computing key ratios for {symbol}...")

    try:
        # Main ticker ratios
        df = get_financial_ratios(symbol)
        symbols_done = [symbol]

        # Read peers
        peers = []
        if peers_file is None:
            peers_file = str(work_dir / "artifacts" / "peers_list.json")

        pf = Path(peers_file)
        if not pf.is_absolute() and not pf.exists():
            pf = work_dir / pf
        if pf.exists():
            try:
                with pf.open("r") as f:
                    peers_data = json.load(f)
                # Accept both {"symbol": [...]} and {"peers": [...]} and [...]
                if isinstance(peers_data, list):
                    peers = peers_data
                elif isinstance(peers_data, dict):
                    peers = peers_data.get("symbol", peers_data.get(
                        "peers", peers_data.get("symbols", [])))
                logger.info(f"  Found {len(peers)} peers in {pf.name}")
            except Exception as exc:
                logger.warning(f"  [FAIL] Could not read peers file: {exc}")
        else:
            logger.info(f"  No peers file at {pf} — ratios for {symbol} only")

        # Fetch peer ratios and merge
        for peer in peers:
            peer = str(peer).strip().upper()
            if not peer or peer == symbol:
                continue
            try:
                peer_df = get_financial_ratios(peer)
                if not peer_df.empty:
                    df = df.merge(
                        peer_df[["Category", "Metric", peer]],
                        on=["Category", "Metric"],
                        how="left",
                    )
                    symbols_done.append(peer)
            except Exception as exc:
                logger.warning(f"  [FAIL] Peer {peer} ratios failed: {exc}")

        path = output_dir / "key_ratios.csv"
        df.to_csv(path, index=False)
        logger.info(
            f"  [ok] Key ratios: {len(df)} metrics for {len(symbols_done)} ticker(s)")
        return True

    except Exception as exc:
        logger.error(f"  [FAIL] Key ratios failed: {exc}", exc_info=True)
        return False


# ===================================================================
# 3. Analyst Recommendations
# ===================================================================

def save_analyst_recommendations(symbol: str, work_dir: Path) -> bool:
    """
    Fetch analyst recommendations from yfinance and save as JSON.

    Limits to MAX_ANALYST_RECOMMENDATIONS most recent entries.
    """
    output_dir = ensure_directory(work_dir / "artifacts")
    logger.info(f"Fetching analyst recommendations for {symbol}...")

    try:
        ticker = yf.Ticker(symbol)
        recs = ticker.recommendations

        if recs is None or (hasattr(recs, "empty") and recs.empty):
            logger.warning("  [FAIL] No analyst recommendations available")
            return False

        # Convert DataFrame to list of dicts, limit count
        if isinstance(recs, pd.DataFrame):
            # Reset index to get date as a column if it's the index
            recs_reset = recs.reset_index()
            recs_list = recs_reset.head(
                MAX_ANALYST_RECOMMENDATIONS).to_dict(orient="records")
        else:
            recs_list = list(recs)[:MAX_ANALYST_RECOMMENDATIONS]

        # Ensure JSON-serializable (convert Timestamps, etc.)
        clean_recs = []
        for rec in recs_list:
            clean = {}
            for k, v in rec.items():
                if hasattr(v, "isoformat"):
                    clean[k] = v.isoformat()
                elif pd.isna(v) if isinstance(v, (float, int)) else False:
                    clean[k] = None
                else:
                    try:
                        json.dumps(v)
                        clean[k] = v
                    except (TypeError, ValueError):
                        clean[k] = str(v)
            clean_recs.append(clean)

        path = output_dir / "analyst_recommendations.json"
        with path.open("w") as f:
            json.dump(clean_recs, f, indent=2, default=str)

        logger.info(
            f"  [ok] Analyst recommendations: {len(clean_recs)} entries")
        return True

    except Exception as exc:
        logger.error(
            f"  [FAIL] Analyst recommendations failed: {exc}", exc_info=True)
        return False


# ===================================================================
# 4. News
# ===================================================================

def save_news(symbol: str, work_dir: Path) -> bool:
    """
    Fetch recent news from yfinance and save as JSON.

    Limits to MAX_NEWS_ARTICLES entries.
    """
    output_dir = ensure_directory(work_dir / "artifacts")
    logger.info(f"Fetching news for {symbol}...")

    try:
        ticker = yf.Ticker(symbol)
        news = ticker.news

        if not news:
            logger.warning("  [FAIL] No news articles available")
            return False

        # Limit to configured maximum
        news_limited = news[:MAX_NEWS_ARTICLES]

        # Clean for JSON serialization
        clean_news = []
        for article in news_limited:
            if isinstance(article, dict):
                clean = {}
                for k, v in article.items():
                    if hasattr(v, "isoformat"):
                        clean[k] = v.isoformat()
                    else:
                        try:
                            json.dumps(v)
                            clean[k] = v
                        except (TypeError, ValueError):
                            clean[k] = str(v)
                clean_news.append(clean)
            else:
                clean_news.append(str(article))

        path = output_dir / "news.json"
        with path.open("w") as f:
            json.dump(clean_news, f, indent=2, default=str)

        logger.info(f"  [ok] News: {len(clean_news)} articles")
        return True

    except Exception as exc:
        logger.error(f"  [FAIL] News fetch failed: {exc}", exc_info=True)
        return False


# ===================================================================
# Manifest builder
# ===================================================================

def _build_manifest(symbol: str, work_dir: Path, task_results: dict) -> dict:
    """
    Build the JSON manifest from task results.

    Only includes artifacts that actually exist on disk.
    """
    artifacts_dir = work_dir / "artifacts"
    artifacts = []

    # Financial statements
    if task_results.get("financial_statements"):
        # Income statement
        p = artifacts_dir / "income_statement.csv"
        if p.exists():
            try:
                df = pd.read_csv(p)
                cols = df.shape[1] - 1  # exclude index column
                summary = f"{cols} years of annual income data"
            except Exception:
                summary = "Annual income statement data"
            artifacts.append({
                "name": "income_statement",
                "path": "artifacts/income_statement.csv",
                "format": "csv",
                "source": "yfinance",
                "summary": summary,
            })

        # Sankey PNG
        sankey_desc = "Income statement Sankey chart: revenue flow through expenses to net income"
        p = artifacts_dir / "income_statement_sankey.png"
        if p.exists():
            artifacts.append({
                "name": "income_statement_sankey",
                "path": "artifacts/income_statement_sankey.png",
                "format": "png",
                "source": "yfinance+plotly",
                "description": sankey_desc,
                "summary": "Revenue flow to net income",
            })
        else:
            # Fall back to HTML if PNG failed
            p = artifacts_dir / "income_statement_sankey.html"
            if p.exists():
                artifacts.append({
                    "name": "income_statement_sankey",
                    "path": "artifacts/income_statement_sankey.html",
                    "format": "html",
                    "source": "yfinance+plotly",
                    "description": sankey_desc,
                    "summary": "Revenue flow to net income",
                })

        # Balance sheet
        p = artifacts_dir / "balance_sheet.csv"
        if p.exists():
            try:
                df = pd.read_csv(p)
                cols = df.shape[1] - 1
                summary = f"{cols} years of annual balance sheet data"
            except Exception:
                summary = "Annual balance sheet data"
            artifacts.append({
                "name": "balance_sheet",
                "path": "artifacts/balance_sheet.csv",
                "format": "csv",
                "source": "yfinance",
                "summary": summary,
            })

        # Cash flow
        p = artifacts_dir / "cash_flow.csv"
        if p.exists():
            try:
                df = pd.read_csv(p)
                cols = df.shape[1] - 1
                summary = f"{cols} years of annual cash flow data"
            except Exception:
                summary = "Annual cash flow data"
            artifacts.append({
                "name": "cash_flow",
                "path": "artifacts/cash_flow.csv",
                "format": "csv",
                "source": "yfinance",
                "summary": summary,
            })

    # Key ratios
    if task_results.get("key_ratios"):
        p = artifacts_dir / "key_ratios.csv"
        if p.exists():
            try:
                df = pd.read_csv(p)
                n_metrics = len(df)
                # Count ticker columns (everything after Category and Metric)
                n_tickers = df.shape[1] - 2
                ticker_label = f"{symbol}"
                if n_tickers > 1:
                    ticker_label += f" + {n_tickers - 1} peers"
                summary = f"{n_metrics} ratios for {ticker_label}"
            except Exception:
                summary = "Financial ratios"
            artifacts.append({
                "name": "key_ratios",
                "path": "artifacts/key_ratios.csv",
                "format": "csv",
                "source": "yfinance",
                "summary": summary,
            })

    # Analyst recommendations
    if task_results.get("analyst_recommendations"):
        p = artifacts_dir / "analyst_recommendations.json"
        if p.exists():
            try:
                with p.open("r") as f:
                    data = json.load(f)
                summary = f"{len(data)} recent analyst actions"
            except Exception:
                summary = "Analyst recommendations"
            artifacts.append({
                "name": "analyst_recommendations",
                "path": "artifacts/analyst_recommendations.json",
                "format": "json",
                "source": "yfinance",
                "summary": summary,
            })

    # News
    if task_results.get("news"):
        p = artifacts_dir / "news.json"
        if p.exists():
            try:
                with p.open("r") as f:
                    data = json.load(f)
                summary = f"{len(data)} recent news articles"
            except Exception:
                summary = "Recent news articles"
            artifacts.append({
                "name": "news",
                "path": "artifacts/news.json",
                "format": "json",
                "source": "yfinance",
                "description": f"Recent news for {symbol}",
                "summary": summary,
            })

    n_ok = sum(1 for v in task_results.values() if v)
    n_total = len(task_results)

    if n_ok == n_total:
        status = "complete"
    elif n_ok > 0:
        status = "partial"
    else:
        status = "failed"

    manifest = {
        "status": status,
        "artifacts": artifacts,
        "error": None if n_ok > 0 else "All fundamental analysis tasks failed",
    }

    return manifest


# ===================================================================
# CLI entry point
# ===================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fundamental analysis: financial statements, ratios, recommendations, news."
    )
    parser.add_argument("symbol", help="Stock ticker symbol (e.g. TSLA)")
    parser.add_argument("--workdir", default=None,
                        help="Working directory path (default: work/SYMBOL_YYYYMMDD)")
    parser.add_argument(
        "--peers-file",
        default=None,
        help="Path to peers JSON file (default: {workdir}/artifacts/peers_list.json)",
    )

    args = parser.parse_args()

    # Validate
    try:
        symbol = validate_symbol(args.symbol)
    except ValueError as exc:
        logger.error(f"Invalid symbol: {exc}")
        manifest = {"status": "failed", "artifacts": [], "error": str(exc)}
        print(json.dumps(manifest))
        return 2

    work_dir = Path(args.workdir or default_workdir(symbol))
    peers_file = args.peers_file

    # Ensure artifacts directory exists
    ensure_directory(work_dir / "artifacts")

    logger.info(f"{'=' * 60}")
    logger.info(f"Fundamental Analysis: {symbol}")
    logger.info(f"Work directory: {work_dir}")
    logger.info(f"{'=' * 60}")

    # Run 5 tasks (no company_overview — that's in research_profile.py)
    task_results = {}

    # Task 1: Financial statements (income, balance sheet, cash flow + Sankey)
    logger.info("\n[1/5] Financial Statements")
    task_results["financial_statements"] = save_financial_statements(
        symbol, work_dir)

    # Task 2: Key ratios
    logger.info("\n[2/5] Key Ratios")
    task_results["key_ratios"] = save_key_ratios(symbol, work_dir, peers_file)

    # Task 3: Analyst recommendations
    logger.info("\n[3/5] Analyst Recommendations")
    task_results["analyst_recommendations"] = save_analyst_recommendations(
        symbol, work_dir)

    # Task 4: News
    logger.info("\n[4/5] News")
    task_results["news"] = save_news(symbol, work_dir)

    # Task 5: (placeholder logged for consistency — Sankey is part of task 1)
    # The Sankey chart is produced inside save_financial_statements, so task 5
    # is effectively the combined financial-statements + sankey task above.
    # We log it for human readability.
    logger.info(
        "\n[5/5] Income Statement Sankey (produced with financial statements)")
    sankey_png = work_dir / "artifacts" / "income_statement_sankey.png"
    sankey_html = work_dir / "artifacts" / "income_statement_sankey.html"
    if sankey_png.exists() or sankey_html.exists():
        logger.info("  [ok] Sankey chart available")
    else:
        logger.warning("  [FAIL] Sankey chart not produced")

    # Summary
    n_ok = sum(1 for v in task_results.values() if v)
    n_total = len(task_results)
    logger.info(f"\n{'=' * 60}")
    logger.info(f"Results: {n_ok}/{n_total} tasks succeeded")
    logger.info(f"{'=' * 60}")

    # Build and emit manifest to stdout
    manifest = _build_manifest(symbol, work_dir, task_results)
    print(json.dumps(manifest, indent=2))

    # Exit code
    if n_ok == n_total:
        return 0
    elif n_ok > 0:
        return 1
    else:
        return 2


if __name__ == "__main__":
    sys.exit(main())
