#!/usr/bin/env python3
"""
Perplexity Analysis Skill — fetch_perplexity_analysis.py

Generates four focused analysis documents using Perplexity AI:
  - Business model analysis (revenue mechanics, unit economics, moat)
  - Competitive analysis (market share, positioning, differentiation)
  - Risk analysis (categorized: operational, financial, regulatory, market)
  - Investment thesis (bull/bear/base cases, SWOT, catalysts)

Usage:
    ./skills/fetch_perplexity_analysis/fetch_perplexity_analysis.py SYMBOL --workdir DIR

Output:
    - perplexity_analysis_business_model.md    Business model & moat analysis
    - perplexity_analysis_competitive.md       Competitive landscape
    - perplexity_analysis_risk.md              Categorized risk analysis
    - perplexity_analysis_investment_thesis.md Bull/bear/base, SWOT, catalysts

    Prints JSON manifest to stdout.
    All progress/diagnostic output goes to stderr.

Exit codes:
    0 - success (all 4 analyses produced)
    1 - partial (some analyses produced)
    2 - failure (nothing produced)
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import yfinance as yf
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
    MAX_RETRIES,
    RETRY_DELAY_SECONDS,
    RETRY_BACKOFF_MULTIPLIER,
)
from utils import setup_logging, validate_symbol, ensure_directory, load_environment  # noqa: E402

load_environment()

# Set up logging (all output to stderr via StreamHandler)
logger = setup_logging(__name__)


# ============================================================================
# Company Name Resolution
# ============================================================================

def get_company_name(symbol: str, workdir: Path) -> str:
    """
    Get company name for a symbol, trying multiple sources.

    Priority:
    1. Read from {workdir}/artifacts/profile.json (from profile phase)
    2. Fall back to yfinance lookup
    3. Fall back to raw symbol

    Args:
        symbol: Stock ticker symbol (uppercase).
        workdir: Work directory path.

    Returns:
        Company name string, or the symbol itself as last resort.
    """
    # Try profile.json first
    profile_path = workdir / 'artifacts' / 'profile.json'
    if profile_path.exists():
        try:
            with profile_path.open('r') as f:
                profile = json.load(f)
                company_name = profile.get('company_name')
                if company_name and company_name != 'N/A':
                    logger.info("Company name from profile.json: %s", company_name)
                    return company_name
        except (IOError, json.JSONDecodeError) as e:
            logger.warning("Could not read profile.json: %s", e)

    # Fall back to yfinance
    try:
        logger.info("Looking up company name for %s via yfinance...", symbol)
        ticker = yf.Ticker(symbol)
        info = ticker.info
        company_name = info.get('longName') or info.get('shortName')
        if company_name:
            logger.info("Found: %s", company_name)
            return company_name
    except Exception as e:
        logger.warning("yfinance lookup failed: %s", e)

    # Last resort
    logger.info("Using symbol as fallback for company name")
    return symbol


# ============================================================================
# Perplexity Query with Retry
# ============================================================================

def query_perplexity(prompt: str, max_tokens: int = 4000) -> Optional[str]:
    """
    Query Perplexity AI with retry logic and exponential backoff.

    Args:
        prompt: The user prompt to send.
        max_tokens: Maximum tokens in the response.

    Returns:
        Response text if successful, None if all retries failed.
    """
    api_key = os.getenv('PERPLEXITY_API_KEY')
    if not api_key:
        logger.error("PERPLEXITY_API_KEY not found in environment")
        return None

    client = OpenAI(api_key=api_key, base_url="https://api.perplexity.ai")

    for attempt in range(MAX_RETRIES):
        try:
            logger.info("  Querying Perplexity (attempt %d/%d)...", attempt + 1, MAX_RETRIES)

            response = client.chat.completions.create(
                model=PERPLEXITY_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a financial research analyst. Provide detailed, well-sourced analysis with specific data points and citations."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=PERPLEXITY_TEMPERATURE,
                max_tokens=max_tokens,
            )

            result = response.choices[0].message.content
            logger.info("  Received response (%d characters)", len(result))
            return result

        except Exception as e:
            logger.warning("  Attempt %d failed: %s", attempt + 1, e)

            if attempt < MAX_RETRIES - 1:
                wait_time = (RETRY_BACKOFF_MULTIPLIER ** attempt) * RETRY_DELAY_SECONDS
                logger.info("  Waiting %ss before retry...", wait_time)
                time.sleep(wait_time)
            else:
                logger.error("  All %d retry attempts failed", MAX_RETRIES)
                return None

    return None


# ============================================================================
# Business Model Analysis
# ============================================================================

def save_business_model_analysis(
    symbol: str,
    workdir: Path,
    company_identifier: str,
) -> bool:
    """
    Query Perplexity for business model analysis and save to markdown.

    Covers revenue streams and mix, unit economics, competitive moat,
    supply chain dependencies, and growth/reinvestment strategy.

    Args:
        symbol: Stock ticker symbol.
        workdir: Work directory path.
        company_identifier: Human-readable company name with symbol.

    Returns:
        True if the artifact was written successfully, False otherwise.
    """
    logger.info("Generating business model analysis for %s...", company_identifier)

    prompt = f"""Provide a detailed business model analysis for {company_identifier}.

