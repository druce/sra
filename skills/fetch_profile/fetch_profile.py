#!/usr/bin/env python3
"""
Company Profile & Peer Detection Skill — research_profile.py

Fetches company profile metadata from yfinance and identifies peer companies
using a provider fallback chain (Finnhub -> OpenBB/FMP). Optionally filters
peers to true industry peers using the Claude API.

Usage:
    ./skills/research_profile.py SYMBOL --workdir DIR [--peers SYM1,SYM2,...] [--no-filter-peers]

Output:
    Writes profile.json and peers_list.json to {workdir}/artifacts/.
    Prints JSON manifest to stdout.
    All progress/diagnostic output goes to stderr.

Exit codes:
    0 - success (both profile and peers produced)
    1 - partial (profile or peers missing)
    2 - failure (nothing produced)
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yfinance as yf

# Add skills directory to path for local imports
_SKILLS_DIR = Path(__file__).resolve().parent.parent
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

# isort: split

from config import MAX_PEERS_TO_FETCH, CLAUDE_MODEL  # noqa: E402
from utils import setup_logging, validate_symbol, ensure_directory, format_currency, load_environment, default_workdir  # noqa: E402

load_environment()

logger = setup_logging(__name__)


# ============================================================================
# Company Profile
# ============================================================================

def get_company_profile(symbol: str) -> Tuple[bool, Optional[Dict], Optional[str]]:
    """
    Fetch company profile from yfinance.

    Extracts identity fields (name, sector, industry, description, employees,
    website, country) and valuation snapshot fields (market cap, enterprise value,
    current price, 52-week range, beta, shares outstanding, float).

    Args:
        symbol: Stock ticker symbol (uppercase).

    Returns:
        Tuple of (success, profile_dict_or_None, error_string_or_None).
    """
    logger.info(f"Fetching company profile for {symbol}...")

    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info

        if not info or info.get('quoteType') is None:
            return False, None, f"yfinance returned no data for {symbol}"

        # Some tickers return minimal info with just a 'symbol' key and nothing else
        if not info.get('shortName') and not info.get('longName'):
            return False, None, f"No company name found for {symbol} — likely invalid ticker"

        profile = {
            "symbol": symbol,
            "timestamp": datetime.now().isoformat(timespec='seconds'),
            "company_name": info.get('longName') or info.get('shortName', 'N/A'),
            "sector": info.get('sector', 'N/A'),
            "industry": info.get('industry', 'N/A'),
            "country": info.get('country', 'N/A'),
            "website": info.get('website', 'N/A'),
            "employees": info.get('fullTimeEmployees'),
            "business_summary": info.get('longBusinessSummary', 'N/A'),
            "market_cap": info.get('marketCap'),
            "enterprise_value": info.get('enterpriseValue'),
            "current_price": round(float(info.get('currentPrice') or info.get('regularMarketPrice') or 0), 2) or None,
            "52_week_high": info.get('fiftyTwoWeekHigh'),
            "52_week_low": info.get('fiftyTwoWeekLow'),
            "beta": info.get('beta'),
            "shares_outstanding": info.get('sharesOutstanding'),
            "float_shares": info.get('floatShares'),
        }

        logger.info(
            f"Profile fetched: {profile['company_name']} | "
            f"{profile['industry']} | "
            f"Market cap {format_currency(profile['market_cap']) if profile['market_cap'] else 'N/A'}"
        )
        return True, profile, None

    except Exception as e:
        error_msg = f"Failed to fetch profile for {symbol}: {e}"
        logger.error(error_msg)
        return False, None, error_msg


# ============================================================================
# Peer Detection — Provider Fallback Chain
# ============================================================================

def _get_peers_finnhub(symbol: str) -> Tuple[Optional[List[str]], Optional[str]]:
    """
    Fetch peer symbols from Finnhub.

    Returns:
        Tuple of (peer_symbol_list_or_None, provider_name_or_error).
    """
    api_key = os.getenv('FINNHUB_API_KEY')
    if not api_key:
        return None, "FINNHUB_API_KEY not set"

    try:
        import finnhub
        client = finnhub.Client(api_key=api_key)
        peer_symbols = client.company_peers(symbol)

        if not peer_symbols:
            return None, "Finnhub returned empty peer list"

        # Remove the target symbol from peers
        peer_symbols = [s for s in peer_symbols if s != symbol]

        if not peer_symbols:
            return None, "Finnhub returned only the target symbol"

        # Cap at MAX_PEERS_TO_FETCH
        peer_symbols = peer_symbols[:MAX_PEERS_TO_FETCH]
        logger.info(f"Finnhub returned {len(peer_symbols)} peers for {symbol}")
        return peer_symbols, "Finnhub"

    except Exception as e:
        return None, f"Finnhub error: {e}"


def _get_peers_openbb(symbol: str) -> Tuple[Optional[List[str]], Optional[str]]:
    """
    Fetch peer symbols from OpenBB/FMP.

    Returns:
        Tuple of (peer_symbol_list_or_None, provider_name_or_error).
    """
    pat = os.getenv('OPENBB_PAT')
    if not pat:
        return None, "OPENBB_PAT not set"

    try:
        from openbb import obb
        obb.user.credentials.openbb_pat = pat

        peers_result = obb.equity.compare.peers(symbol=symbol, provider='fmp')
        peers_data = peers_result.to_dict()

        # OpenBB returns peers in a 'peers_list' key or similar structure
        # Handle both list-of-dicts and dict-of-lists formats
        peer_symbols = []
        if isinstance(peers_data, dict):
            # Try common key names
            for key in ('peers_list', 'symbol', 'peers'):
                if key in peers_data:
                    val = peers_data[key]
                    if isinstance(val, list):
                        peer_symbols = val
                        break
            # If dict-of-lists with index keys, try to extract
            if not peer_symbols and 'results' in peers_data:
                results = peers_data['results']
                if isinstance(results, list):
                    for item in results:
                        if isinstance(item, dict) and 'symbol' in item:
                            peer_symbols.append(item['symbol'])
                        elif isinstance(item, str):
                            peer_symbols.append(item)
        elif isinstance(peers_data, list):
            for item in peers_data:
                if isinstance(item, dict) and 'symbol' in item:
                    peer_symbols.append(item['symbol'])
                elif isinstance(item, str):
                    peer_symbols.append(item)

        # Flatten if we got a list-of-lists
        if peer_symbols and isinstance(peer_symbols[0], list):
            peer_symbols = [s for sublist in peer_symbols for s in sublist]

        # Remove target symbol and filter to strings
        peer_symbols = [s for s in peer_symbols if isinstance(
            s, str) and s != symbol]

        if not peer_symbols:
            return None, "OpenBB/FMP returned no peers"

        peer_symbols = peer_symbols[:MAX_PEERS_TO_FETCH]
        logger.info(
            f"OpenBB/FMP returned {len(peer_symbols)} peers for {symbol}")
        return peer_symbols, "OpenBB/FMP"

    except Exception as e:
        return None, f"OpenBB/FMP error: {e}"


def _enrich_peers_with_yfinance(peer_symbols: List[str]) -> Dict:
    """
    Enrich peer symbols with name, price, and market cap from yfinance.

    Args:
        peer_symbols: List of ticker symbols.

    Returns:
        Dict in list-of-lists format: {symbol: [...], name: [...], price: [...], market_cap: [...]}.
    """
    symbols = []
    names = []
    prices = []
    market_caps = []

    for sym in peer_symbols:
        try:
            ticker = yf.Ticker(sym)
            info = ticker.info

            name = info.get('longName') or info.get('shortName', sym)
            price = info.get('currentPrice') or info.get('regularMarketPrice')
            mcap = info.get('marketCap')

            symbols.append(sym)
            names.append(name)
            prices.append(price)
            market_caps.append(mcap)

            logger.info(f"  Enriched {sym}: {name}")
        except Exception as e:
            logger.warning(f"  Could not enrich {sym}: {e}")
            # Include with partial data rather than skipping
            symbols.append(sym)
            names.append(sym)
            prices.append(None)
            market_caps.append(None)

        # Brief pause to avoid rate limiting
        time.sleep(0.1)

    return {
        "symbol": symbols,
        "name": names,
        "price": prices,
        "market_cap": market_caps,
    }


def get_peers(symbol: str) -> Tuple[bool, Optional[Dict], Optional[str]]:
    """
    Identify peer companies using provider fallback chain.

    Tries Finnhub first, then OpenBB/FMP. For each peer found, enriches
    with yfinance data (name, current price, market cap).

    Args:
        symbol: Stock ticker symbol (uppercase).

    Returns:
        Tuple of (success, peers_dict_or_None, error_string_or_None).
        peers_dict uses list-of-lists format with provider metadata.
    """
    logger.info(f"Detecting peers for {symbol}...")

    peer_symbols = None
    provider = None
    errors = []

    # Try Finnhub first
    result, msg = _get_peers_finnhub(symbol)
    if result:
        peer_symbols = result
        provider = "Finnhub"
    else:
        errors.append(msg)
        logger.warning(f"  Finnhub: {msg}")

    # Fallback to OpenBB/FMP
    if peer_symbols is None:
        result, msg = _get_peers_openbb(symbol)
        if result:
            peer_symbols = result
            provider = "OpenBB/FMP"
        else:
            errors.append(msg)
            logger.warning(f"  OpenBB/FMP: {msg}")

    if peer_symbols is None:
        error_msg = f"All peer providers failed: {'; '.join(errors)}"
        logger.error(error_msg)
        return False, None, error_msg

    # Enrich with yfinance data
    logger.info(f"Enriching {len(peer_symbols)} peers with yfinance data...")
    peers_data = _enrich_peers_with_yfinance(peer_symbols)
    peers_data["provider"] = provider
    peers_data["filtered"] = False
    peers_data["filter_rationale"] = None

    logger.info(
        f"Peers detected: {len(peers_data['symbol'])} via {provider}"
    )
    return True, peers_data, None


def get_peers_from_list(peer_symbols: List[str]) -> Tuple[bool, Optional[Dict], Optional[str]]:
    """
    Build peers data from an explicit list of symbols (--peers flag).

    Args:
        peer_symbols: List of ticker symbols provided by user.

    Returns:
        Tuple of (success, peers_dict_or_None, error_string_or_None).
    """
    logger.info(f"Using custom peer list: {', '.join(peer_symbols)}")

    # Validate and normalize symbols
    valid_symbols = []
    for sym in peer_symbols:
        try:
            valid_symbols.append(validate_symbol(sym))
        except ValueError as e:
            logger.warning(f"  Skipping invalid peer symbol '{sym}': {e}")

    if not valid_symbols:
        return False, None, "No valid peer symbols in custom list"

    valid_symbols = valid_symbols[:MAX_PEERS_TO_FETCH]

    logger.info(
        f"Enriching {len(valid_symbols)} custom peers with yfinance data...")
    peers_data = _enrich_peers_with_yfinance(valid_symbols)
    peers_data["provider"] = "custom"
    peers_data["filtered"] = False
    peers_data["filter_rationale"] = "User-provided peer list"

    return True, peers_data, None


# ============================================================================
# Peer Filtering via Claude API
# ============================================================================

def filter_peers(
    symbol: str,
    company_name: str,
    industry: str,
    peers_data: Dict
) -> Tuple[Optional[Dict], Optional[str]]:
    """
    Use Claude API to filter peers to true industry peers.

    Sends the full peer list with the target company's name and industry,
    gets back keep/exclude decisions with rationale.

    Args:
        symbol: Target company ticker.
        company_name: Target company name.
        industry: Target company industry.
        peers_data: Peers dict in list-of-lists format.

    Returns:
        Tuple of (filtered_peers_dict_or_None, rationale_or_None).
        Returns (None, None) if ANTHROPIC_API_KEY is not set or filtering fails.
    """
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — skipping peer filtering")
        return None, None

    peer_count = len(peers_data.get('symbol', []))
    if peer_count == 0:
        return None, None

    logger.info(f"Filtering {peer_count} peers using Claude API...")

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)

        # Build peer list description for the prompt
        peer_lines = []
        for i in range(peer_count):
            sym = peers_data['symbol'][i]
            name = peers_data['name'][i] if i < len(
                peers_data.get('name', [])) else sym
            mcap = peers_data['market_cap'][i] if i < len(
                peers_data.get('market_cap', [])) else None
            mcap_str = format_currency(mcap) if mcap else "N/A"
            peer_lines.append(f"- {sym}: {name} (market cap: {mcap_str})")

        peers_text = "\n".join(peer_lines)

        prompt = f"""You are a financial analyst. Given a target company and a list of potential peer companies, determine which are true industry peers suitable for financial comparison.

