"""
Income statement Sankey chart generator.

Builds an interactive Plotly Sankey diagram showing how revenue flows through
cost-of-revenue, operating expenses, interest/taxes, and down to net income.
Handles both profitable and loss-making companies, and tolerates missing or
inconsistently-named line items across different yfinance data formats.

The chart is saved as both an interactive HTML file and a static PNG
(PNG requires the kaleido package).
"""

import logging
from pathlib import Path

import pandas as pd

from config import CHART_SCALE


logger = logging.getLogger(__name__)


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

    latest = _find_latest_period(income_stmt)
    if latest is None:
        logger.warning("  [FAIL] No column with valid revenue data for Sankey chart")
        return False

    items = _extract_line_items(latest)
    if items["total_revenue"] == 0:
        logger.warning(
            "  [FAIL] Revenue is zero or missing — skipping Sankey chart")
        return False

    _derive_missing_values(items)

    builder = _SankeyBuilder()
    _build_sankey_graph(builder, items)

    if not builder.links_source:
        logger.warning(
            "  [FAIL] Could not build Sankey links — data too sparse")
        return False

    period_label = _get_period_label(income_stmt)
    title_name = company_name or symbol
    fig = _create_figure(go, builder, title_name, period_label)

    return _save_figure(fig, output_dir)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_latest_period(income_stmt: pd.DataFrame):
    """Return the first column with valid revenue data, or None."""
    revenue_keys = ["Total Revenue", "TotalRevenue"]
    for col_idx in range(income_stmt.shape[1]):
        col = income_stmt.iloc[:, col_idx]
        for rk in revenue_keys:
            if rk in col.index and pd.notna(col.get(rk)) and float(col[rk]) > 0:
                return col
    return None


def _extract_line_items(latest) -> dict:
    """Pull all needed line items from the income statement period, trying multiple naming conventions."""
    def _val(key: str) -> float:
        if key in latest.index:
            v = latest[key]
            if v is not None and pd.notna(v):
                try:
                    return float(v)
                except (ValueError, TypeError):
                    return 0.0
        return 0.0

    def _first(*keys: str) -> float:
        """Return the first non-zero value across naming conventions."""
        for k in keys:
            v = _val(k)
            if v != 0.0:
                return v
        return 0.0

    return {
        "total_revenue": _first("Total Revenue", "TotalRevenue"),
        "cost_of_revenue": _first("Cost Of Revenue", "CostOfRevenue"),
        "gross_profit": _first("Gross Profit", "GrossProfit"),
        "operating_expense": _first("Operating Expense", "OperatingExpense"),
        "selling_ga": _first("Selling General And Administration", "SellingGeneralAndAdministration"),
        "research_dev": _first("Research And Development", "ResearchAndDevelopment", "Research Development"),
        "other_operating": _first("Other Operating Expenses", "OtherOperatingExpenses"),
        "operating_income": _first("Operating Income", "OperatingIncome", "EBIT"),
        "interest_expense": abs(_first("Interest Expense", "InterestExpense", "Interest Expense Non Operating", "InterestExpenseNonOperating")),
        "tax_provision": abs(_first("Tax Provision", "TaxProvision", "Income Tax Expense", "IncomeTaxExpense")),
        "other_income": _first("Other Income Expense", "OtherIncomeExpense", "Other Non Operating Income Expenses"),
        "net_income": _first("Net Income", "NetIncome", "Net Income Common Stockholders", "NetIncomeCommonStockholders"),
        "pretax_income": _first("Pretax Income", "PretaxIncome"),
    }


def _derive_missing_values(items: dict) -> None:
    """Fill in derived values when direct data is missing."""
    if items["gross_profit"] == 0 and items["total_revenue"] > 0 and items["cost_of_revenue"] > 0:
        items["gross_profit"] = items["total_revenue"] - items["cost_of_revenue"]

    if items["cost_of_revenue"] == 0 and items["total_revenue"] > 0 and items["gross_profit"] > 0:
        items["cost_of_revenue"] = items["total_revenue"] - items["gross_profit"]

    if items["operating_expense"] == 0 and (items["selling_ga"] > 0 or items["research_dev"] > 0):
        items["operating_expense"] = items["selling_ga"] + items["research_dev"] + items["other_operating"]

    if items["operating_income"] == 0 and items["gross_profit"] > 0:
        items["operating_income"] = items["gross_profit"] - items["operating_expense"]