Structure your analysis with the following sections and depth:

## 1. Revenue Streams & Mix
- Break down revenue by business segment with exact dollar amounts and percentages from the most recent fiscal year and trailing twelve months.
- Break down revenue by geography (domestic vs international, and by major region).
- Identify which segments are growing fastest and which are declining.
- Note any revenue concentration risks (single customer, single product, etc.).

## 2. Unit Economics & Margins by Segment
- Gross margin by segment and how it has trended over the last 3 years.
- Operating margin by segment with specific figures.
- Customer acquisition cost (CAC) and lifetime value (LTV) if applicable.
- Average revenue per user/unit/contract depending on the business type.
- Working capital dynamics: days sales outstanding, inventory turns, payables.

## 3. Competitive Moat & Barriers to Entry
- Identify and assess the durability of each competitive advantage: network effects, switching costs, cost advantages, intangible assets (brands, patents, licenses), efficient scale.
- Quantify the moat where possible (e.g., patent portfolio size, brand value rankings, customer retention rates).
- Assess how the moat has changed over the past 5 years — strengthening or eroding?
- Compare moat strength to the top 2-3 competitors.

## 4. Supply Chain & Key Dependencies
- Critical suppliers and any single-source dependencies.
- Vertical integration strategy — what is made in-house vs outsourced.
- Geographic concentration of manufacturing or service delivery.
- Raw material or input cost exposure and hedging approach.
- Key technology or platform dependencies (e.g., cloud providers, chip suppliers).

## 5. Growth Drivers & Reinvestment Strategy
- Capital allocation breakdown: R&D, capex, M&A, buybacks, dividends (with dollar amounts).
- R&D spending as percentage of revenue and how it compares to peers.
- Organic growth rate vs acquisition-driven growth.
- Total addressable market (TAM) for each major segment and penetration rate.
- Pipeline of new products, services, or markets with expected timing.

For each section, cite specific data points from recent earnings reports, SEC filings, or reputable financial sources. Include dates for all figures."""

    result = query_perplexity(prompt, max_tokens=PERPLEXITY_MAX_TOKENS['business_model'])

    if result:
        artifacts_dir = ensure_directory(workdir / 'artifacts')
        output_path = artifacts_dir / 'perplexity_analysis_business_model.md'
        with output_path.open('w') as f:
            f.write(f"# Business Model Analysis - {symbol}\n\n")
            f.write(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n")
            f.write(result)
        logger.info("Saved business model analysis to %s", output_path)
        return True
    else:
        logger.error("Failed to get business model analysis from Perplexity")
        return False


# ============================================================================
# Competitive Analysis
# ============================================================================

def save_competitive_analysis(
    symbol: str,
    workdir: Path,
    company_identifier: str,
) -> bool:
    """
    Query Perplexity for competitive landscape analysis and save to markdown.

    Covers market share, direct competitors, advantages/disadvantages,
    consolidation trends, and disruption risks.

    Args:
        symbol: Stock ticker symbol.
        workdir: Work directory path.
        company_identifier: Human-readable company name with symbol.

    Returns:
        True if the artifact was written successfully, False otherwise.
    """
    logger.info("Generating competitive analysis for %s...", company_identifier)

    prompt = f"""Provide a detailed competitive landscape analysis for {company_identifier}.

