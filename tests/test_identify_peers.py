"""Tests for identify_peers.py — pure Python peer identification."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure skills/ and skills/identify_peers/ are importable
_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root / "skills"))
sys.path.insert(0, str(_root / "skills" / "identify_peers"))

# Import as a plain module (no __init__.py in identify_peers/)
import identify_peers as _mod  # noqa: E402

fetch_finnhub_peers = _mod.fetch_finnhub_peers
fetch_openbb_peers = _mod.fetch_openbb_peers
fetch_yfinance_sector_peers = _mod.fetch_yfinance_sector_peers
enrich_candidates = _mod.enrich_candidates
filter_bad_tickers = _mod.filter_bad_tickers
score_and_rank = _mod.score_and_rank
select_peers = _mod.select_peers
get_target_profile = _mod.get_target_profile

# Module name for mock.patch targets
_M = "identify_peers"


# ── Fixtures ──────────────────────────────────────────────────────────────


def _make_candidate(
    symbol="AAPL",
    name="Apple Inc.",
    sector="Technology",
    industry="Consumer Electronics",
    market_cap=3_000_000_000_000,
    price=190.0,
    revenue=400_000_000_000,
    gross_margins=0.44,
    operating_margins=0.30,
    source="Finnhub",
):
    return {
        "symbol": symbol,
        "name": name,
        "sector": sector,
        "industry": industry,
        "market_cap": market_cap,
        "price": price,
        "revenue": revenue,
        "gross_margins": gross_margins,
        "operating_margins": operating_margins,
        "source": source,
    }


@pytest.fixture
def target():
    return _make_candidate(
        symbol="MSFT",
        name="Microsoft Corporation",
        market_cap=2_800_000_000_000,
        revenue=230_000_000_000,
        gross_margins=0.69,
        operating_margins=0.42,
    )


@pytest.fixture
def candidates():
    return [
        _make_candidate(
            symbol="AAPL",
            name="Apple Inc.",
            market_cap=3_000_000_000_000,
            revenue=400_000_000_000,
            gross_margins=0.44,
            operating_margins=0.30,
        ),
        _make_candidate(
            symbol="GOOG",
            name="Alphabet Inc.",
            market_cap=2_000_000_000_000,
            revenue=320_000_000_000,
            gross_margins=0.57,
            operating_margins=0.28,
        ),
        _make_candidate(
            symbol="CRM",
            name="Salesforce Inc.",
            industry="Software - Application",
            market_cap=250_000_000_000,
            revenue=35_000_000_000,
            gross_margins=0.75,
            operating_margins=0.17,
        ),
        _make_candidate(
            symbol="TINY",
            name="Tiny Corp",
            market_cap=500_000,
            revenue=1_000_000,
            gross_margins=0.10,
            operating_margins=0.02,
        ),
    ]


# ── filter_bad_tickers ───────────────────────────────────────────────────


class TestFilterBadTickers:
    def test_removes_no_market_cap(self):
        cands = [_make_candidate(market_cap=None)]
        assert filter_bad_tickers(cands) == []

    def test_removes_no_price(self):
        cands = [_make_candidate(price=None)]
        assert filter_bad_tickers(cands) == []

    def test_removes_name_equals_ticker(self):
        cands = [_make_candidate(symbol="XYZ", name="XYZ")]
        assert filter_bad_tickers(cands) == []

    def test_keeps_valid(self):
        cands = [_make_candidate()]
        result = filter_bad_tickers(cands)
        assert len(result) == 1
        assert result[0]["symbol"] == "AAPL"

    def test_mixed(self):
        cands = [
            _make_candidate(symbol="GOOD", name="Good Inc."),
            _make_candidate(symbol="BAD1", name="BAD1"),  # name == ticker
            _make_candidate(symbol="BAD2", market_cap=None),
            _make_candidate(symbol="BAD3", price=None),
            _make_candidate(symbol="OK", name="OK Corp.", market_cap=1000, price=5.0),
        ]
        result = filter_bad_tickers(cands)
        syms = [c["symbol"] for c in result]
        assert "GOOD" in syms
        assert "OK" in syms
        assert "BAD1" not in syms
        assert "BAD2" not in syms
        assert "BAD3" not in syms

    def test_empty_input(self):
        assert filter_bad_tickers([]) == []

    def test_removes_zero_market_cap(self):
        cands = [_make_candidate(market_cap=0)]
        assert filter_bad_tickers(cands) == []


# ── score_and_rank ────────────────────────────────────────────────────────


class TestScoreAndRank:
    def test_returns_sorted_descending(self, target, candidates):
        ranked = score_and_rank(target, candidates)
        scores = [c["_score"] for c in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_all_have_score_key(self, target, candidates):
        ranked = score_and_rank(target, candidates)
        for c in ranked:
            assert "_score" in c
            assert isinstance(c["_score"], float)

    def test_score_between_0_and_1(self, target, candidates):
        ranked = score_and_rank(target, candidates)
        for c in ranked:
            assert 0.0 <= c["_score"] <= 1.0

    def test_same_industry_scores_higher(self, target):
        same_ind = _make_candidate(
            symbol="SAME",
            industry="Consumer Electronics",
            market_cap=target["market_cap"],
            gross_margins=target["gross_margins"],
            operating_margins=target["operating_margins"],
        )
        diff_ind = _make_candidate(
            symbol="DIFF",
            industry="Totally Different",
            market_cap=target["market_cap"],
            gross_margins=target["gross_margins"],
            operating_margins=target["operating_margins"],
        )
        ranked = score_and_rank(target, [same_ind, diff_ind])
        assert ranked[0]["symbol"] == "SAME"

    def test_closer_market_cap_scores_higher(self, target):
        close = _make_candidate(symbol="CLOSE", market_cap=2_500_000_000_000)
        far = _make_candidate(symbol="FAR", market_cap=1_000_000)
        ranked = score_and_rank(target, [close, far])
        assert ranked[0]["symbol"] == "CLOSE"

    def test_handles_missing_margins(self, target):
        cand = _make_candidate(gross_margins=None, operating_margins=None)
        ranked = score_and_rank(target, [cand])
        assert len(ranked) == 1
        assert 0.0 <= ranked[0]["_score"] <= 1.0

    def test_empty_candidates(self, target):
        assert score_and_rank(target, []) == []


# ── select_peers ──────────────────────────────────────────────────────────


class TestSelectPeers:
    def test_output_structure(self, target, candidates):
        ranked = score_and_rank(target, candidates)
        result = select_peers(ranked, 3)
        assert isinstance(result, dict)
        for key in ("symbol", "name", "price", "market_cap"):
            assert key in result
            assert isinstance(result[key], list)
        assert result["provider"] == "identify_peers"
        assert result["filtered"] is True
        assert "filter_rationale" in result

    def test_respects_count(self, target, candidates):
        ranked = score_and_rank(target, candidates)
        result = select_peers(ranked, 2)
        assert len(result["symbol"]) == 2

    def test_count_larger_than_available(self, target, candidates):
        ranked = score_and_rank(target, candidates)
        result = select_peers(ranked, 100)
        assert len(result["symbol"]) == len(candidates)

    def test_all_lists_same_length(self, target, candidates):
        ranked = score_and_rank(target, candidates)
        result = select_peers(ranked, 3)
        n = len(result["symbol"])
        assert len(result["name"]) == n
        assert len(result["price"]) == n
        assert len(result["market_cap"]) == n

    def test_empty_ranked(self):
        result = select_peers([], 5)
        assert result["symbol"] == []
        assert result["name"] == []


# ── fetch_finnhub_peers (mocked) ─────────────────────────────────────────


class TestFetchFinnhubPeers:
    @patch(f"{_M}.finnhub")
    def test_returns_peers_on_success(self, mock_finnhub):
        mock_client = MagicMock()
        mock_client.company_peers.return_value = ["AAPL", "GOOG", "MSFT", "META"]
        mock_finnhub.Client.return_value = mock_client

        result, source = fetch_finnhub_peers("MSFT", api_key="fake-key")
        assert result is not None
        assert "MSFT" not in result  # target excluded
        assert "AAPL" in result
        assert source == "Finnhub"

    @patch(f"{_M}.finnhub")
    def test_returns_none_on_empty(self, mock_finnhub):
        mock_client = MagicMock()
        mock_client.company_peers.return_value = []
        mock_finnhub.Client.return_value = mock_client

        result, source = fetch_finnhub_peers("MSFT", api_key="fake-key")
        assert result is None

    @patch.dict("os.environ", {}, clear=True)
    def test_returns_none_no_api_key(self):
        result, source = fetch_finnhub_peers("MSFT", api_key=None)
        assert result is None
        assert "not set" in source.lower()

    @patch(f"{_M}.finnhub")
    def test_returns_none_on_exception(self, mock_finnhub):
        mock_finnhub.Client.side_effect = Exception("connection error")

        result, source = fetch_finnhub_peers("MSFT", api_key="fake-key")
        assert result is None
        assert "error" in source.lower()

    @patch(f"{_M}.finnhub")
    def test_limits_to_max_peers(self, mock_finnhub):
        mock_client = MagicMock()
        mock_client.company_peers.return_value = [f"T{i}" for i in range(30)]
        mock_finnhub.Client.return_value = mock_client

        result, _ = fetch_finnhub_peers("MSFT", api_key="fake-key")
        assert result is not None
        assert len(result) <= 15  # MAX_PEERS_TO_FETCH


# ── enrich_candidates (mocked) ───────────────────────────────────────────


class TestEnrichCandidates:
    @patch(f"{_M}.yf")
    def test_enriches_symbols(self, mock_yf):
        mock_info = {
            "longName": "Apple Inc.",
            "sector": "Technology",
            "industry": "Consumer Electronics",
            "marketCap": 3_000_000_000_000,
            "currentPrice": 190.0,
            "totalRevenue": 400_000_000_000,
            "grossMargins": 0.44,
            "operatingMargins": 0.30,
        }
        mock_ticker = MagicMock()
        mock_ticker.info = mock_info
        mock_yf.Ticker.return_value = mock_ticker

        result = enrich_candidates(["AAPL"])
        assert len(result) == 1
        assert result[0]["symbol"] == "AAPL"
        assert result[0]["name"] == "Apple Inc."
        assert result[0]["market_cap"] == 3_000_000_000_000

    @patch(f"{_M}.yf")
    def test_handles_yfinance_exception(self, mock_yf):
        mock_yf.Ticker.side_effect = Exception("network error")
        result = enrich_candidates(["BAD"])
        assert len(result) == 1
        assert result[0]["symbol"] == "BAD"
        assert result[0]["market_cap"] is None

    @patch(f"{_M}.yf")
    def test_deduplicates(self, mock_yf):
        mock_ticker = MagicMock()
        mock_ticker.info = {"longName": "Apple", "marketCap": 100}
        mock_yf.Ticker.return_value = mock_ticker

        result = enrich_candidates(["AAPL", "AAPL", "GOOG"])
        symbols = [c["symbol"] for c in result]
        assert symbols.count("AAPL") == 1  # deduplicated


# ── get_target_profile (mocked) ──────────────────────────────────────────


class TestGetTargetProfile:
    @patch(f"{_M}.yf")
    def test_returns_profile(self, mock_yf):
        mock_ticker = MagicMock()
        mock_ticker.info = {
            "longName": "Microsoft Corp",
            "sector": "Technology",
            "industry": "Software",
            "marketCap": 2_800_000_000_000,
            "totalRevenue": 230_000_000_000,
            "grossMargins": 0.69,
            "operatingMargins": 0.42,
            "longBusinessSummary": "Cloud company",
        }
        mock_yf.Ticker.return_value = mock_ticker

        result = get_target_profile("MSFT")
        assert result["ticker"] == "MSFT"
        assert result["name"] == "Microsoft Corp"
        assert result["sector"] == "Technology"
        assert result["market_cap"] == 2_800_000_000_000
