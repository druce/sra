#!/usr/bin/env python3
"""
Wikipedia Research Skill — Fetch company summary from Wikipedia.

Searches Wikipedia for the given stock symbol's company and extracts the
lead-section summary and full page content.  Saves the results as text
artifacts and emits a JSON manifest on stdout.

Usage:
    ./skills/research_wikipedia.py SYMBOL --workdir DIR

Exit codes:
    0  success
    1  summary very short or possibly wrong page
    2  no Wikipedia page found
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import wikipedia

# Add skills directory to path for local imports
_SKILLS_DIR = Path(__file__).resolve().parent.parent
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

# isort: split

from config import MAX_RETRIES, RETRY_DELAY_SECONDS  # noqa: E402
from utils import (  # noqa: E402
    setup_logging, validate_symbol, ensure_directory, default_workdir,
    resolve_company_name,
)

logger = setup_logging(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_company_name(symbol: str, workdir: str) -> str:
    """
    Resolve a human-readable company name for *symbol*.

    Priority:
        1. ``{workdir}/artifacts/profile.json``  (via resolve_company_name)
        2. yfinance ``ticker.info['longName']``
        3. The raw *symbol* string as a last resort
    """
    # 1. Try profile.json (shared logic)
    name = resolve_company_name(symbol, workdir)
    if name != symbol:
        logger.info("Company name from profile.json: %s", name)
        return name

    # 2. Try yfinance (wikipedia-specific fallback for better search terms)
    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
        yf_name = info.get("longName")
        if yf_name:
            logger.info("Company name from yfinance: %s", yf_name)
            return yf_name
    except Exception as exc:
        logger.warning("yfinance lookup failed: %s", exc)

    # 3. Fallback to symbol
    logger.info("Using symbol as company name: %s", symbol)
    return symbol


def fetch_wikipedia_summary(
    company_name: str,
    symbol: str,
) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
    """
    Search Wikipedia for *company_name* / *symbol* and return the lead summary
    and full page content.

    Returns:
        (success, summary_text, full_content, page_title)

        *success* is ``True`` when a reasonable summary was retrieved.
    """
    search_terms = [
        company_name,
        f"{company_name} company",
        f"{symbol} stock",
    ]

    for term in search_terms:
        result = _try_wikipedia_search(term)
        if result is not None:
            success, summary, full_content, title = result
            if success:
                return True, summary, full_content, title

    return False, None, None, None


def _try_wikipedia_search(
    term: str,
) -> Optional[Tuple[bool, Optional[str], Optional[str], Optional[str]]]:
    """
    Attempt a single Wikipedia search for *term*.

    Handles disambiguation pages by retrying with ``" (company)"`` appended.
    Retries on network / timeout errors up to ``MAX_RETRIES`` times.

    Returns ``None`` when no results are found at all, or a
    ``(success, summary, full_content, title)`` tuple.
    """
    attempts = 0
    while attempts < MAX_RETRIES:
        try:
            results = wikipedia.search(term)
            if not results:
                logger.info("No Wikipedia results for: %s", term)
                return None

            page_title = results[0]
            return _fetch_page(page_title)

        except wikipedia.exceptions.DisambiguationError as exc:
            logger.info("Disambiguation page for '%s', retrying with '(company)'", term)
            return _try_disambiguation(term, exc)

        except (wikipedia.exceptions.WikipediaException, Exception) as exc:
            attempts += 1
            if attempts < MAX_RETRIES:
                logger.warning(
                    "Wikipedia error (attempt %d/%d): %s — retrying in %ds",
                    attempts,
                    MAX_RETRIES,
                    exc,
                    RETRY_DELAY_SECONDS,
                )
                time.sleep(RETRY_DELAY_SECONDS)
            else:
                logger.error("Wikipedia lookup failed after %d attempts: %s", MAX_RETRIES, exc)
                return None

    return None


def _fetch_page(
    page_title: str,
) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
    """
    Fetch a Wikipedia page by *page_title* and return its summary and full content.

    Returns ``(success, summary, full_content, title)``.  *success* is ``False``
    when the summary is suspiciously short (< 100 chars).
    """
    attempts = 0
    while attempts < MAX_RETRIES:
        try:
            page = wikipedia.page(page_title, auto_suggest=False)
            summary = page.summary
            full_content = page.content

            if len(summary) < 100:
                logger.warning(
                    "Summary for '%s' very short (%d chars) — likely wrong page",
                    page_title,
                    len(summary),
                )
                return False, summary, full_content, page.title

            logger.info(
                "Fetched Wikipedia summary for '%s' (%d chars)",
                page.title,
                len(summary),
            )
            return True, summary, full_content, page.title

        except wikipedia.exceptions.DisambiguationError as exc:
            logger.info("Disambiguation page for '%s'", page_title)
            return _try_disambiguation(page_title, exc)

        except wikipedia.exceptions.PageError:
            logger.warning("Wikipedia page not found: %s", page_title)
            return None

        except (wikipedia.exceptions.WikipediaException, Exception) as exc:
            attempts += 1
            if attempts < MAX_RETRIES:
                logger.warning(
                    "Wikipedia fetch error (attempt %d/%d): %s — retrying in %ds",
                    attempts,
                    MAX_RETRIES,
                    exc,
                    RETRY_DELAY_SECONDS,
                )
                time.sleep(RETRY_DELAY_SECONDS)
            else:
                logger.error(
                    "Wikipedia fetch failed after %d attempts: %s",
                    MAX_RETRIES,
                    exc,
                )
                return None

    return None


def _try_disambiguation(
    original_term: str,
    exc: wikipedia.exceptions.DisambiguationError,
) -> Optional[Tuple[bool, Optional[str], Optional[str], Optional[str]]]:
    """
    Handle a disambiguation page by looking for a ``"(company)"`` variant
    among the listed options, or by searching with the suffix appended.
    """
    company_variant = f"{original_term} (company)"

    # Check if one of the disambiguation options contains "(company)"
    for option in getattr(exc, "options", []):
        if "(company)" in option.lower():
            logger.info("Found company variant in disambiguation: %s", option)
            return _fetch_page(option)

    # Otherwise search explicitly
    try:
        results = wikipedia.search(company_variant)
        if results:
            return _fetch_page(results[0])
    except Exception as search_exc:
        logger.warning("Disambiguation retry failed: %s", search_exc)

    return None


def _build_summary_line(company_name: str, summary: str) -> str:
    """
    Build the one-line description used in the manifest ``summary`` field.

    Format: ``"Company Name — 1,847 chars, covers founding, products, market position"``
    """
    char_count = len(summary)

    # Extract a few topic hints from the first ~500 chars of the summary
    topics = []
    text_lower = summary[:500].lower()
    topic_keywords = {
        "founding": ["founded", "established", "incorporated"],
        "products": ["products", "manufactures", "produces", "develops"],
        "services": ["services", "provides", "offers"],
        "market position": ["market", "largest", "leading", "Fortune"],
        "headquarters": ["headquartered", "headquarters", "based in"],
        "history": ["history", "originally", "formerly"],
        "revenue": ["revenue", "earnings", "profit"],
        "employees": ["employees", "workforce", "staff"],
    }

    for topic, keywords in topic_keywords.items():
        if any(kw in text_lower for kw in keywords):
            topics.append(topic)
        if len(topics) >= 3:
            break

    topic_str = ", ".join(topics) if topics else "general overview"
    return f"{company_name} — {char_count:,} chars, covers {topic_str}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch Wikipedia summary for a stock symbol's company.",
    )
    parser.add_argument("symbol", help="Stock ticker symbol (e.g. TSLA)")
    parser.add_argument("--workdir", default=None, help="Working directory path (default: work/SYMBOL_YYYYMMDD)")

    args = parser.parse_args()

    # Validate symbol
    try:
        symbol = validate_symbol(args.symbol)
    except ValueError as exc:
        logger.error("Invalid symbol: %s", exc)
        manifest = {"status": "error", "artifacts": [], "error": str(exc)}
        print(json.dumps(manifest))
        return 2

    workdir = args.workdir or default_workdir(symbol)

    # Ensure artifacts directory exists
    artifacts_dir = ensure_directory(Path(workdir) / "artifacts")

    # Step 1: Resolve company name
    logger.info("Resolving company name for %s ...", symbol)
    company_name = get_company_name(symbol, workdir)
    logger.info("Company name: %s", company_name)

    # Step 2: Fetch Wikipedia summary
    logger.info("Searching Wikipedia for '%s' ...", company_name)
    success, summary, full_content, page_title = fetch_wikipedia_summary(company_name, symbol)

    if summary is None:
        logger.error("No Wikipedia page found for %s / %s", company_name, symbol)
        manifest = {
            "status": "error",
            "artifacts": [],
            "error": f"No Wikipedia page found for {company_name} ({symbol})",
        }
        print(json.dumps(manifest))
        return 2

    # Step 3: Save summary artifact
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    output_path = artifacts_dir / "wikipedia_summary.txt"

    header = (
        f"Wikipedia Summary - {symbol}\n"
        f"Company: {company_name}\n"
        f"Generated: {timestamp}\n"
        f"{'=' * 40}\n"
    )
    output_path.write_text(header + "\n" + summary, encoding="utf-8")
    logger.info("Saved Wikipedia summary to %s", output_path)

    # Step 3b: Save full page artifact
    full_output_path = artifacts_dir / "wikipedia_full.txt"
    full_header = (
        f"Wikipedia Full Article - {symbol}\n"
        f"Company: {company_name}\n"
        f"Page: {page_title}\n"
        f"Generated: {timestamp}\n"
        f"{'=' * 40}\n"
    )
    full_output_path.write_text(full_header + "\n" + full_content, encoding="utf-8")
    logger.info("Saved full Wikipedia article to %s (%d chars)", full_output_path, len(full_content))

    # Step 4: Emit manifest
    summary_line = _build_summary_line(company_name, summary)
    manifest = {
        "status": "complete",
        "artifacts": [
            {
                "name": "wikipedia_summary",
                "path": "artifacts/wikipedia_summary.txt",
                "format": "txt",
                "source": "wikipedia",
                "summary": summary_line,
            },
            {
                "name": "wikipedia_full",
                "path": "artifacts/wikipedia_full.txt",
                "format": "txt",
                "source": "wikipedia",
                "summary": f"{company_name} — full Wikipedia article, {len(full_content):,} chars",
            },
        ],
        "error": None,
    }
    print(json.dumps(manifest))

    if not success:
        # Summary was retrieved but is very short / possibly wrong page
        logger.warning("Summary may be incorrect or very short (%d chars)", len(summary))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
