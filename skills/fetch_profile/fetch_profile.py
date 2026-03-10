#!/usr/bin/env python3
"""
Company Profile Skill — fetch_profile.py

Fetches company profile metadata from yfinance.

Usage:
    ./skills/fetch_profile/fetch_profile.py SYMBOL --workdir DIR

Output:
    Writes profile.json to {workdir}/artifacts/.
    Prints JSON manifest to stdout.
    All progress/diagnostic output goes to stderr.

Exit codes:
    0 - success
    2 - failure
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

import yfinance as yf

# Add skills directory to path for local imports
_SKILLS_DIR = Path(__file__).resolve().parent.parent
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

# isort: split

from utils import setup_logging, validate_symbol, ensure_directory, format_currency, load_environment, default_workdir  # noqa: E402

load_environment()

logger = setup_logging(__name__)


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


def main() -> int:
    """
    CLI entry point. Fetches company profile and saves to artifacts/.

    Returns:
        Exit code: 0 (success), 2 (failure).
    """
    parser = argparse.ArgumentParser(
        description='Fetch company profile data'
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
    logger.info(f"Fetch Profile: {symbol}")
    logger.info(f"Work directory: {workdir}")
    logger.info(f"{'=' * 60}")

    # ---- Fetch Company Profile ----
    profile_ok, profile_data, profile_error = get_company_profile(symbol)

    if profile_ok and profile_data:
        profile_path = artifacts_dir / 'profile.json'
        with profile_path.open('w') as f:
            json.dump(profile_data, f, indent=2)
        logger.info(f"Saved {profile_path}")

        mcap_str = format_currency(profile_data['market_cap']) if profile_data.get(
            'market_cap') else 'N/A'

        manifest = {
            "status": "complete",
            "artifacts": [{
                "name": "profile",
                "path": "artifacts/profile.json",
                "format": "json",
                "source": "yfinance",
                "summary": (
                    f"{symbol} {profile_data.get('company_name', 'N/A')} | "
                    f"{profile_data.get('industry', 'N/A')} | "
                    f"Market cap {mcap_str}"
                ),
            }],
            "error": None,
        }
        print(json.dumps(manifest, indent=2))
        return 0
    else:
        logger.error(f"Profile fetch failed: {profile_error}")
        manifest = {
            "status": "error",
            "artifacts": [],
            "error": profile_error or "Unknown profile error",
        }
        print(json.dumps(manifest, indent=2))
        return 2


if __name__ == '__main__':
    sys.exit(main())
