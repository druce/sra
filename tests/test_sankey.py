"""Tests for skills/fetch_fundamental/sankey.py — Sankey chart generation.

Tests the data extraction and derivation logic without actually rendering
Plotly charts (those require display/kaleido). We test the internal helpers
that transform income statement data into Sankey node/link structures.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

SKILLS = Path(__file__).resolve().parent.parent / "skills"
if str(SKILLS) not in sys.path:
    sys.path.insert(0, str(SKILLS))

from fetch_fundamental.sankey import (
    _extract_line_items,
    _derive_missing_values,
    _find_latest_period,
    _SankeyBuilder,
    _build_sankey_graph,
    _fmt,
)


# ---------------------------------------------------------------------------
# _fmt (dollar formatting)
# ---------------------------------------------------------------------------

class TestFmt:
    def test_billions(self):
        assert _fmt(2_500_000_000) == "$2.5B"

    def test_millions(self):
        assert _fmt(150_000_000) == "$150.0M"

    def test_thousands(self):
        assert _fmt(45_000) == "$45.0K"

    def test_small(self):
        assert _fmt(500) == "$500"

    def test_negative_uses_absolute(self):
        assert _fmt(-1_000_000_000) == "$1.0B"


# ---------------------------------------------------------------------------
# _find_latest_period
# ---------------------------------------------------------------------------

class TestFindLatestPeriod:
    def test_finds_period_with_revenue(self):
        df = pd.DataFrame({
            "2024": [100_000_000, 50_000_000],
            "2023": [90_000_000, 45_000_000],
        }, index=["Total Revenue", "Cost Of Revenue"])
        result = _find_latest_period(df)
        assert result is not None
        assert float(result["Total Revenue"]) == 100_000_000

    def test_returns_none_for_no_revenue(self):
        df = pd.DataFrame({
            "2024": [0, 50_000_000],
        }, index=["Total Revenue", "Cost Of Revenue"])
        result = _find_latest_period(df)
        assert result is None

    def test_handles_camelcase_keys(self):
        df = pd.DataFrame({
            "2024": [100_000_000],
        }, index=["TotalRevenue"])
        result = _find_latest_period(df)
        assert result is not None


# ---------------------------------------------------------------------------
# _extract_line_items
# ---------------------------------------------------------------------------

class TestExtractLineItems:
    def _make_period(self, data):
        return pd.Series(data)

    def test_extracts_basic_items(self):
        period = self._make_period({
            "Total Revenue": 1_000_000,
            "Cost Of Revenue": 400_000,
            "Gross Profit": 600_000,
            "Operating Income": 200_000,
            "Net Income": 150_000,
        })
        items = _extract_line_items(period)
        assert items["total_revenue"] == 1_000_000
        assert items["cost_of_revenue"] == 400_000
        assert items["gross_profit"] == 600_000

    def test_missing_keys_default_to_zero(self):
        period = self._make_period({
            "Total Revenue": 1_000_000,
        })
        items = _extract_line_items(period)
        assert items["total_revenue"] == 1_000_000
        assert items["cost_of_revenue"] == 0.0
        assert items["selling_ga"] == 0.0


# ---------------------------------------------------------------------------
# _derive_missing_values
# ---------------------------------------------------------------------------

class TestDeriveMissingValues:
    def test_derives_gross_profit(self):
        items = {
            "total_revenue": 1_000_000,
            "cost_of_revenue": 400_000,
            "gross_profit": 0,
            "operating_expense": 0,
            "selling_ga": 0,
            "research_dev": 0,
            "other_operating": 0,
            "operating_income": 0,
        }
        _derive_missing_values(items)
        assert items["gross_profit"] == 600_000

    def test_derives_cost_of_revenue(self):
        items = {
            "total_revenue": 1_000_000,
            "cost_of_revenue": 0,
            "gross_profit": 600_000,
            "operating_expense": 0,
            "selling_ga": 0,
            "research_dev": 0,
            "other_operating": 0,
            "operating_income": 0,
        }
        _derive_missing_values(items)
        assert items["cost_of_revenue"] == 400_000

    def test_derives_operating_expense(self):
        items = {
            "total_revenue": 1_000_000,
            "cost_of_revenue": 400_000,
            "gross_profit": 600_000,
            "operating_expense": 0,
            "selling_ga": 200_000,
            "research_dev": 100_000,
            "other_operating": 50_000,
            "operating_income": 0,
        }
        _derive_missing_values(items)
        assert items["operating_expense"] == 350_000

    def test_no_derivation_when_values_present(self):
        items = {
            "total_revenue": 1_000_000,
            "cost_of_revenue": 400_000,
            "gross_profit": 600_000,
            "operating_expense": 300_000,
            "selling_ga": 200_000,
            "research_dev": 100_000,
            "other_operating": 0,
            "operating_income": 300_000,
        }
        _derive_missing_values(items)
        # Values should be unchanged
        assert items["gross_profit"] == 600_000
        assert items["cost_of_revenue"] == 400_000


# ---------------------------------------------------------------------------
# _SankeyBuilder
# ---------------------------------------------------------------------------

class TestSankeyBuilder:
    def test_add_node(self):
        b = _SankeyBuilder()
        idx = b.add_node("Revenue", _SankeyBuilder.CLR_BLUE)
        assert idx == 0
        assert b.nodes[0] == "Revenue"

    def test_add_link(self):
        b = _SankeyBuilder()
        src = b.add_node("A")
        tgt = b.add_node("B")
        b.add_link(src, tgt, 100.0)
        assert len(b.links_source) == 1
        assert b.links_value[0] == 100.0

    def test_add_link_zero_skipped(self):
        b = _SankeyBuilder()
        src = b.add_node("A")
        tgt = b.add_node("B")
        b.add_link(src, tgt, 0.0)
        assert len(b.links_source) == 0

    def test_add_link_negative_skipped(self):
        b = _SankeyBuilder()
        src = b.add_node("A")
        tgt = b.add_node("B")
        b.add_link(src, tgt, -50.0)
        assert len(b.links_source) == 0


# ---------------------------------------------------------------------------
# _build_sankey_graph (end-to-end data flow)
# ---------------------------------------------------------------------------

class TestBuildSankeyGraph:
    def test_profitable_company(self):
        items = {
            "total_revenue": 10_000_000,
            "cost_of_revenue": 4_000_000,
            "gross_profit": 6_000_000,
            "operating_expense": 3_000_000,
            "selling_ga": 1_500_000,
            "research_dev": 1_000_000,
            "other_operating": 500_000,
            "operating_income": 3_000_000,
            "interest_expense": 200_000,
            "tax_provision": 500_000,
            "other_income": 0,
            "net_income": 2_300_000,
            "pretax_income": 2_800_000,
        }
        b = _SankeyBuilder()
        _build_sankey_graph(b, items)
        assert len(b.nodes) > 0
        assert len(b.links_source) > 0
        # Revenue should be the first node
        assert "Revenue" in b.nodes[0]

    def test_loss_making_company(self):
        items = {
            "total_revenue": 5_000_000,
            "cost_of_revenue": 3_000_000,
            "gross_profit": 2_000_000,
            "operating_expense": 4_000_000,
            "selling_ga": 2_000_000,
            "research_dev": 2_000_000,
            "other_operating": 0,
            "operating_income": -2_000_000,
            "interest_expense": 100_000,
            "tax_provision": 0,
            "other_income": 0,
            "net_income": -2_100_000,
            "pretax_income": -2_100_000,
        }
        b = _SankeyBuilder()
        _build_sankey_graph(b, items)
        assert len(b.nodes) > 0
        # Should have a "Loss" node somewhere
        loss_nodes = [n for n in b.nodes if "Loss" in n]
        assert len(loss_nodes) > 0