Target company:
- Symbol: {symbol}
- Name: {company_name}
- Industry: {industry}

Potential peers:
{peers_text}

For each peer, decide whether to KEEP or EXCLUDE it. A true peer should:
1. Operate in the same or closely related industry
2. Have a comparable business model
3. Be a meaningful comparison for financial analysis (not just because they are in the same index)

Respond with valid JSON only (no markdown, no code fences). Use this exact schema:
{{
  "decisions": [
    {{"symbol": "SYM", "keep": true, "reason": "brief reason"}}
  ],
  "rationale": "one sentence summary of filtering logic"
}}"""

        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )

        # Parse the response
        response_text = response.content[0].text.strip()

        # Handle potential markdown code fences in response
        if response_text.startswith('```'):
            lines = response_text.split('\n')
            # Remove first and last lines (code fences)
            lines = [lstr for lstr in lines if not lstr.strip().startswith('```')]
            response_text = '\n'.join(lines)

        filter_result = json.loads(response_text)

        # Build filtered peers data
        keep_symbols = set()
        for decision in filter_result.get('decisions', []):
            if decision.get('keep', False):
                keep_symbols.add(decision['symbol'])

        if not keep_symbols:
            logger.warning(
                "Claude filtered out all peers — keeping original list")
            return None, None

        # Build filtered list preserving order
        filtered = {
            "symbol": [],
            "name": [],
            "price": [],
            "market_cap": [],
        }

        for i in range(peer_count):
            if peers_data['symbol'][i] in keep_symbols:
                filtered['symbol'].append(peers_data['symbol'][i])
                filtered['name'].append(
                    peers_data['name'][i] if i < len(peers_data.get(
                        'name', [])) else peers_data['symbol'][i]
                )
                filtered['price'].append(
                    peers_data['price'][i] if i < len(
                        peers_data.get('price', [])) else None
                )
                filtered['market_cap'].append(
                    peers_data['market_cap'][i] if i < len(
                        peers_data.get('market_cap', [])) else None
                )

        rationale = filter_result.get(
            'rationale',
            f"Filtered from {peer_count} to {len(filtered['symbol'])} peers using Claude API"
        )
        full_rationale = f"Filtered from {peer_count} to {len(filtered['symbol'])} peers using Claude API. {rationale}"

        logger.info(
            f"Peer filtering complete: {peer_count} -> {len(filtered['symbol'])} peers"
        )
        return filtered, full_rationale

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Claude filter response: {e}")
        return None, None
    except Exception as e:
        logger.error(f"Claude peer filtering failed: {e}")
        return None, None


def suggest_and_select_peers(
    symbol: str,
    company_name: str,
    industry: str,
    market_cap: Optional[int],
    peers_data: Dict,
    max_peers: int = 5,
) -> Tuple[Optional[Dict], Optional[str]]:
    """
    Ask Claude to suggest additional peers, then select the top N most comparable.

    Takes the current (filtered) peer list, asks Claude to suggest missing peers,
    enriches any new suggestions via yfinance, then asks Claude to rank and pick
    the best `max_peers` from the combined list.

    Args:
        symbol: Target company ticker.
        company_name: Target company name.
        industry: Target company industry.
        market_cap: Target company market cap (for size comparison).
        peers_data: Current peers dict in list-of-lists format.
        max_peers: Maximum peers to return (default 5).

    Returns:
        Tuple of (selected_peers_dict_or_None, rationale_or_None).
    """
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — skipping peer suggestion")
        return None, None

    current_symbols = peers_data.get('symbol', [])
    peer_count = len(current_symbols)

    logger.info(f"Asking Claude to suggest additional peers beyond {peer_count} current...")

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        # Build current peer list for context
        peer_lines = []
        for i in range(peer_count):
            sym = current_symbols[i]
            name = peers_data['name'][i] if i < len(peers_data.get('name', [])) else sym
            mcap = peers_data['market_cap'][i] if i < len(peers_data.get('market_cap', [])) else None
            mcap_str = format_currency(mcap) if mcap else "N/A"
            peer_lines.append(f"- {sym}: {name} (market cap: {mcap_str})")
        peers_text = "\n".join(peer_lines) if peer_lines else "(none)"

        mcap_str = format_currency(market_cap) if market_cap else "N/A"

        # Step 1: Ask Claude to suggest additional peers
        suggest_prompt = f"""You are a financial analyst identifying peer companies for equity research.

