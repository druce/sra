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
    CHART_SCALE,
)
from utils import setup_logging, validate_symbol, ensure_directory, default_workdir  # noqa: E402


# ---------------------------------------------------------------------------
# Logging — all output to stderr
# ---------------------------------------------------------------------------
logger = setup_logging(__name__)


# ---------------------------------------------------------------------------
# Helper: read company name from profile.json (for Sankey title)
# ---------------------------------------------------------------------------
def _read_company_name(workdir: Path, symbol: str) -> str:
    """Try to read company name from profile.json; fall back to symbol."""
    profile_path = workdir / "artifacts" / "profile.json"
    if profile_path.exists():
        try:
            with profile_path.open("r") as f:
                data = json.load(f)
            name = data.get("company_name") or data.get(
                "longName") or data.get("shortName")
            if name:
                return name
        except Exception:
            pass
    return symbol


# ===================================================================
# 1. Financial Statements
# ===================================================================

def save_income_statement_sankey(
    income_stmt: pd.DataFrame,
    output_dir: Path,
    symbol: str,
    company_name: str = "",
) -> bool:
    """
    Create a Sankey chart visualizing income statement flow.

    Revenue
    - Cost of Revenue        = Gross Profit
    - SG&A / R&D / Other OpEx = Operating Income
    - Interest Expense
    +/- Other Income/Expense  = Pretax Income
    - Taxes                   = Net Income

    Uses plotly to build an interactive HTML chart and a static PNG.
    Returns True on success, False otherwise.
    """
    try:
        import plotly.graph_objects as go
    except ImportError:
        logger.warning("  [FAIL] plotly not installed — skipping Sankey chart")
        return False

    if income_stmt is None or income_stmt.empty:
        logger.warning("  [FAIL] No income statement data for Sankey chart")
        return False

    # Use the most recent period with valid revenue data
    # yfinance sometimes returns an incomplete future fiscal year as column 0
    latest = None
    revenue_keys = ["Total Revenue", "TotalRevenue"]
    for col_idx in range(income_stmt.shape[1]):
        col = income_stmt.iloc[:, col_idx]
        for rk in revenue_keys:
            if rk in col.index and pd.notna(col.get(rk)) and float(col[rk]) > 0:
                latest = col
                break
        if latest is not None:
            break
    if latest is None:
        logger.warning("  [FAIL] No column with valid revenue data for Sankey chart")
        return False

    def _val(key: str) -> float:
        """Safely extract a numeric value from the latest period."""
        if key in latest.index:
            v = latest[key]
            if v is not None and pd.notna(v):
                try:
                    return float(v)
                except (ValueError, TypeError):
                    return 0.0
        return 0.0

    # Extract key line items — try multiple naming conventions
    total_revenue = _val("Total Revenue") or _val("TotalRevenue")
    cost_of_revenue = _val("Cost Of Revenue") or _val("CostOfRevenue")
    gross_profit = _val("Gross Profit") or _val("GrossProfit")

    operating_expense = _val("Operating Expense") or _val("OperatingExpense")
    selling_ga = _val("Selling General And Administration") or _val(
        "SellingGeneralAndAdministration")
    research_dev = _val("Research And Development") or _val(
        "ResearchAndDevelopment") or _val("Research Development")
    other_operating = _val("Other Operating Expenses") or _val(
        "OtherOperatingExpenses")

    operating_income = _val("Operating Income") or _val(
        "OperatingIncome") or _val("EBIT")

    interest_expense = abs(_val("Interest Expense") or _val("InterestExpense") or _val(
        "Interest Expense Non Operating") or _val("InterestExpenseNonOperating"))
    tax_provision = abs(_val("Tax Provision") or _val("TaxProvision") or _val(
        "Income Tax Expense") or _val("IncomeTaxExpense"))
    other_income = _val("Other Income Expense") or _val(
        "OtherIncomeExpense") or _val("Other Non Operating Income Expenses")

    net_income = _val("Net Income") or _val("NetIncome") or _val(
        "Net Income Common Stockholders") or _val("NetIncomeCommonStockholders")
    pretax_income = _val("Pretax Income") or _val("PretaxIncome")

    # If we don't have revenue, we can't draw anything meaningful
    if total_revenue == 0:
        logger.warning(
            "  [FAIL] Revenue is zero or missing — skipping Sankey chart")
        return False

    # Derive values if missing
    if gross_profit == 0 and total_revenue > 0 and cost_of_revenue > 0:
        gross_profit = total_revenue - cost_of_revenue

    if cost_of_revenue == 0 and total_revenue > 0 and gross_profit > 0:
        cost_of_revenue = total_revenue - gross_profit

    # Calculate SG&A remainder if we have individual components
    if operating_expense == 0 and (selling_ga > 0 or research_dev > 0):
        operating_expense = selling_ga + research_dev + other_operating

    # If operating_income is missing, derive it
    if operating_income == 0 and gross_profit > 0:
        operating_income = gross_profit - operating_expense

    # Build Sankey nodes and links
    # Nodes: Revenue -> Cost of Revenue + Gross Profit
    #        Gross Profit -> Operating Expenses + Operating Income
    #        Operating Income -> Interest + Other Expense + Pretax Income
    #        Other Income (if positive) -> Pretax Income
    #        Pretax Income -> Taxes + Net Income
    nodes = []
    node_colors = []
    links_source = []
    links_target = []
    links_value = []
    links_color = []

    # Node color constants
    CLR_BLUE = "rgba(31,119,180,0.8)"      # Revenue
    CLR_GREEN = "rgba(80,180,80,0.8)"       # Profit / Income
    CLR_ORANGE = "rgba(230,150,80,0.8)"     # Expenses
    CLR_RED = "rgba(220,50,50,0.8)"         # Loss
    CLR_TEAL = "rgba(80,180,160,0.8)"       # Other income
    CLR_GRAY = "rgba(150,150,150,0.6)"      # Default

    def _fmt(v: float) -> str:
        """Format a dollar value for labels."""
        av = abs(v)
        if av >= 1e9:
            return f"${av / 1e9:.1f}B"
        elif av >= 1e6:
            return f"${av / 1e6:.1f}M"
        elif av >= 1e3:
            return f"${av / 1e3:.1f}K"
        return f"${av:.0f}"

    def add_node(name: str, color: str = CLR_GRAY) -> int:
        idx = len(nodes)
        nodes.append(name)
        node_colors.append(color)
        return idx

    def add_link(src: int, tgt: int, value: float, color: str = "rgba(100,100,100,0.3)"):
        if value > 0:
            links_source.append(src)
            links_target.append(tgt)
            links_value.append(value)
            links_color.append(color)

    # Node indices
    n_revenue = add_node(f"Revenue {_fmt(total_revenue)}", CLR_BLUE)
    n_cogs = add_node(f"Cost of Revenue {_fmt(cost_of_revenue)}", CLR_ORANGE)
    n_gross = add_node(f"Gross Profit {_fmt(gross_profit)}", CLR_GREEN)

    # Revenue -> COGS + Gross Profit
    add_link(n_revenue, n_cogs, cost_of_revenue, "rgba(255,100,100,0.4)")
    add_link(n_revenue, n_gross, gross_profit, "rgba(100,180,100,0.4)")

    # Gross Profit breakdown
    if selling_ga > 0 or research_dev > 0 or other_operating > 0:
        if selling_ga > 0:
            n_sga = add_node(f"SG&A {_fmt(selling_ga)}", CLR_ORANGE)
            add_link(n_gross, n_sga, selling_ga, "rgba(255,150,100,0.4)")
        if research_dev > 0:
            n_rd = add_node(f"R&D {_fmt(research_dev)}", CLR_ORANGE)
            add_link(n_gross, n_rd, research_dev, "rgba(255,180,100,0.4)")
        if other_operating > 0:
            n_other_op = add_node(f"Other OpEx {_fmt(other_operating)}", CLR_ORANGE)
            add_link(n_gross, n_other_op, other_operating,
                     "rgba(255,200,100,0.4)")
    elif operating_expense > 0:
        n_opex = add_node(f"Operating Expenses {_fmt(operating_expense)}", CLR_ORANGE)
        add_link(n_gross, n_opex, operating_expense, "rgba(255,150,100,0.4)")

    if operating_income > 0:
        n_opinc = add_node(f"Operating Income {_fmt(operating_income)}", CLR_GREEN)
        add_link(n_gross, n_opinc, operating_income, "rgba(100,200,100,0.4)")

        # Operating Income -> Interest + Other Expense + Pretax Income
        if interest_expense > 0:
            n_interest = add_node(f"Interest {_fmt(interest_expense)}", CLR_ORANGE)
            add_link(n_opinc, n_interest, interest_expense,
                     "rgba(255,120,120,0.4)")

        if other_income < 0:
            n_other_exp = add_node(f"Other Expense {_fmt(other_income)}", CLR_ORANGE)
            add_link(n_opinc, n_other_exp, abs(other_income),
                     "rgba(255,140,140,0.4)")

        # Derive pretax_income if missing
        if pretax_income == 0 and operating_income > 0:
            pretax_income = operating_income - interest_expense + other_income

        if pretax_income > 0:
            n_pretax = add_node(f"Pretax Income {_fmt(pretax_income)}", CLR_GREEN)
            add_link(n_opinc, n_pretax, pretax_income, "rgba(100,180,140,0.4)")

            # Other income (positive) flows into pretax as additional source
            if other_income > 0:
                n_other_inc = add_node(f"Other Income {_fmt(other_income)}", CLR_TEAL)
                add_link(n_other_inc, n_pretax, other_income,
                         "rgba(100,200,180,0.4)")

            # Pretax Income -> Taxes + Net Income
            if tax_provision > 0:
                n_tax = add_node(f"Taxes {_fmt(tax_provision)}", CLR_ORANGE)
                add_link(n_pretax, n_tax, tax_provision,
                         "rgba(255,160,100,0.4)")

            if net_income > 0:
                n_net = add_node(f"Net Income {_fmt(net_income)}", CLR_GREEN)
                add_link(n_pretax, n_net, net_income, "rgba(50,180,50,0.5)")
            elif net_income < 0:
                n_net = add_node(f"Net Loss {_fmt(net_income)}", CLR_RED)
                remaining = pretax_income - tax_provision
                if remaining > 0:
                    add_link(n_pretax, n_net, remaining,
                             "rgba(220,50,50,0.5)")
            else:
                remaining = pretax_income - tax_provision
                if remaining > 0:
                    n_net = add_node(f"Net Income {_fmt(remaining)}", CLR_GREEN)
                    add_link(n_pretax, n_net, remaining,
                             "rgba(50,180,50,0.5)")
        elif pretax_income < 0:
            # Pretax loss — show as red river
            abs_pt = abs(pretax_income)
            n_pretax = add_node(f"Pretax Loss {_fmt(pretax_income)}", CLR_RED)
            remaining = operating_income - interest_expense
            if other_income < 0:
                remaining -= abs(other_income)
            if remaining > 0:
                add_link(n_opinc, n_pretax, remaining,
                         "rgba(255,100,100,0.4)")
            # Show net loss from pretax loss
            if net_income < 0:
                abs_ni = abs(net_income)
                n_netloss = add_node(f"Net Loss {_fmt(net_income)}", CLR_RED)
                if tax_provision > 0:
                    n_tax = add_node(f"Taxes {_fmt(tax_provision)}", CLR_ORANGE)
                    add_link(n_pretax, n_tax, tax_provision,
                             "rgba(255,160,100,0.4)")
                add_link(n_pretax, n_netloss, abs_ni, "rgba(200,30,30,0.6)")

    elif operating_income < 0:
        # Operating loss — show as red river from Gross Profit
        abs_oi = abs(operating_income)
        n_oploss = add_node(f"Operating Loss {_fmt(operating_income)}", CLR_RED)
        add_link(n_gross, n_oploss, abs_oi, "rgba(220,50,50,0.5)")

        if net_income < 0:
            abs_ni = abs(net_income)
            n_netloss = add_node(f"Net Loss {_fmt(net_income)}", CLR_RED)

            if abs_oi > abs_ni + 1e6:
                # Non-operating items (interest income, etc.) reduced the loss
                offset = abs_oi - abs_ni
                n_offset = add_node(
                    f"Interest/Other Income {_fmt(offset)}", CLR_TEAL)
                add_link(n_oploss, n_offset, offset,
                         "rgba(100,200,180,0.4)")
                add_link(n_oploss, n_netloss, abs_ni,
                         "rgba(200,30,30,0.6)")
            elif abs_ni > abs_oi + 1e6:
                # Non-operating items (interest expense, etc.) worsened the loss
                additional = abs_ni - abs_oi
                add_link(n_oploss, n_netloss, abs_oi,
                         "rgba(200,30,30,0.6)")
                n_extra = add_node(
                    f"Interest/Other Charges {_fmt(additional)}", CLR_ORANGE)
                add_link(n_extra, n_netloss, additional,
                         "rgba(255,100,100,0.4)")
            else:
                # Approximately equal
                add_link(n_oploss, n_netloss, abs_ni,
                         "rgba(200,30,30,0.6)")
        elif net_income > 0:
            # Operating loss but net profit (large non-operating income)
            n_netinc = add_node(f"Net Income {_fmt(net_income)}", CLR_GREEN)
            total_recovery = abs_oi + net_income
            n_recovery = add_node(
                f"Interest/Other Income {_fmt(total_recovery)}", CLR_TEAL)
            add_link(n_recovery, n_netinc, net_income,
                     "rgba(50,180,50,0.5)")
            # Operating loss absorbed by non-operating income
            add_link(n_oploss, n_recovery, abs_oi,
                     "rgba(100,200,180,0.4)")

    # Guard: if no links were created, bail out
    if not links_source:
        logger.warning(
            "  [FAIL] Could not build Sankey links — data too sparse")
        return False

    title_name = company_name or symbol
    period_label = ""
    try:
        col_name = income_stmt.columns[0]
        if hasattr(col_name, "strftime"):
            period_label = f" (FY ending {col_name.strftime('%Y-%m-%d')})"
        else:
            period_label = f" ({col_name})"
    except Exception:
        pass

    fig = go.Figure(data=[go.Sankey(
        node=dict(
            pad=20,
            thickness=25,
            line=dict(color="black", width=0.5),
            label=nodes,
            color=node_colors,
        ),
        link=dict(
            source=links_source,
            target=links_target,
            value=links_value,
            color=links_color,
        ),
    )])

    fig.update_layout(
        title_text=f"{title_name} — Income Statement Flow{period_label}",
        font_size=12,
        width=900,
        height=500,
        margin=dict(l=20, r=20, t=60, b=20),
    )

    # Save HTML
    html_path = output_dir / "income_statement_sankey.html"
    fig.write_html(str(html_path))
    logger.info(f"  [ok] Sankey HTML: {html_path}")

    # Save PNG (requires kaleido)
    png_path = output_dir / "income_statement_sankey.png"
    try:
        fig.write_image(str(png_path), scale=CHART_SCALE)
        logger.info(f"  [ok] Sankey PNG:  {png_path}")
    except Exception as exc:
        logger.warning(f"  [FAIL] PNG export failed (install kaleido): {exc}")
        # HTML was still saved, so we return True

    return True


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
            company_name = _read_company_name(work_dir, symbol)
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
        p = artifacts_dir / "income_statement_sankey.png"
        if p.exists():
            artifacts.append({
                "name": "income_statement_sankey",
                "path": "artifacts/income_statement_sankey.png",
                "format": "png",
                "source": "yfinance+plotly",
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
