#!/usr/bin/env python3
"""
Peer Identification Skill — identify_peers.py

Pure-Python peer identification: fetch candidates from multiple providers,
enrich via yfinance, filter bad tickers, score by comparability, select top N.

Usage:
    ./skills/identify_peers/identify_peers.py SYMBOL [--count 5] [--workdir DIR]

Output:
    - {workdir}/artifacts/peers_list.json — peer list in column-oriented format
    - JSON manifest to stdout
"""

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import finnhub
import yfinance as yf

_SKILLS_DIR = Path(__file__).resolve().parent.parent
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

from config import MAX_PEERS_TO_FETCH  # noqa: E402
from utils import (  # noqa: E402
    setup_logging,
    validate_symbol,
    format_currency,
    load_environment,
    default_workdir,
)

load_environment()
logger = setup_logging(__name__)


# ── Provider Fetch Functions ──────────────────────────────────────────────


def fetch_finnhub_peers(
    symbol: str, *, api_key: Optional[str] = None
) -> Tuple[Optional[List[str]], str]:
    """Fetch peer tickers from Finnhub API.

    Args:
        symbol: Target stock ticker.
        api_key: Finnhub API key. Falls back to FINNHUB_API_KEY env var.

    Returns:
        (list_of_tickers | None, source_description_or_error)
    """
    key = api_key or os.getenv("FINNHUB_API_KEY")
    if not key:
        return None, "FINNHUB_API_KEY not set"
    try:
        client = finnhub.Client(api_key=key)
        peers = client.company_peers(symbol)
        peers = [s for s in (peers or []) if s != symbol]
        if not peers:
            return None, "Finnhub returned no peers"
        logger.info(f"Finnhub returned {len(peers)} peers")
        return peers[:MAX_PEERS_TO_FETCH], "Finnhub"
    except Exception as e:
        return None, f"Finnhub error: {e}"


def fetch_openbb_peers(symbol: str) -> Tuple[Optional[List[str]], str]:
    """Fetch peer tickers from OpenBB/FMP.

    Args:
        symbol: Target stock ticker.

    Returns:
        (list_of_tickers | None, source_description_or_error)
    """
    pat = os.getenv("OPENBB_PAT")
    if not pat:
        return None, "OPENBB_PAT not set"
    try:
        from openbb import obb

        obb.user.credentials.openbb_pat = pat
        result = obb.equity.compare.peers(symbol=symbol, provider="fmp")
        data = result.to_dict()
        peer_symbols: List[str] = []
        if isinstance(data, dict):
            for key in ("peers_list", "symbol", "peers"):
                if key in data:
                    val = data[key]
                    if isinstance(val, list):
                        peer_symbols = val
                        break
        if peer_symbols and isinstance(peer_symbols[0], list):
            peer_symbols = [s for sub in peer_symbols for s in sub]
        peer_symbols = [
            s for s in peer_symbols if isinstance(s, str) and s != symbol
        ]
        if not peer_symbols:
            return None, "OpenBB/FMP returned no peers"
        logger.info(f"OpenBB/FMP returned {len(peer_symbols)} peers")
        return peer_symbols[:MAX_PEERS_TO_FETCH], "OpenBB/FMP"
    except Exception as e:
        return None, f"OpenBB/FMP error: {e}"