class _SankeyBuilder:
    """Accumulates nodes and links for a Plotly Sankey diagram."""

    # Color constants
    CLR_BLUE = "rgba(31,119,180,0.8)"
    CLR_GREEN = "rgba(80,180,80,0.8)"
    CLR_ORANGE = "rgba(230,150,80,0.8)"
    CLR_RED = "rgba(220,50,50,0.8)"
    CLR_TEAL = "rgba(80,180,160,0.8)"
    CLR_GRAY = "rgba(150,150,150,0.6)"

    def __init__(self):
        self.nodes = []
        self.node_colors = []
        self.links_source = []
        self.links_target = []
        self.links_value = []
        self.links_color = []

    def add_node(self, name: str, color: str = None) -> int:
        idx = len(self.nodes)
        self.nodes.append(name)
        self.node_colors.append(color or self.CLR_GRAY)
        return idx

    def add_link(self, src: int, tgt: int, value: float, color: str = "rgba(100,100,100,0.3)"):
        if value > 0:
            self.links_source.append(src)
            self.links_target.append(tgt)
            self.links_value.append(value)
            self.links_color.append(color)


def _fmt(v: float) -> str:
    """Format a dollar value for node labels."""
    av = abs(v)
    if av >= 1e9:
        return f"${av / 1e9:.1f}B"
    elif av >= 1e6:
        return f"${av / 1e6:.1f}M"
    elif av >= 1e3:
        return f"${av / 1e3:.1f}K"
    return f"${av:.0f}"


def _build_sankey_graph(b: _SankeyBuilder, items: dict) -> None:
    """Populate the SankeyBuilder with nodes and links from extracted line items."""
    C = _SankeyBuilder  # shorthand for color constants

    n_revenue = b.add_node(f"Revenue {_fmt(items['total_revenue'])}", C.CLR_BLUE)
    n_cogs = b.add_node(f"Cost of Revenue {_fmt(items['cost_of_revenue'])}", C.CLR_ORANGE)
    n_gross = b.add_node(f"Gross Profit {_fmt(items['gross_profit'])}", C.CLR_GREEN)

    b.add_link(n_revenue, n_cogs, items["cost_of_revenue"], "rgba(255,100,100,0.4)")
    b.add_link(n_revenue, n_gross, items["gross_profit"], "rgba(100,180,100,0.4)")

    _add_operating_expense_links(b, n_gross, items)

    if items["operating_income"] > 0:
        _add_profitable_operating_path(b, n_gross, items)
    elif items["operating_income"] < 0:
        _add_loss_operating_path(b, n_gross, items)


def _add_operating_expense_links(b: _SankeyBuilder, n_gross: int, items: dict) -> None:
    """Add links from gross profit to operating expense breakdown."""
    C = _SankeyBuilder
    if items["selling_ga"] > 0 or items["research_dev"] > 0 or items["other_operating"] > 0:
        if items["selling_ga"] > 0:
            n_sga = b.add_node(f"SG&A {_fmt(items['selling_ga'])}", C.CLR_ORANGE)
            b.add_link(n_gross, n_sga, items["selling_ga"], "rgba(255,150,100,0.4)")
        if items["research_dev"] > 0:
            n_rd = b.add_node(f"R&D {_fmt(items['research_dev'])}", C.CLR_ORANGE)
            b.add_link(n_gross, n_rd, items["research_dev"], "rgba(255,180,100,0.4)")
        if items["other_operating"] > 0:
            n_other_op = b.add_node(f"Other OpEx {_fmt(items['other_operating'])}", C.CLR_ORANGE)
            b.add_link(n_gross, n_other_op, items["other_operating"], "rgba(255,200,100,0.4)")
    elif items["operating_expense"] > 0:
        n_opex = b.add_node(f"Operating Expenses {_fmt(items['operating_expense'])}", C.CLR_ORANGE)
        b.add_link(n_gross, n_opex, items["operating_expense"], "rgba(255,150,100,0.4)")