Structure your analysis with the following sections:

## 1. Market Share & Industry Ranking
- Current market share with specific percentage and dollar figures for each major segment the company operates in.
- How market share has trended over the past 3-5 years (gaining or losing).
- Ranking within the industry by revenue, profitability, and market capitalization.
- Total addressable market size for each segment and growth rate of the overall market.
- Source the market share data from industry reports (IDC, Gartner, Statista, etc.) where possible.

## 2. Direct Competitors & Positioning
For each of the top 5 direct competitors, provide:
- Company name, ticker, and market cap.
- Revenue overlap: which segments compete directly.
- Key differentiator vs {company_identifier}.
- Relative strengths and weaknesses.
- Recent strategic moves that affect competitive dynamics.

Create a positioning matrix showing how the company and its competitors compare on two key dimensions relevant to the industry (e.g., price vs quality, breadth vs depth, enterprise vs consumer).

## 3. Competitive Advantages & Disadvantages
- List 3-5 specific, defensible competitive advantages with evidence for each.
- List 3-5 specific competitive disadvantages or vulnerabilities.
- For each advantage/disadvantage, assess whether it is strengthening or weakening and why.
- Compare pricing power: can the company raise prices without losing customers?

## 4. Industry Consolidation Trends
- Recent M&A activity in the industry (last 2-3 years) with deal values.
- Is the industry consolidating or fragmenting? What is driving the trend?
- Is {company_identifier} likely to be an acquirer or a target? What evidence supports this?
- Regulatory stance on consolidation in this industry.

## 5. Disruption Risks
- Identify 2-3 emerging technologies or business models that could disrupt the current competitive landscape.
- Name specific startups or non-traditional competitors entering the space.
- Assess how well {company_identifier} is positioned to adapt to or lead disruption.
- Timeline: when could disruption materially impact the industry (1-3 years, 3-5 years, 5+ years)?

Cite specific data points, dates, and sources throughout. Use the most recent available data."""

    result = query_perplexity(prompt, max_tokens=PERPLEXITY_MAX_TOKENS['competitive'])

    if result:
        artifacts_dir = ensure_directory(workdir / 'artifacts')
        output_path = artifacts_dir / 'perplexity_analysis_competitive.md'
        with output_path.open('w') as f:
            f.write(f"# Competitive Analysis - {symbol}\n\n")
            f.write(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n")
            f.write(result)
        logger.info("Saved competitive analysis to %s", output_path)
        return True
    else:
        logger.error("Failed to get competitive analysis from Perplexity")
        return False


# ============================================================================
# Risk Analysis
# ============================================================================

def save_risk_analysis(
    symbol: str,
    workdir: Path,
    company_identifier: str,
) -> bool:
    """
    Query Perplexity for categorized risk analysis and save to markdown.

    Risks are organized into four categories: operational, financial,
    regulatory, and market. Each risk includes specific data points.

    Args:
        symbol: Stock ticker symbol.
        workdir: Work directory path.
        company_identifier: Human-readable company name with symbol.

    Returns:
        True if the artifact was written successfully, False otherwise.
    """
    logger.info("Generating risk analysis for %s...", company_identifier)

    prompt = f"""Provide a comprehensive, categorized risk analysis for {company_identifier}.

For each risk, you must include: (a) a clear description, (b) specific supporting data points or evidence, (c) likelihood assessment (high/medium/low), (d) potential financial impact, and (e) any mitigants the company has in place.

## 1. Operational Risks