def fetch_yfinance_sector_peers(
    symbol: str, sector: str, industry: str
) -> Tuple[Optional[List[str]], str]:
    """Get sector/industry peers from yfinance recommendations.

    Args:
        symbol: Target stock ticker.
        sector: Target company sector.
        industry: Target company industry.

    Returns:
        (list_of_tickers | None, source_description_or_error)
    """
    try:
        ticker = yf.Ticker(symbol)
        recs = ticker.recommendations
        if recs is None or recs.empty:
            return None, "yfinance returned no recommendations"

        # Try to extract peer-like tickers from recommendations
        peer_symbols: List[str] = []
        if hasattr(recs, "index"):
            # Some yfinance versions provide symbol-indexed recommendations
            for col in recs.columns:
                if col.lower() in ("symbol", "ticker"):
                    peer_symbols = recs[col].dropna().unique().tolist()
                    break

        # If no symbols found in recommendations, return None
        if not peer_symbols:
            return None, "yfinance recommendations did not contain peer symbols"

        peer_symbols = [s for s in peer_symbols if s != symbol]
        if not peer_symbols:
            return None, "yfinance returned no peers after filtering"

        logger.info(f"yfinance returned {len(peer_symbols)} peers")
        return peer_symbols[:MAX_PEERS_TO_FETCH], "yfinance"
    except Exception as e:
        return None, f"yfinance error: {e}"


# ── Enrichment & Filtering ───────────────────────────────────────────────


def enrich_candidates(symbols: List[str]) -> List[Dict]:
    """Enrich all unique candidate tickers with yfinance data.

    Fetches name, sector, industry, market_cap, price, revenue, and margins
    for each symbol. Deduplicates input symbols.

    Args:
        symbols: List of ticker symbols (may contain duplicates).

    Returns:
        List of dicts with enriched data for each unique symbol.
    """
    seen: set = set()
    results: List[Dict] = []

    for sym in symbols:
        if sym in seen:
            continue
        seen.add(sym)

        try:
            info = yf.Ticker(sym).info
            results.append({
                "symbol": sym,
                "name": info.get("longName") or info.get("shortName", sym),
                "sector": info.get("sector", "N/A"),
                "industry": info.get("industry", "N/A"),
                "market_cap": info.get("marketCap"),
                "price": info.get("currentPrice") or info.get("regularMarketPrice"),
                "revenue": info.get("totalRevenue"),
                "gross_margins": info.get("grossMargins"),
                "operating_margins": info.get("operatingMargins"),
            })
            logger.info(f"  Enriched {sym}: {results[-1]['name']}")
        except Exception as e:
            logger.warning(f"  Could not enrich {sym}: {e}")
            results.append({
                "symbol": sym,
                "name": sym,
                "sector": "N/A",
                "industry": "N/A",
                "market_cap": None,
                "price": None,
                "revenue": None,
                "gross_margins": None,
                "operating_margins": None,
            })
        time.sleep(0.1)

    return results


def filter_bad_tickers(candidates: List[Dict]) -> List[Dict]:
    """Remove candidates with no market_cap, no price, or name==ticker.

    Args:
        candidates: List of enriched candidate dicts.

    Returns:
        Filtered list with bad tickers removed.
    """
    filtered: List[Dict] = []
    for c in candidates:
        if not c.get("market_cap"):
            logger.info(f"  Filtered {c['symbol']}: no market cap")
            continue
        if not c.get("price"):
            logger.info(f"  Filtered {c['symbol']}: no price")
            continue
        if c.get("name") == c.get("symbol"):
            logger.info(f"  Filtered {c['symbol']}: name equals ticker (likely private/foreign)")
            continue
        filtered.append(c)
    return filtered


# ── Scoring ───────────────────────────────────────────────────────────────


def _log_ratio(a: Optional[float], b: Optional[float]) -> float:
    """Compute scale proximity as 1 - |log10(a/b)| / log10(1000), clamped to [0, 1].

    Returns 0 when either value is missing or zero.
    """
    if not a or not b:
        return 0.0
    try:
        ratio = abs(math.log10(a / b))
        return max(0.0, 1.0 - ratio / math.log10(1000))
    except (ValueError, ZeroDivisionError):
        return 0.0