def _add_profitable_operating_path(b: _SankeyBuilder, n_gross: int, items: dict) -> None:
    """Build Sankey path when operating income is positive."""
    C = _SankeyBuilder
    oi = items["operating_income"]
    n_opinc = b.add_node(f"Operating Income {_fmt(oi)}", C.CLR_GREEN)
    b.add_link(n_gross, n_opinc, oi, "rgba(100,200,100,0.4)")

    if items["interest_expense"] > 0:
        n_interest = b.add_node(f"Interest {_fmt(items['interest_expense'])}", C.CLR_ORANGE)
        b.add_link(n_opinc, n_interest, items["interest_expense"], "rgba(255,120,120,0.4)")

    if items["other_income"] < 0:
        n_other_exp = b.add_node(f"Other Expense {_fmt(items['other_income'])}", C.CLR_ORANGE)
        b.add_link(n_opinc, n_other_exp, abs(items["other_income"]), "rgba(255,140,140,0.4)")

    # Derive pretax_income if missing
    pretax = items["pretax_income"]
    if pretax == 0 and oi > 0:
        pretax = oi - items["interest_expense"] + items["other_income"]

    if pretax > 0:
        _add_pretax_positive_path(b, n_opinc, pretax, items)
    elif pretax < 0:
        _add_pretax_negative_path(b, n_opinc, pretax, items)


def _add_pretax_positive_path(b: _SankeyBuilder, n_opinc: int, pretax: float, items: dict) -> None:
    """Add nodes/links for positive pretax income → taxes → net income."""
    C = _SankeyBuilder
    n_pretax = b.add_node(f"Pretax Income {_fmt(pretax)}", C.CLR_GREEN)
    b.add_link(n_opinc, n_pretax, pretax, "rgba(100,180,140,0.4)")

    if items["other_income"] > 0:
        n_other_inc = b.add_node(f"Other Income {_fmt(items['other_income'])}", C.CLR_TEAL)
        b.add_link(n_other_inc, n_pretax, items["other_income"], "rgba(100,200,180,0.4)")

    if items["tax_provision"] > 0:
        n_tax = b.add_node(f"Taxes {_fmt(items['tax_provision'])}", C.CLR_ORANGE)
        b.add_link(n_pretax, n_tax, items["tax_provision"], "rgba(255,160,100,0.4)")

    ni = items["net_income"]
    if ni > 0:
        n_net = b.add_node(f"Net Income {_fmt(ni)}", C.CLR_GREEN)
        b.add_link(n_pretax, n_net, ni, "rgba(50,180,50,0.5)")
    elif ni < 0:
        n_net = b.add_node(f"Net Loss {_fmt(ni)}", C.CLR_RED)
        remaining = pretax - items["tax_provision"]
        if remaining > 0:
            b.add_link(n_pretax, n_net, remaining, "rgba(220,50,50,0.5)")
    else:
        remaining = pretax - items["tax_provision"]
        if remaining > 0:
            n_net = b.add_node(f"Net Income {_fmt(remaining)}", C.CLR_GREEN)
            b.add_link(n_pretax, n_net, remaining, "rgba(50,180,50,0.5)")


def _add_pretax_negative_path(b: _SankeyBuilder, n_opinc: int, pretax: float, items: dict) -> None:
    """Add nodes/links for pretax loss scenario."""
    C = _SankeyBuilder
    oi = items["operating_income"]
    n_pretax = b.add_node(f"Pretax Loss {_fmt(pretax)}", C.CLR_RED)
    remaining = oi - items["interest_expense"]
    if items["other_income"] < 0:
        remaining -= abs(items["other_income"])
    if remaining > 0:
        b.add_link(n_opinc, n_pretax, remaining, "rgba(255,100,100,0.4)")

    ni = items["net_income"]
    if ni < 0:
        abs_ni = abs(ni)
        n_netloss = b.add_node(f"Net Loss {_fmt(ni)}", C.CLR_RED)
        if items["tax_provision"] > 0:
            n_tax = b.add_node(f"Taxes {_fmt(items['tax_provision'])}", C.CLR_ORANGE)
            b.add_link(n_pretax, n_tax, items["tax_provision"], "rgba(255,160,100,0.4)")
        b.add_link(n_pretax, n_netloss, abs_ni, "rgba(200,30,30,0.6)")