### Supply Chain Risks
- Identify specific supply chain vulnerabilities: single-source suppliers, geographic concentration, raw material dependencies.
- Cite any recent supply chain disruptions and their financial impact.
- Assess inventory management effectiveness (days of inventory, write-downs).

### Key Person Risk
- Dependence on specific executives (CEO, founder, key technical leaders).
- Succession planning status — is there a disclosed succession plan?
- Historical impact of leadership changes on the company.

### Technology & Execution Risks
- Critical technology infrastructure risks (legacy systems, technical debt, cybersecurity).
- History of execution failures: missed product launches, delayed projects, cost overruns.
- IT spending relative to peers and adequacy of technology investments.

### Operational Concentration
- Facility concentration: single points of failure in manufacturing or data centers.
- Geographic concentration of operations or workforce.
- Dependence on key contracts or customers (percentage of revenue from top customers).

## 2. Financial Risks

### Leverage & Liquidity
- Current debt-to-equity ratio, net debt, and interest coverage ratio with specific figures.
- Debt maturity schedule: when are major maturities coming due?
- Credit rating and recent rating actions or outlook changes.
- Cash and equivalents vs short-term obligations.

### Currency & Interest Rate Exposure
- Percentage of revenue and costs in foreign currencies.
- Hedging strategy and effectiveness.
- Sensitivity to interest rate changes (floating rate debt, pension obligations).

### Capital Allocation Risks
- Track record of M&A: have past acquisitions created or destroyed value? Cite specific examples with returns.
- Share buyback timing: has the company historically bought back shares at high or low valuations?
- Dividend sustainability: payout ratio and free cash flow coverage.

## 3. Regulatory Risks

### Pending Legislation & Regulation
- Identify specific pending bills, regulatory proposals, or rulemaking that could impact the company.
- Quantify potential financial impact where possible.
- Timeline for regulatory decisions.

### Compliance & Legal
- Ongoing litigation with potential material impact (name specific cases, amounts at stake).
- Regulatory investigations or enforcement actions.
- History of compliance failures, fines, or consent decrees.

### Antitrust & Market Power
- Is the company subject to antitrust scrutiny? In which jurisdictions?
- Market concentration issues that could trigger regulatory action.
- Impact of potential forced divestitures or behavioral remedies.

## 4. Market Risks

### Cyclicality & Macro Sensitivity
- How sensitive is the company's revenue to GDP growth, consumer spending, or business investment cycles?
- Performance during the last two economic downturns (specific revenue and earnings impact).
- Current positioning in the business cycle.

### Competitive & Disruption Risks
- Specific competitive threats that could erode market share within 2-3 years.
- Pricing pressure trends and impact on margins.
- Technology disruption threats from startups or adjacent-industry entrants.

### Geopolitical & ESG Risks
- Exposure to geopolitical tensions (specific countries and revenue at risk).
- ESG-related risks: carbon exposure, labor practices, governance concerns.
- Stakeholder activism or shareholder proposal trends.

Cite specific data points, regulatory filings, court documents, and recent news sources. Do not use vague or generic language — every risk should be supported by concrete evidence specific to {company_identifier}."""

    result = query_perplexity(prompt, max_tokens=PERPLEXITY_MAX_TOKENS['risk'])

    if result:
        artifacts_dir = ensure_directory(workdir / 'artifacts')
        output_path = artifacts_dir / 'perplexity_analysis_risk.md'
        with output_path.open('w') as f:
            f.write(f"# Risk Analysis - {symbol}\n\n")
            f.write(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n")
            f.write(result)
        logger.info("Saved risk analysis to %s", output_path)
        return True
    else:
        logger.error("Failed to get risk analysis from Perplexity")
        return False


# ============================================================================
# Investment Thesis
# ============================================================================

def save_investment_thesis(
    symbol: str,
    workdir: Path,
    company_identifier: str,
) -> bool:
    """
    Query Perplexity for investment thesis and save to markdown.

    Includes bull/bear/base cases, SWOT analysis, and key watchpoints.

    Args:
        symbol: Stock ticker symbol.
        workdir: Work directory path.
        company_identifier: Human-readable company name with symbol.

    Returns:
        True if the artifact was written successfully, False otherwise.
    """
    logger.info("Generating investment thesis for %s...", company_identifier)

    prompt = f"""Provide a comprehensive investment thesis for {company_identifier}.