def score_and_rank(target: Dict, candidates: List[Dict]) -> List[Dict]:
    """Score each candidate by comparability to target and return sorted list.

    Scoring weights:
        - Scale proximity (market cap ratio): 40%
        - Industry match: 30%
        - Margin similarity: 30%

    Args:
        target: Target company profile dict.
        candidates: List of enriched candidate dicts.

    Returns:
        Sorted list (descending by score) with '_score' key added.
    """
    if not candidates:
        return []

    target_mcap = target.get("market_cap")
    target_industry = target.get("industry", "")
    target_gm = target.get("gross_margins")
    target_om = target.get("operating_margins")

    scored: List[Dict] = []
    for c in candidates:
        # Scale proximity: 40%
        scale_score = _log_ratio(c.get("market_cap"), target_mcap)

        # Industry match: 30%
        industry_score = 1.0 if c.get("industry") == target_industry else 0.0

        # Margin similarity: 30% (average of gross & operating margin closeness)
        margin_scores = []
        c_gm = c.get("gross_margins")
        c_om = c.get("operating_margins")
        if target_gm is not None and c_gm is not None:
            margin_scores.append(max(0.0, 1.0 - abs(target_gm - c_gm)))
        if target_om is not None and c_om is not None:
            margin_scores.append(max(0.0, 1.0 - abs(target_om - c_om)))
        margin_score = sum(margin_scores) / len(margin_scores) if margin_scores else 0.0

        total = 0.4 * scale_score + 0.3 * industry_score + 0.3 * margin_score
        entry = dict(c)
        entry["_score"] = round(total, 4)
        scored.append(entry)

    scored.sort(key=lambda x: x["_score"], reverse=True)
    return scored


# ── Selection & Output ────────────────────────────────────────────────────


def select_peers(ranked: List[Dict], count: int) -> Dict:
    """Take top N ranked candidates and format into column-oriented output.

    Args:
        ranked: Sorted list of candidate dicts (from score_and_rank).
        count: Number of peers to select.

    Returns:
        Column-oriented dict matching downstream contract:
        {"symbol": [...], "name": [...], "price": [...], "market_cap": [...],
         "provider": "identify_peers", "filtered": true, "filter_rationale": "..."}
    """
    selected = ranked[:count]
    symbols = [c["symbol"] for c in selected]
    names = [c.get("name", c["symbol"]) for c in selected]
    prices = [
        round(float(c["price"]), 2) if c.get("price") is not None else None
        for c in selected
    ]
    market_caps = [c.get("market_cap") for c in selected]

    scores_desc = ", ".join(
        f"{c['symbol']}({c.get('_score', 0):.2f})" for c in selected
    )
    rationale = (
        f"Top {len(selected)} peers selected by comparability scoring "
        f"(scale 40%, industry 30%, margins 30%): {scores_desc}"
    )

    return {
        "symbol": symbols,
        "name": names,
        "price": prices,
        "market_cap": market_caps,
        "provider": "identify_peers",
        "filtered": True,
        "filter_rationale": rationale,
    }


# ── Target Profile ────────────────────────────────────────────────────────


def get_target_profile(symbol: str) -> Dict:
    """Fetch target company profile from yfinance.

    Args:
        symbol: Stock ticker symbol.

    Returns:
        Dict with ticker, name, sector, industry, market_cap, revenue,
        gross_margins, operating_margins, business_summary.
    """
    logger.info(f"Fetching profile for {symbol}...")
    info = yf.Ticker(symbol).info
    return {
        "ticker": symbol,
        "name": info.get("longName") or info.get("shortName", symbol),
        "sector": info.get("sector", "N/A"),
        "industry": info.get("industry", "N/A"),
        "market_cap": info.get("marketCap"),
        "revenue": info.get("totalRevenue"),
        "gross_margins": info.get("grossMargins"),
        "operating_margins": info.get("operatingMargins"),
        "business_summary": info.get("longBusinessSummary", ""),
    }


# ── Main ──────────────────────────────────────────────────────────────────