def _add_loss_operating_path(b: _SankeyBuilder, n_gross: int, items: dict) -> None:
    """Build Sankey path when operating income is negative (operating loss)."""
    C = _SankeyBuilder
    oi = items["operating_income"]
    abs_oi = abs(oi)
    ni = items["net_income"]

    n_oploss = b.add_node(f"Operating Loss {_fmt(oi)}", C.CLR_RED)
    b.add_link(n_gross, n_oploss, abs_oi, "rgba(220,50,50,0.5)")

    if ni < 0:
        abs_ni = abs(ni)
        n_netloss = b.add_node(f"Net Loss {_fmt(ni)}", C.CLR_RED)

        if abs_oi > abs_ni + 1e6:
            # Non-operating items reduced the loss
            offset = abs_oi - abs_ni
            n_offset = b.add_node(f"Interest/Other Income {_fmt(offset)}", C.CLR_TEAL)
            b.add_link(n_oploss, n_offset, offset, "rgba(100,200,180,0.4)")
            b.add_link(n_oploss, n_netloss, abs_ni, "rgba(200,30,30,0.6)")
        elif abs_ni > abs_oi + 1e6:
            # Non-operating items worsened the loss
            additional = abs_ni - abs_oi
            b.add_link(n_oploss, n_netloss, abs_oi, "rgba(200,30,30,0.6)")
            n_extra = b.add_node(f"Interest/Other Charges {_fmt(additional)}", C.CLR_ORANGE)
            b.add_link(n_extra, n_netloss, additional, "rgba(255,100,100,0.4)")
        else:
            b.add_link(n_oploss, n_netloss, abs_ni, "rgba(200,30,30,0.6)")
    elif ni > 0:
        # Operating loss but net profit (large non-operating income)
        n_netinc = b.add_node(f"Net Income {_fmt(ni)}", C.CLR_GREEN)
        total_recovery = abs_oi + ni
        n_recovery = b.add_node(f"Interest/Other Income {_fmt(total_recovery)}", C.CLR_TEAL)
        b.add_link(n_recovery, n_netinc, ni, "rgba(50,180,50,0.5)")
        b.add_link(n_oploss, n_recovery, abs_oi, "rgba(100,200,180,0.4)")


def _get_period_label(income_stmt: pd.DataFrame) -> str:
    """Extract a human-readable period label from the first column name."""
    try:
        col_name = income_stmt.columns[0]
        if hasattr(col_name, "strftime"):
            return f" (FY ending {col_name.strftime('%Y-%m-%d')})"
        return f" ({col_name})"
    except Exception:
        return ""


def _create_figure(go, builder: _SankeyBuilder, title_name: str, period_label: str):
    """Create the Plotly Sankey figure from the builder's accumulated data."""
    fig = go.Figure(data=[go.Sankey(
        node=dict(
            pad=20,
            thickness=25,
            line=dict(color="black", width=0.5),
            label=builder.nodes,
            color=builder.node_colors,
        ),
        link=dict(
            source=builder.links_source,
            target=builder.links_target,
            value=builder.links_value,
            color=builder.links_color,
        ),
    )])

    fig.update_layout(
        title_text=f"{title_name} — Income Statement Flow{period_label}",
        font_size=12,
        width=900,
        height=500,
        margin=dict(l=20, r=20, t=60, b=20),
    )
    return fig


def _save_figure(fig, output_dir: Path) -> bool:
    """Save the Sankey figure as HTML and PNG."""
    html_path = output_dir / "income_statement_sankey.html"
    fig.write_html(str(html_path))
    logger.info(f"  [ok] Sankey HTML: {html_path}")

    png_path = output_dir / "income_statement_sankey.png"
    try:
        fig.write_image(str(png_path), scale=CHART_SCALE)
        logger.info(f"  [ok] Sankey PNG:  {png_path}")
    except Exception as exc:
        logger.warning(f"  [FAIL] PNG export failed (install kaleido): {exc}")

    return True