## 1. Bull Case
Present the most compelling optimistic scenario with specific catalysts:
- Identify 5 specific catalysts that could drive the stock significantly higher over the next 12-24 months. For each catalyst:
  - What is the catalyst and when could it materialize?
  - What is the quantitative impact on revenue, earnings, or valuation?
  - What is the probability of this catalyst occurring?
- Provide a bull case revenue and EPS estimate for the next 2 fiscal years with assumptions.
- What valuation multiple would the market assign in the bull case and why?
- Name comparable companies or historical precedents that support the bull case.
- What is the implied upside from the current price?

## 2. Bear Case
Present the most credible pessimistic scenario with specific risks:
- Identify 5 specific risks or negative catalysts that could drive the stock significantly lower. For each:
  - What triggers the downside scenario?
  - What is the quantitative impact on revenue, earnings, or valuation?
  - What early warning signs should investors watch for?
- Provide a bear case revenue and EPS estimate for the next 2 fiscal years with assumptions.
- What valuation multiple would the market assign in the bear case?
- Historical examples of similar companies or situations that resulted in poor outcomes.
- What is the implied downside from the current price?

## 3. Base Case
Present the most likely outcome:
- Expected revenue growth rate and EPS trajectory for the next 2-3 fiscal years.
- Key assumptions underlying the base case (market growth, share gains/losses, margin trajectory).
- What is the consensus view and where does it differ from this base case?
- Fair value estimate based on DCF, peer multiples, and historical valuation range.
- Expected total return (price appreciation + dividends) over 12 months.

## 4. SWOT Analysis
Present as a structured analysis:

| Category | Details |
|----------|---------|
| **Strengths** | List 4-5 specific internal strengths with supporting evidence |
| **Weaknesses** | List 4-5 specific internal weaknesses with supporting evidence |
| **Opportunities** | List 4-5 specific external opportunities with market sizing |
| **Threats** | List 4-5 specific external threats with probability assessment |

For each SWOT item, include a specific data point or citation, not generic statements.

## 5. Key Watchpoints
Identify 5-7 specific, measurable indicators that would cause you to change the investment thesis:
- For each watchpoint:
  - What metric or event to monitor.
  - Current value or status.
  - Threshold that would trigger a thesis change (bullish or bearish).
  - How frequently to check (quarterly earnings, monthly data, real-time events).
  - Which direction the thesis would shift if the threshold is breached.

Examples: customer churn rate exceeding X%, management guidance revision, regulatory decision by date Y, competitor product launch, margin falling below Z%.

