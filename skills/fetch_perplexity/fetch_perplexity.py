#!/usr/bin/env python3
"""
Perplexity AI Research — news stories, business profile, and executive profiles.

Queries Perplexity's sonar-pro model for qualitative research on a public company
and saves Markdown artifacts. Outputs a JSON manifest to stdout; all progress and
diagnostic output goes to stderr.

Usage:
    ./skills/fetch_perplexity/fetch_perplexity.py SYMBOL --workdir DIR

Exit codes:
    0  All three research sections succeeded
    1  Partial success (at least one section succeeded)
    2  Complete failure (no sections succeeded)

Requires:
    PERPLEXITY_API_KEY environment variable (or in .env file)

Python packages: openai, yfinance, python-dotenv
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

from openai import OpenAI

# Add skills directory to path for local imports
_SKILLS_DIR = Path(__file__).resolve().parent.parent
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

# isort: split

from config import (  # noqa: E402
    PERPLEXITY_MODEL,
    PERPLEXITY_TEMPERATURE,
    PERPLEXITY_MAX_TOKENS,
    NEWS_STORIES_COUNT,
    NEWS_STORIES_SINCE,
    MAX_RETRIES,
    RETRY_DELAY_SECONDS,
    RETRY_BACKOFF_MULTIPLIER,
)
from utils import setup_logging, validate_symbol, ensure_directory, load_environment, default_workdir  # noqa: E402

load_environment()

logger = setup_logging(__name__)


# ============================================================================
# Company name resolution
# ============================================================================

def get_company_name(symbol: str, workdir: str) -> str:
    """
    Resolve human-readable company name for a ticker symbol.

    Priority:
        1. Read from {workdir}/artifacts/profile.json (company_name field)
        2. Fallback: yfinance ticker.info['longName']
        3. Fallback: use the symbol itself

    Args:
        symbol: Stock ticker symbol (e.g. 'AAPL')
        workdir: Working directory path

    Returns:
        Company name string
    """
    # Priority 1: profile.json in artifacts/
    profile_path = Path(workdir) / "artifacts" / "profile.json"
    if profile_path.exists():
        try:
            with profile_path.open("r") as f:
                profile = json.load(f)
            company_name = profile.get("company_name")
            if company_name:
                logger.info("Company name from profile.json: %s", company_name)
                return company_name
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Could not read profile.json: %s", e)

    # Priority 2: yfinance
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        long_name = ticker.info.get("longName")
        if long_name:
            logger.info("Company name from yfinance: %s", long_name)
            return long_name
    except Exception as e:
        logger.warning("yfinance lookup failed: %s", e)

    # Priority 3: symbol itself
    logger.info("Using symbol as company name: %s", symbol)
    return symbol


# ============================================================================
# Perplexity API query
# ============================================================================

def query_perplexity(
    prompt: str,
    model: str = PERPLEXITY_MODEL,
    temperature: float = PERPLEXITY_TEMPERATURE,
    max_tokens: int = 4000,
    max_retries: int = MAX_RETRIES,
) -> Optional[str]:
    """
    Query Perplexity AI using the OpenAI-compatible client.

    Implements exponential backoff retry on failure.

    Args:
        prompt: User prompt to send
        model: Perplexity model name (default: sonar-pro)
        temperature: Sampling temperature (default: 0.2)
        max_tokens: Maximum response tokens
        max_retries: Number of retry attempts

    Returns:
        Response text content, or None on failure
    """
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        logger.error("PERPLEXITY_API_KEY not set")
        return None

    client = OpenAI(api_key=api_key, base_url="https://api.perplexity.ai")

    delay = RETRY_DELAY_SECONDS
    for attempt in range(1, max_retries + 1):
        try:
            logger.info("  Perplexity query attempt %d/%d (model=%s, max_tokens=%d)",
                        attempt, max_retries, model, max_tokens)

            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a financial research analyst providing "
                            "detailed, factual analysis of public companies. "
                            "Cite sources where possible and use specific "
                            "numbers, dates, and data points."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )

            content = response.choices[0].message.content
            if content:
                logger.info("  Received %d characters", len(content))
                return content
            else:
                logger.warning("  Empty response from Perplexity")

        except Exception as e:
            logger.error("  Attempt %d failed: %s", attempt, e)
            if attempt < max_retries:
                logger.info("  Retrying in %ss...", delay)
                time.sleep(delay)
                delay *= RETRY_BACKOFF_MULTIPLIER

    logger.error("  All retry attempts exhausted")
    return None


# ============================================================================
# Research sections
# ============================================================================

def save_news_research(symbol: str, workdir: str, company_identifier: str) -> bool:
    """
    Query Perplexity for major news stories and save to Markdown.

    Args:
        symbol: Stock ticker symbol
        workdir: Working directory path
        company_identifier: Human-readable company name

    Returns:
        True if research was saved successfully
    """
    logger.info("--- News Research for %s (%s) ---", company_identifier, symbol)

    prompt = (
        f"Provide a comprehensive summary of the {NEWS_STORIES_COUNT} most significant "
        f"news stories and developments for {company_identifier} (ticker: {symbol}) "
        f"since {NEWS_STORIES_SINCE}. For each story, include:\n\n"
        f"1. **Date** — when the event occurred or was announced\n"
        f"2. **Headline** — a concise summary of the news\n"
        f"3. **Details** — 2-3 sentences explaining the significance and impact\n"
        f"4. **Market Impact** — how the stock or market reacted, if known\n\n"
        f"Focus on material events such as:\n"
        f"- Earnings surprises (beats or misses)\n"
        f"- Major product launches or strategic announcements\n"
        f"- M&A activity (acquisitions, divestitures, mergers)\n"
        f"- Leadership changes (CEO, CFO, board members)\n"
        f"- Regulatory actions or legal developments\n"
        f"- Significant partnerships or contract wins\n"
        f"- Guidance changes or analyst rating shifts\n"
        f"- Any controversies or reputational events\n\n"
        f"Order stories chronologically from most recent to oldest. "
        f"Use Markdown formatting with headers for each story."
    )

    content = query_perplexity(
        prompt,
        max_tokens=PERPLEXITY_MAX_TOKENS.get("news_stories", 4000),
    )

    if not content:
        logger.error("Failed to retrieve news research")
        return False

    output_dir = ensure_directory(Path(workdir) / "artifacts")
    output_path = output_dir / "perplexity_news_stories.md"

    header = (
        f"# Major News Stories: {company_identifier} ({symbol})\n\n"
        f"_Research generated via Perplexity AI ({PERPLEXITY_MODEL}) | "
        f"Stories since {NEWS_STORIES_SINCE}_\n\n---\n\n"
    )

    with output_path.open("w") as f:
        f.write(header + content)

    logger.info("Saved news research to %s", output_path)
    return True


def save_business_profile(symbol: str, workdir: str, company_identifier: str) -> bool:
    """
    Query Perplexity for a 10-section business profile and save to Markdown.

    Args:
        symbol: Stock ticker symbol
        workdir: Working directory path
        company_identifier: Human-readable company name

    Returns:
        True if profile was saved successfully
    """
    logger.info("--- Business Profile for %s (%s) ---", company_identifier, symbol)

    prompt = (
        f"Provide a comprehensive business profile of {company_identifier} "
        f"(ticker: {symbol}) covering the following 10 sections. "
        f"Use detailed analysis with specific data points, numbers, and dates.\n\n"
        f"## 1. Company Overview\n"
        f"History, founding, headquarters, mission, and current market position.\n\n"
        f"## 2. Business Model & Revenue Streams\n"
        f"How the company makes money. Break down revenue by segment, product, "
        f"and geography. Include recent revenue figures.\n\n"
        f"## 3. Products & Services\n"
        f"Key products and services, market share, and competitive positioning "
        f"for each major offering.\n\n"
        f"## 4. Industry & Market Analysis\n"
        f"Total addressable market (TAM), industry growth rate, market trends, "
        f"and the company's position within the industry.\n\n"
        f"## 5. Competitive Landscape\n"
        f"Major competitors, competitive advantages (moats), and areas of "
        f"competitive vulnerability.\n\n"
        f"## 6. Financial Performance Summary\n"
        f"Recent revenue, earnings, margins, and growth trends. Key financial "
        f"ratios and how they compare to peers.\n\n"
        f"## 7. Growth Strategy\n"
        f"Management's stated growth priorities, recent strategic initiatives, "
        f"R&D investments, and expansion plans.\n\n"
        f"## 8. Risk Factors\n"
        f"Key business, regulatory, competitive, and macroeconomic risks.\n\n"
        f"## 9. ESG & Corporate Governance\n"
        f"Environmental, social, and governance practices. Board structure, "
        f"shareholder rights, and any ESG controversies.\n\n"
        f"## 10. Recent Developments & Outlook\n"
        f"Latest quarterly results, guidance, analyst consensus, and "
        f"forward-looking catalysts or headwinds.\n\n"
        f"Use Markdown formatting with clear section headers."
    )

    content = query_perplexity(
        prompt,
        max_tokens=PERPLEXITY_MAX_TOKENS.get("business_profile", 8000),
    )

    if not content:
        logger.error("Failed to retrieve business profile")
        return False

    output_dir = ensure_directory(Path(workdir) / "artifacts")
    output_path = output_dir / "perplexity_business_profile.md"

    header = (
        f"# Business Profile: {company_identifier} ({symbol})\n\n"
        f"_Research generated via Perplexity AI ({PERPLEXITY_MODEL})_\n\n---\n\n"
    )

    with output_path.open("w") as f:
        f.write(header + content)

    logger.info("Saved business profile to %s", output_path)
    return True


def save_executive_profiles(symbol: str, workdir: str, company_identifier: str) -> bool:
    """
    Query Perplexity for C-suite executive profiles and save to Markdown.

    Args:
        symbol: Stock ticker symbol
        workdir: Working directory path
        company_identifier: Human-readable company name

    Returns:
        True if profiles were saved successfully
    """
    logger.info("--- Executive Profiles for %s (%s) ---", company_identifier, symbol)

    prompt = (
        f"Provide detailed profiles of the key C-suite executives at "
        f"{company_identifier} (ticker: {symbol}). For each executive, include:\n\n"
        f"1. **Name and Title** — full name and current title\n"
        f"2. **Background** — education, career history, and how they arrived "
        f"at their current role\n"
        f"3. **Tenure** — when they joined the company and assumed their "
        f"current role\n"
        f"4. **Compensation** — most recent total compensation package "
        f"(base salary, bonus, stock awards, total) from proxy filings\n"
        f"5. **Key Accomplishments** — notable achievements in their role\n"
        f"6. **Leadership Style & Reputation** — market perception and "
        f"management approach\n\n"
        f"Cover at minimum the following roles (if applicable):\n"
        f"- Chief Executive Officer (CEO)\n"
        f"- Chief Financial Officer (CFO)\n"
        f"- Chief Operating Officer (COO)\n"
        f"- Chief Technology Officer (CTO)\n"
        f"- Other notable C-suite or key executives\n\n"
        f"Include any recent leadership changes or succession planning. "
        f"Use Markdown formatting with a clear header for each executive."
    )

    content = query_perplexity(
        prompt,
        max_tokens=PERPLEXITY_MAX_TOKENS.get("executive_profiles", 4000),
    )

    if not content:
        logger.error("Failed to retrieve executive profiles")
        return False

    output_dir = ensure_directory(Path(workdir) / "artifacts")
    output_path = output_dir / "perplexity_executive_profiles.md"

    header = (
        f"# Executive Profiles: {company_identifier} ({symbol})\n\n"
        f"_Research generated via Perplexity AI ({PERPLEXITY_MODEL})_\n\n---\n\n"
    )

    with output_path.open("w") as f:
        f.write(header + content)

    logger.info("Saved executive profiles to %s", output_path)
    return True


# ============================================================================
# Main
# ============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Perplexity AI research: news, business profile, executive profiles"
    )
    parser.add_argument("symbol", help="Stock ticker symbol (e.g. AAPL)")
    parser.add_argument("--workdir", default=None, help="Working directory path (default: work/SYMBOL_YYYYMMDD)")
    args = parser.parse_args()

    # Validate symbol
    try:
        symbol = validate_symbol(args.symbol)
    except ValueError as e:
        logger.error("%s", e)
        manifest = {"status": "error", "artifacts": [], "error": str(e)}
        print(json.dumps(manifest, indent=2))
        return 2

    workdir = args.workdir or default_workdir(symbol)
    logger.info("Starting Perplexity research for %s", symbol)
    logger.info("Working directory: %s", workdir)

    # Check API key
    if not os.environ.get("PERPLEXITY_API_KEY"):
        msg = "PERPLEXITY_API_KEY environment variable not set"
        logger.error("%s", msg)
        manifest = {"status": "error", "artifacts": [], "error": msg}
        print(json.dumps(manifest, indent=2))
        return 2

    # Ensure artifacts directory exists
    ensure_directory(Path(workdir) / "artifacts")

    # Resolve company name
    company_identifier = get_company_name(symbol, workdir)
    logger.info("Company identifier: %s", company_identifier)

    # Run all three research sections
    results = {}
    results["news_stories"] = save_news_research(symbol, workdir, company_identifier)
    results["business_profile"] = save_business_profile(symbol, workdir, company_identifier)
    results["executive_profiles"] = save_executive_profiles(symbol, workdir, company_identifier)

    # Build manifest
    succeeded = sum(1 for v in results.values() if v)
    total = len(results)

    logger.info("Results: %d/%d sections completed", succeeded, total)

    artifacts = []
    if results["news_stories"]:
        artifacts.append({
            "name": "news_stories",
            "path": "artifacts/perplexity_news_stories.md",
            "format": "md",
            "source": "perplexity",
            "summary": f"{NEWS_STORIES_COUNT} major news stories since {NEWS_STORIES_SINCE}",
        })
    if results["business_profile"]:
        artifacts.append({
            "name": "business_profile",
            "path": "artifacts/perplexity_business_profile.md",
            "format": "md",
            "source": "perplexity",
            "summary": "10-section business profile",
        })
    if results["executive_profiles"]:
        artifacts.append({
            "name": "executive_profiles",
            "path": "artifacts/perplexity_executive_profiles.md",
            "format": "md",
            "source": "perplexity",
            "summary": "CEO, CFO, COO profiles with compensation",
        })

    if succeeded == total:
        status = "complete"
        error = None
    elif succeeded > 0:
        failed_sections = [k for k, v in results.items() if not v]
        status = "partial"
        error = f"Failed sections: {', '.join(failed_sections)}"
    else:
        status = "error"
        error = "All research sections failed"

    manifest = {
        "status": status,
        "artifacts": artifacts,
        "error": error,
    }

    # JSON manifest to stdout
    print(json.dumps(manifest, indent=2))

    # Exit code
    if succeeded == total:
        logger.info("All research sections completed successfully")
        return 0
    elif succeeded > 0:
        logger.warning("Partial success: %d/%d sections", succeeded, total)
        return 1
    else:
        logger.error("All research sections failed")
        return 2


if __name__ == "__main__":
    sys.exit(main())