def main() -> int:
    """CLI entry point: fetch, enrich, filter, score, select peers."""
    parser = argparse.ArgumentParser(
        description="Identify the most comparable peer companies for a given ticker"
    )
    parser.add_argument("symbol", help="Stock ticker symbol")
    parser.add_argument(
        "--count",
        type=int,
        default=5,
        help="Number of peers to select (default: 5)",
    )
    parser.add_argument(
        "--workdir",
        default=None,
        help="Work directory (default: work/SYMBOL_YYYYMMDD)",
    )
    args = parser.parse_args()

    symbol = validate_symbol(args.symbol)
    workdir = Path(args.workdir) if args.workdir else Path(default_workdir(symbol))
    workdir.mkdir(parents=True, exist_ok=True)
    artifacts_dir = workdir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Identifying {args.count} peers for {symbol} in {workdir}")

    # 1. Fetch target profile
    try:
        target = get_target_profile(symbol)
    except Exception as e:
        logger.error(f"Failed to fetch profile for {symbol}: {e}")
        print(json.dumps({"status": "failed", "error": str(e), "artifacts": []}))
        return 2

    logger.info(
        f"Target: {target['name']} | {target['industry']} | "
        f"Market cap {format_currency(target['market_cap']) if target.get('market_cap') else 'N/A'}"
    )

    # 2. Fetch candidates from providers
    all_symbols: List[str] = []
    sources: List[str] = []

    finnhub_peers, finnhub_src = fetch_finnhub_peers(symbol)
    if finnhub_peers:
        all_symbols.extend(finnhub_peers)
        sources.append(finnhub_src)
        logger.info(f"Finnhub: {len(finnhub_peers)} peers")
    else:
        logger.warning(f"Finnhub: {finnhub_src}")

    openbb_peers, openbb_src = fetch_openbb_peers(symbol)
    if openbb_peers:
        all_symbols.extend(openbb_peers)
        sources.append(openbb_src)
        logger.info(f"OpenBB: {len(openbb_peers)} peers")
    else:
        logger.warning(f"OpenBB: {openbb_src}")

    yf_peers, yf_src = fetch_yfinance_sector_peers(
        symbol, target.get("sector", ""), target.get("industry", "")
    )
    if yf_peers:
        all_symbols.extend(yf_peers)
        sources.append(yf_src)
        logger.info(f"yfinance: {len(yf_peers)} peers")
    else:
        logger.warning(f"yfinance: {yf_src}")

    if not all_symbols:
        logger.error("No provider returned any peer candidates")
        print(json.dumps({
            "status": "failed",
            "error": "No peer candidates from any provider",
            "artifacts": [],
        }))
        return 2

    # 3. Enrich candidates
    logger.info(f"Enriching {len(set(all_symbols))} unique candidates...")
    enriched = enrich_candidates(all_symbols)

    # 4. Filter bad tickers
    filtered = filter_bad_tickers(enriched)
    logger.info(f"After filtering: {len(filtered)} of {len(enriched)} candidates")

    if not filtered:
        logger.error("All candidates filtered out")
        print(json.dumps({
            "status": "failed",
            "error": "No valid candidates after filtering",
            "artifacts": [],
        }))
        return 2

    # 5. Score and rank
    ranked = score_and_rank(target, filtered)
    for c in ranked[:10]:
        logger.info(
            f"  {c['symbol']:6s} score={c['_score']:.3f}  "
            f"{c.get('name', '?'):30s}  mcap={format_currency(c.get('market_cap', 0))}"
        )

    # 6. Select top N
    peers_list = select_peers(ranked, args.count)
    peer_count = len(peers_list["symbol"])

    # 7. Write output
    output_path = artifacts_dir / "peers_list.json"
    output_path.write_text(json.dumps(peers_list, indent=2, default=str))
    logger.info(f"Wrote {peer_count} peers to {output_path}")

    peer_names = ", ".join(peers_list["symbol"])
    manifest = {
        "status": "complete",
        "artifacts": [
            {
                "name": "peers_list",
                "path": "artifacts/peers_list.json",
                "format": "json",
                "description": f"{peer_count} peer companies for {symbol}: {peer_names}",
            }
        ],
        "error": None,
    }
    print(json.dumps(manifest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