Cite recent analyst reports, earnings transcripts, SEC filings, and financial data sources throughout. Use specific numbers, not vague qualitative assessments."""

    result = query_perplexity(prompt, max_tokens=PERPLEXITY_MAX_TOKENS['thesis'])

    if result:
        artifacts_dir = ensure_directory(workdir / 'artifacts')
        output_path = artifacts_dir / 'perplexity_analysis_investment_thesis.md'
        with output_path.open('w') as f:
            f.write(f"# Investment Thesis - {symbol}\n\n")
            f.write(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n")
            f.write(result)
        logger.info("Saved investment thesis to %s", output_path)
        return True
    else:
        logger.error("Failed to get investment thesis from Perplexity")
        return False


# ============================================================================
# Main Entry Point
# ============================================================================

def main() -> int:
    """
    CLI entry point. Runs all four analysis queries sequentially.

    Returns:
        Exit code: 0 (all 4 succeed), 1 (partial), 2 (nothing produced).
    """
    parser = argparse.ArgumentParser(
        description='Perplexity AI analysis research: business model, competitive, risk, investment thesis'
    )
    parser.add_argument(
        'symbol',
        help='Stock ticker symbol (e.g., TSLA, AAPL, MSFT)'
    )
    parser.add_argument(
        '--workdir',
        required=True,
        help='Work directory path'
    )

    args = parser.parse_args()

    # Validate symbol
    try:
        symbol = validate_symbol(args.symbol)
    except ValueError as e:
        manifest = {
            "status": "error",
            "artifacts": [],
            "error": str(e),
        }
        print(json.dumps(manifest, indent=2))
        return 2

    workdir = Path(args.workdir)
    ensure_directory(workdir / 'artifacts')

    # Resolve company name
    company_name = get_company_name(symbol, workdir)
    company_identifier = f"{company_name} ({symbol})" if company_name != symbol else symbol

    logger.info("=" * 60)
    logger.info("Research Analysis: %s", symbol)
    logger.info("Company: %s", company_identifier)
    logger.info("Work directory: %s", workdir)
    logger.info("=" * 60)

    # Track results for each analysis
    results = {}
    artifacts = []
    failed_names = []

    # ---- Task 1: Business Model Analysis ----
    logger.info("\n[1/4] Business Model Analysis")
    results['business_model'] = save_business_model_analysis(symbol, workdir, company_identifier)
    if results['business_model']:
        artifacts.append({
            "name": "business_model",
            "path": "artifacts/perplexity_analysis_business_model.md",
            "format": "md",
            "source": "perplexity",
            "summary": "Revenue segments, unit economics, competitive moat",
        })
    else:
        failed_names.append("business_model")

    # ---- Task 2: Competitive Analysis ----
    logger.info("\n[2/4] Competitive Analysis")
    results['competitive'] = save_competitive_analysis(symbol, workdir, company_identifier)
    if results['competitive']:
        artifacts.append({
            "name": "competitive",
            "path": "artifacts/perplexity_analysis_competitive.md",
            "format": "md",
            "source": "perplexity",
            "summary": "Market share, competitors, positioning",
        })
    else:
        failed_names.append("competitive")

    # ---- Task 3: Risk Analysis ----
    logger.info("\n[3/4] Risk Analysis")
    results['risk'] = save_risk_analysis(symbol, workdir, company_identifier)
    if results['risk']:
        artifacts.append({
            "name": "risk",
            "path": "artifacts/perplexity_analysis_risk.md",
            "format": "md",
            "source": "perplexity",
            "summary": "Risks across 4 categories with specific data",
        })
    else:
        failed_names.append("risk")

    # ---- Task 4: Investment Thesis ----
    logger.info("\n[4/4] Investment Thesis")
    results['investment'] = save_investment_thesis(symbol, workdir, company_identifier)
    if results['investment']:
        artifacts.append({
            "name": "investment",
            "path": "artifacts/perplexity_analysis_investment_thesis.md",
            "format": "md",
            "source": "perplexity",
            "summary": "Bull/bear/base cases, SWOT, catalysts",
        })
    else:
        failed_names.append("investment")

    # ---- Determine exit status ----
    succeeded = sum(1 for v in results.values() if v)
    total = len(results)

    if succeeded == total:
        status = "complete"
        error = None
        exit_code = 0
    elif succeeded > 0:
        status = "partial"
        error = f"Failed: {', '.join(failed_names)}"
        exit_code = 1
    else:
        status = "error"
        error = "All 4 analysis queries failed"
        exit_code = 2

    # ---- Print summary to stderr ----
    logger.info("\n" + "=" * 60)
    logger.info("Analysis Research Complete: %d/%d succeeded", succeeded, total)
    if failed_names:
        logger.info("Failed: %s", ", ".join(failed_names))
    logger.info("=" * 60)

    # ---- Print manifest to stdout ----
    manifest = {
        "status": status,
        "artifacts": artifacts,
        "error": error,
    }
    print(json.dumps(manifest, indent=2))

    return exit_code


if __name__ == '__main__':
    sys.exit(main())