Target company:
- Symbol: {symbol}
- Name: {company_name}
- Industry: {industry}
- Market cap: {mcap_str}

Current peer list:
{peers_text}

Suggest up to 5 additional publicly traded US peers NOT already in the list above. Focus on companies that:
1. Compete directly or operate in the same industry segment
2. Have comparable business models and revenue mix
3. Are of similar scale (market cap within roughly 0.2x to 5x)

Respond with valid JSON only (no markdown, no code fences):
{{"suggestions": ["SYM1", "SYM2", ...], "reasoning": "brief explanation"}}

If the current list is already comprehensive, return {{"suggestions": [], "reasoning": "..."}}.
"""

        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1000,
            messages=[{"role": "user", "content": suggest_prompt}],
        )

        response_text = response.content[0].text.strip()
        if response_text.startswith('```'):
            lines = response_text.split('\n')
            lines = [l for l in lines if not l.strip().startswith('```')]
            response_text = '\n'.join(lines)

        suggest_result = json.loads(response_text)
        new_symbols = suggest_result.get('suggestions', [])
        # Filter out any that are already in the list or are the target
        new_symbols = [s for s in new_symbols if s not in current_symbols and s != symbol]

        if new_symbols:
            logger.info(f"Claude suggested {len(new_symbols)} additional peers: {', '.join(new_symbols)}")
            # Enrich new suggestions with yfinance
            new_data = _enrich_peers_with_yfinance(new_symbols)
            # Merge into combined list
            combined = {
                "symbol": list(current_symbols) + new_data['symbol'],
                "name": list(peers_data.get('name', [])) + new_data['name'],
                "price": list(peers_data.get('price', [])) + new_data['price'],
                "market_cap": list(peers_data.get('market_cap', [])) + new_data['market_cap'],
            }
        else:
            logger.info("Claude had no additional peer suggestions")
            combined = peers_data

        combined_count = len(combined['symbol'])
        if combined_count <= max_peers:
            logger.info(f"Combined list has {combined_count} peers (within limit of {max_peers})")
            return combined, f"Kept all {combined_count} peers (within limit)"

        # Step 2: Ask Claude to rank and select top N
        rank_lines = []
        for i in range(combined_count):
            sym = combined['symbol'][i]
            name = combined['name'][i] if i < len(combined.get('name', [])) else sym
            mcap = combined['market_cap'][i] if i < len(combined.get('market_cap', [])) else None
            mcap_str = format_currency(mcap) if mcap else "N/A"
            rank_lines.append(f"- {sym}: {name} (market cap: {mcap_str})")
        combined_text = "\n".join(rank_lines)

        select_prompt = f"""You are a financial analyst selecting the most comparable peer companies for equity research.

Target company:
- Symbol: {symbol}
- Name: {company_name}
- Industry: {industry}
- Market cap: {format_currency(market_cap) if market_cap else 'N/A'}

Candidate peers:
{combined_text}

Select exactly {max_peers} peers that are the MOST comparable to {symbol} for equity research. Prioritize:
1. Direct competitors in the same business segment
2. Similar business model and revenue mix
3. Comparable market cap and scale
4. Companies that analysts typically compare with {symbol}

Respond with valid JSON only (no markdown, no code fences):
{{"selected": ["SYM1", "SYM2", ...], "rationale": "brief explanation of selections"}}
"""

        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1000,
            messages=[{"role": "user", "content": select_prompt}],
        )

        response_text = response.content[0].text.strip()
        if response_text.startswith('```'):
            lines = response_text.split('\n')
            lines = [l for l in lines if not l.strip().startswith('```')]
            response_text = '\n'.join(lines)

        select_result = json.loads(response_text)
        selected_symbols = select_result.get('selected', [])[:max_peers]

        if not selected_symbols:
            logger.warning("Claude returned empty selection — keeping combined list trimmed")
            selected_symbols = combined['symbol'][:max_peers]

        # Build final peers data preserving order from selection
        selected_set = set(selected_symbols)
        final = {"symbol": [], "name": [], "price": [], "market_cap": []}
        for sym in selected_symbols:
            for i in range(combined_count):
                if combined['symbol'][i] == sym:
                    final['symbol'].append(sym)
                    final['name'].append(combined['name'][i] if i < len(combined.get('name', [])) else sym)
                    final['price'].append(combined['price'][i] if i < len(combined.get('price', [])) else None)
                    final['market_cap'].append(combined['market_cap'][i] if i < len(combined.get('market_cap', [])) else None)
                    break

        rationale = select_result.get('rationale', f"Selected top {max_peers} from {combined_count} candidates")
        logger.info(f"Final peer selection: {combined_count} -> {len(final['symbol'])} peers")
        return final, rationale

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Claude response in peer suggestion: {e}")
        return None, None
    except Exception as e:
        logger.error(f"Claude peer suggestion/selection failed: {e}")
        return None, None


# ============================================================================
# Main Entry Point
# ============================================================================

def main() -> int:
    """
    CLI entry point. Orchestrates profile fetch, peer detection, and filtering.

    Returns:
        Exit code: 0 (success), 1 (partial), 2 (failure).
    """
    parser = argparse.ArgumentParser(
        description='Fetch company profile and detect peer companies'
    )
    parser.add_argument(
        'symbol',
        help='Stock ticker symbol'
    )
    parser.add_argument(
        '--workdir',
        default=None,
        help='Work directory path (default: work/SYMBOL_YYYYMMDD)'
    )
    parser.add_argument(
        '--peers',
        default=None,
        help='Comma-separated custom peer tickers (skip auto-detection)'
    )
    parser.add_argument(
        '--no-filter-peers',
        action='store_true',
        default=False,
        help='Disable Claude-based peer filtering'
    )

    args = parser.parse_args()

    # Validate symbol
    try:
        symbol = validate_symbol(args.symbol)
    except ValueError as e:
        manifest = {
            "status": "error",
            "artifacts": [],
            "error": str(e)
        }
        print(json.dumps(manifest, indent=2))
        return 2

    workdir = Path(args.workdir or default_workdir(symbol))
    artifacts_dir = ensure_directory(workdir / 'artifacts')

    logger.info(f"{'=' * 60}")
    logger.info(f"Research Profile: {symbol}")
    logger.info(f"Work directory: {workdir}")
    logger.info(f"{'=' * 60}")

    artifacts = []
    errors = []

    # ---- Step 1: Company Profile ----
    profile_ok, profile_data, profile_error = get_company_profile(symbol)

    if profile_ok and profile_data:
        profile_path = artifacts_dir / 'profile.json'
        with profile_path.open('w') as f:
            json.dump(profile_data, f, indent=2)
        logger.info(f"Saved {profile_path}")

        mcap_str = format_currency(profile_data['market_cap']) if profile_data.get(
            'market_cap') else 'N/A'
        artifacts.append({
            "name": "profile",
            "path": "artifacts/profile.json",
            "format": "json",
            "source": "yfinance",
            "summary": (
                f"{symbol} {profile_data.get('company_name', 'N/A')} | "
                f"{profile_data.get('industry', 'N/A')} | "
                f"Market cap {mcap_str}"
            ),
        })
    else:
        errors.append(profile_error or "Unknown profile error")
        logger.error(f"Profile fetch failed: {profile_error}")

    # ---- Step 2: Peer Detection ----
    peers_data = None
    peers_provider = None

    if args.peers:
        # Custom peer list
        peer_symbols = [s.strip() for s in args.peers.split(',') if s.strip()]
        peers_ok, peers_data, peers_error = get_peers_from_list(peer_symbols)
        if peers_ok:
            peers_provider = "custom"
        else:
            errors.append(peers_error or "Custom peer list failed")
            logger.error(f"Custom peer list failed: {peers_error}")
    else:
        # Auto-detect peers
        peers_ok, peers_data, peers_error = get_peers(symbol)
        if peers_ok:
            peers_provider = peers_data.get('provider', 'unknown')
        else:
            errors.append(peers_error or "Peer detection failed")
            logger.error(f"Peer detection failed: {peers_error}")

    # ---- Step 3: Peer Filtering ----
    original_count = len(peers_data['symbol']) if peers_data else 0

    if (
        peers_data
        and not args.no_filter_peers
        and peers_provider != "custom"
        and profile_ok
        and profile_data
    ):
        filtered_peers, filter_rationale = filter_peers(
            symbol,
            profile_data.get('company_name', symbol),
            profile_data.get('industry', 'N/A'),
            peers_data,
        )
        if filtered_peers is not None:
            filtered_peers['provider'] = peers_data.get('provider', 'unknown')
            filtered_peers['filtered'] = True
            filtered_peers['filter_rationale'] = filter_rationale
            peers_data = filtered_peers
        # If filtering returned None, keep the original unfiltered peers_data

    # ---- Step 3b: Suggest additional peers and select top 5 ----
    if (
        peers_data
        and not args.no_filter_peers
        and profile_ok
        and profile_data
    ):
        selected_peers, select_rationale = suggest_and_select_peers(
            symbol,
            profile_data.get('company_name', symbol),
            profile_data.get('industry', 'N/A'),
            profile_data.get('market_cap'),
            peers_data,
            max_peers=5,
        )
        if selected_peers is not None:
            selected_peers['provider'] = peers_data.get('provider', 'unknown')
            selected_peers['filtered'] = True
            selected_peers['filter_rationale'] = select_rationale
            peers_data = selected_peers

    # ---- Step 4: Save Peers ----
    if peers_data:
        peers_path = artifacts_dir / 'peers_list.json'
        with peers_path.open('w') as f:
            json.dump(peers_data, f, indent=2)
        logger.info(f"Saved {peers_path}")

        peer_count = len(peers_data.get('symbol', []))
        peer_names = ', '.join(peers_data.get('symbol', []))
        source_str = f"{peers_provider}+yfinance" if peers_provider != "custom" else "custom+yfinance"

        filtered_note = ""
        if peers_data.get('filtered'):
            filtered_note = f" (filtered from {original_count})"

        artifacts.append({
            "name": "peers_list",
            "path": "artifacts/peers_list.json",
            "format": "json",
            "source": source_str,
            "summary": f"{peer_count} peers: {peer_names}{filtered_note}",
        })
    else:
        # Save empty peers list so downstream tasks have a file to read
        empty_peers = {
            "symbol": [],
            "name": [],
            "price": [],
            "market_cap": [],
            "provider": None,
            "filtered": False,
            "filter_rationale": None,
        }
        peers_path = artifacts_dir / 'peers_list.json'
        with peers_path.open('w') as f:
            json.dump(empty_peers, f, indent=2)
        logger.info(f"Saved empty peers list to {peers_path}")

        artifacts.append({
            "name": "peers_list",
            "path": "artifacts/peers_list.json",
            "format": "json",
            "source": "none",
            "summary": "0 peers (all providers failed)",
        })

    # ---- Determine exit status ----
    if profile_ok and peers_data and len(peers_data.get('symbol', [])) > 0:
        status = "complete"
        exit_code = 0
    elif profile_ok or (peers_data and len(peers_data.get('symbol', [])) > 0):
        status = "partial"
        exit_code = 1
    else:
        status = "error"
        exit_code = 2

    # ---- Print manifest to stdout ----
    manifest = {
        "status": status,
        "artifacts": artifacts,
        "error": "; ".join(errors) if errors else None,
    }

    logger.info(f"\nStatus: {status}")
    if errors:
        logger.error(f"Errors: {'; '.join(errors)}")
    logger.info(f"{'=' * 60}")

    # Only JSON manifest goes to stdout
    print(json.dumps(manifest, indent=2))

    return exit_code


if __name__ == '__main__':
    sys.exit(main())
