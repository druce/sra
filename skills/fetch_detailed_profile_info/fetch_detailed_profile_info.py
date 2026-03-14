#!/usr/bin/env python3
"""
Detailed Profile Info — fetch_detailed_profile_info.py

Replaces fetch_perplexity + fetch_perplexity_analysis with 7 parallel
invoke_claude calls that use web search for current data.

Produces 7 artifacts (same paths as the two old skills combined):
  - artifacts/news_stories.md
  - artifacts/business_profile.md
  - artifacts/executive_profiles.md
  - artifacts/business_model_analysis.md
  - artifacts/competitive_analysis.md
  - artifacts/risk_analysis.md
  - artifacts/investment_thesis.md

Usage:
    ./skills/fetch_detailed_profile_info/fetch_detailed_profile_info.py SYMBOL --workdir DIR

Exit codes:
    0  All 7 sections succeeded
    1  Partial success (at least one section succeeded)
    2  Complete failure
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

_SKILLS_DIR = Path(__file__).resolve().parent.parent
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

from utils import (  # noqa: E402
    setup_logging,
    validate_symbol,
    ensure_directory,
    load_environment,
    default_workdir,
    invoke_claude,
    resolve_company_name,
)

load_environment()
logger = setup_logging(__name__)


# ---------------------------------------------------------------------------
# System prompt shared by all 7 tasks
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a financial research analyst producing detailed, factual analysis "
    "of public companies. Use web search to find current data — earnings, filings, "
    "news, analyst reports. Cite sources with specific numbers, dates, and data points. "
    "Write in Markdown. Save your output to the file path specified at the end of the prompt."
)

# ---------------------------------------------------------------------------
# Task definitions: (task_key, output_file, prompt_builder)
# ---------------------------------------------------------------------------


def _prompt_news(company: str, symbol: str) -> str:
    return f"""Research and write a comprehensive summary of major recent developments for {company} (ticker: {symbol}) since 2024.

Use web search to find the latest information. For each item include the date, a concise headline, 2-3 sentences of detail, and market impact if known.

Cover three areas with a ## heading for each:

## Recent Developments
- Quarterly earnings results (beats, misses, guidance changes)
- Revenue trends and segment performance
- Major product launches, platform updates, or service expansions
- M&A activity (acquisitions, divestitures, mergers, joint ventures)
- Restructurings, layoffs, or organizational changes
- Leadership changes (CEO, CFO, board members)
- Capital markets activity (debt issuance, share buybacks, dividends)

## Regulatory & Legal
- SEC investigations or enforcement actions
- Major lawsuits (filed, settled, or dismissed)
- Regulatory approvals or denials
- Short-seller reports or fraud allegations
- Insider trading patterns or notable insider transactions
- Government contracts or policy changes affecting the company
- Compliance failures, fines, or consent decrees

## Strategic & Competitive
- Strategic partnerships and alliances
- Analyst upgrades, downgrades, and price target changes
- Institutional ownership changes (major buyers/sellers)
- Competitive dynamics (market share shifts, new entrants, competitor moves)
- International expansion or market entry/exit
- Technology investments, patents, or R&D breakthroughs

Order chronologically from most recent within each section."""


def _prompt_business_profile(company: str, symbol: str) -> str:
    return f"""Research and write a comprehensive business profile of {company} (ticker: {symbol}).

Use web search for the most current data. Cover these 10 sections with detailed analysis, specific data points, numbers, and dates:

## 1. Company Overview
History, founding, headquarters, mission, and current market position.

## 2. Business Model & Revenue Streams
How the company makes money. Break down revenue by segment, product, and geography. Include recent revenue figures.

## 3. Products & Services
Key products and services, market share, and competitive positioning for each major offering.

## 4. Industry & Market Analysis
Total addressable market (TAM), industry growth rate, market trends, and the company's position within the industry.

## 5. Competitive Landscape
Major competitors, competitive advantages (moats), and areas of competitive vulnerability.

## 6. Financial Performance Summary
Recent revenue, earnings, margins, and growth trends. Key financial ratios and how they compare to peers.

## 7. Growth Strategy
Management's stated growth priorities, recent strategic initiatives, R&D investments, and expansion plans.

## 8. Risk Factors
Key business, regulatory, competitive, and macroeconomic risks.

## 9. ESG & Corporate Governance
Environmental, social, and governance practices. Board structure, shareholder rights, and any ESG controversies.

## 10. Recent Developments & Outlook
Latest quarterly results, guidance, analyst consensus, and forward-looking catalysts or headwinds."""


def _prompt_executive_profiles(company: str, symbol: str) -> str:
    return f"""Research and write detailed profiles of the key C-suite executives at {company} (ticker: {symbol}).

Use web search for current information. For each executive, include:

1. **Name and Title** — full name and current title
2. **Background** — education, career history, and how they arrived at their current role
3. **Tenure** — when they joined the company and assumed their current role
4. **Compensation** — most recent total compensation package (base salary, bonus, stock awards, total) from proxy filings
5. **Key Accomplishments** — notable achievements in their role
6. **Leadership Style & Reputation** — market perception and management approach

Cover at minimum:
- Chief Executive Officer (CEO)
- Chief Financial Officer (CFO)
- Chief Operating Officer (COO)
- Chief Technology Officer (CTO)
- Other notable C-suite or key executives

Include any recent leadership changes or succession planning."""


def _prompt_business_model(company: str, symbol: str) -> str:
    return f"""Research and write a detailed business model analysis for {company} (ticker: {symbol}).

Use web search for current financial data. Structure your analysis:

## 1. Revenue Streams & Mix
- Break down revenue by business segment with exact dollar amounts and percentages from the most recent fiscal year and TTM.
- Break down revenue by geography (domestic vs international, by major region).
- Identify fastest-growing and declining segments.
- Note revenue concentration risks (single customer, single product, etc.).

## 2. Unit Economics & Margins by Segment
- Gross margin by segment and 3-year trend.
- Operating margin by segment with specific figures.
- Customer acquisition cost (CAC) and lifetime value (LTV) if applicable.
- Average revenue per user/unit/contract.
- Working capital dynamics: DSO, inventory turns, payables.

## 3. Competitive Moat & Barriers to Entry
- Identify and assess durability of each competitive advantage: network effects, switching costs, cost advantages, intangible assets, efficient scale.
- Quantify the moat where possible (patent portfolio, brand value, retention rates).
- Assess how the moat has changed over 5 years — strengthening or eroding?
- Compare moat strength to top 2-3 competitors.

## 4. Supply Chain & Key Dependencies
- Critical suppliers and single-source dependencies.
- Vertical integration strategy — in-house vs outsourced.
- Geographic concentration of manufacturing or service delivery.
- Raw material or input cost exposure and hedging.
- Key technology or platform dependencies.

## 5. Growth Drivers & Reinvestment Strategy
- Capital allocation breakdown: R&D, capex, M&A, buybacks, dividends (with dollar amounts).
- R&D spending as % of revenue vs peers.
- Organic growth rate vs acquisition-driven growth.
- TAM for each major segment and penetration rate.
- Pipeline of new products, services, or markets with expected timing.

Cite specific data points from recent earnings reports, SEC filings, or reputable financial sources."""


def _prompt_competitive(company: str, symbol: str) -> str:
    return f"""Research and write a detailed competitive landscape analysis for {company} (ticker: {symbol}).

Use web search for current data. Structure your analysis:

## 1. Market Share & Industry Ranking
- Current market share with specific percentage and dollar figures for each major segment.
- Market share trend over 3-5 years (gaining or losing).
- Ranking by revenue, profitability, and market cap.
- TAM size for each segment and market growth rate.
- Source market share data from industry reports (IDC, Gartner, Statista) where possible.

## 2. Direct Competitors & Positioning
For each of the top 5 direct competitors:
- Company name, ticker, and market cap.
- Revenue overlap: which segments compete directly.
- Key differentiator vs {company}.
- Relative strengths and weaknesses.
- Recent strategic moves affecting competitive dynamics.

## 3. Competitive Advantages & Disadvantages
- 3-5 specific, defensible competitive advantages with evidence.
- 3-5 specific competitive disadvantages or vulnerabilities.
- For each, assess whether strengthening or weakening and why.
- Pricing power assessment.

## 4. Industry Consolidation Trends
- Recent M&A activity (last 2-3 years) with deal values.
- Consolidating or fragmenting? What drives the trend?
- Is {company} likely acquirer or target?
- Regulatory stance on consolidation.

## 5. Disruption Risks
- 2-3 emerging technologies or business models that could disrupt the landscape.
- Specific startups or non-traditional competitors entering the space.
- How well {company} is positioned to adapt or lead disruption.
- Timeline: 1-3 years, 3-5 years, 5+ years.

Cite specific data points, dates, and sources throughout."""


def _prompt_risk(company: str, symbol: str) -> str:
    return f"""Research and write a comprehensive, categorized risk analysis for {company} (ticker: {symbol}).

Use web search for current data. For each risk include: (a) clear description, (b) specific supporting evidence, (c) likelihood (high/medium/low), (d) potential financial impact, (e) mitigants.

## 1. Operational Risks

### Supply Chain Risks
- Specific supply chain vulnerabilities: single-source suppliers, geographic concentration, raw material dependencies.
- Recent supply chain disruptions and financial impact.
- Inventory management effectiveness.

### Key Person Risk
- Dependence on specific executives.
- Succession planning status.
- Historical impact of leadership changes.

### Technology & Execution Risks
- Critical technology infrastructure risks (legacy systems, cybersecurity).
- History of execution failures: missed launches, delayed projects, cost overruns.
- IT spending relative to peers.

### Operational Concentration
- Facility concentration: single points of failure.
- Geographic concentration of operations or workforce.
- Dependence on key contracts or customers.

## 2. Financial Risks

### Leverage & Liquidity
- Current debt-to-equity, net debt, interest coverage with specific figures.
- Debt maturity schedule.
- Credit rating and recent actions.
- Cash vs short-term obligations.

### Currency & Interest Rate Exposure
- % of revenue and costs in foreign currencies.
- Hedging strategy and effectiveness.
- Interest rate sensitivity.

### Capital Allocation Risks
- M&A track record with specific examples.
- Share buyback timing vs valuation.
- Dividend sustainability: payout ratio and FCF coverage.

## 3. Regulatory Risks

### Pending Legislation & Regulation
- Specific pending bills or regulatory proposals.
- Quantified potential financial impact.
- Timeline for decisions.

### Compliance & Legal
- Ongoing litigation with material impact (specific cases, amounts).
- Regulatory investigations or enforcement.
- History of compliance failures.

### Antitrust & Market Power
- Antitrust scrutiny and jurisdictions.
- Market concentration issues.
- Impact of potential remedies.

## 4. Market Risks

### Cyclicality & Macro Sensitivity
- Revenue sensitivity to GDP, consumer spending, business investment.
- Performance during last two downturns.
- Current business cycle positioning.

### Competitive & Disruption Risks
- Specific threats that could erode share within 2-3 years.
- Pricing pressure and margin impact.
- Technology disruption from startups or adjacent entrants.

### Geopolitical & ESG Risks
- Geopolitical exposure (specific countries, revenue at risk).
- ESG risks: carbon, labor, governance.
- Stakeholder activism trends.

Every risk must be supported by concrete evidence specific to {company}."""


def _prompt_investment_thesis(company: str, symbol: str) -> str:
    return f"""Research and write a comprehensive investment thesis for {company} (ticker: {symbol}).

Use web search for current analyst estimates and financial data.

## 1. Bull Case
- 5 specific catalysts that could drive the stock significantly higher over 12-24 months.
- For each: what, when, quantitative impact on revenue/earnings/valuation, probability.
- Bull case revenue and EPS estimates for next 2 fiscal years with assumptions.
- Valuation multiple in bull case and why.
- Comparable companies or historical precedents.
- Implied upside from current price.

## 2. Bear Case
- 5 specific risks or negative catalysts.
- For each: trigger, quantitative impact, early warning signs.
- Bear case revenue and EPS estimates for next 2 fiscal years.
- Valuation multiple in bear case.
- Historical examples of similar poor outcomes.
- Implied downside from current price.

## 3. Base Case
- Expected revenue growth and EPS trajectory for next 2-3 fiscal years.
- Key assumptions (market growth, share gains/losses, margin trajectory).
- Consensus view and where this differs.
- Fair value via DCF, peer multiples, and historical range.
- Expected total return (appreciation + dividends) over 12 months.

## 4. SWOT Analysis

| Category | Details |
|----------|---------|
| **Strengths** | 4-5 specific internal strengths with evidence |
| **Weaknesses** | 4-5 specific internal weaknesses with evidence |
| **Opportunities** | 4-5 specific external opportunities with market sizing |
| **Threats** | 4-5 specific external threats with probability |

Each item must include a specific data point or citation.

## 5. Key Watchpoints
5-7 specific, measurable indicators that would change the thesis:
- Metric or event to monitor
- Current value or status
- Threshold triggering a thesis change
- Check frequency
- Direction of thesis shift if breached

Cite recent analyst reports, earnings transcripts, SEC filings, and financial data."""


# Map of task_key -> (output_path_relative, prompt_builder)
TASKS = {
    "news": ("knowledge/news_stories.md", _prompt_news),
    "profile": ("knowledge/business_profile.md", _prompt_business_profile),
    "executives": ("knowledge/executive_profiles.md", _prompt_executive_profiles),
    "biz_model": ("knowledge/business_model_analysis.md", _prompt_business_model),
    "competitive": ("knowledge/competitive_analysis.md", _prompt_competitive),
    "risk": ("knowledge/risk_analysis.md", _prompt_risk),
    "thesis": ("knowledge/investment_thesis.md", _prompt_investment_thesis),
}

# Artifact metadata for the manifest
ARTIFACT_META = {
    "news": {"name": "news_stories", "format": "md", "description": "Recent news stories and developments", "summary": "Major news stories and developments since 2024"},
    "profile": {"name": "business_profile", "format": "md", "description": "Narrative business overview: history, products, markets, strategy, and competitive positioning", "summary": "10-section business profile"},
    "executives": {"name": "executive_profiles", "format": "md", "description": "Key executive bios: name, title, background, tenure, and notable achievements", "summary": "C-suite executive profiles with compensation"},
    "biz_model": {"name": "business_model", "format": "md", "description": "Business model analysis: revenue streams, cost structure, unit economics, scalability, profitability, and moat / defensibility", "summary": "Revenue segments, unit economics, competitive moat"},
    "competitive": {"name": "competitive", "format": "md", "description": "Competitive landscape: positioning, market share, key competitors, and differentiation", "summary": "Market share, competitors, positioning"},
    "risk": {"name": "risk", "format": "md", "description": "Risk analysis: regulatory, operational, market, financial, and strategic risks", "summary": "Risks across 4 categories with specific data"},
    "thesis": {"name": "investment", "format": "md", "description": "Investment thesis: investment rationale, catalysts, bull/bear cases, and valuation", "summary": "Bull/bear/base cases, SWOT, catalysts"},
}


# ---------------------------------------------------------------------------
# Run one task
# ---------------------------------------------------------------------------

async def run_task(
    task_key: str,
    symbol: str,
    company: str,
    workdir: Path,
    debug: bool = False,
) -> tuple[str, bool]:
    """Run a single invoke_claude task. Returns (task_key, success)."""
    output_path, prompt_builder = TASKS[task_key]
    prompt = prompt_builder(company, symbol)

    logger.info("[%s] Starting...", task_key)

    result = await invoke_claude(
        prompt=prompt,
        workdir=workdir,
        step_label=task_key,
        task_id=f"detailed_profile_{task_key}",
        output_file=output_path,
        system=SYSTEM_PROMPT,
        debug=debug,
        stream_to_stdout=sys.stderr.isatty(),
        stream_prefix=f"[{task_key}]",
    )

    if result["status"] == "complete":
        logger.info("[%s] Complete", task_key)
        return (task_key, True)
    else:
        logger.error("[%s] Failed: %s", task_key, result.get("error"))
        return (task_key, False)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(symbol: str, workdir: Path, debug: bool = False) -> int:
    """Run all 7 research tasks in parallel."""
    ensure_directory(workdir / "artifacts")
    ensure_directory(workdir / "knowledge")

    company_name = resolve_company_name(symbol, workdir, yfinance_fallback=True)
    company = f"{company_name} ({symbol})" if company_name != symbol else symbol
    logger.info("Company: %s", company)

    # Launch all 7 in parallel
    results = await asyncio.gather(
        *[run_task(key, symbol, company, workdir, debug) for key in TASKS],
    )

    # Build manifest
    result_map = dict(results)
    artifacts = []
    failed = []

    for key in TASKS:
        if result_map.get(key):
            output_path = TASKS[key][0]
            meta = ARTIFACT_META[key]
            artifacts.append({
                "name": meta["name"],
                "path": output_path,
                "format": meta["format"],
                "description": meta.get("description", meta["summary"]),
                "summary": meta["summary"],
            })
        else:
            failed.append(key)

    succeeded = len(artifacts)
    total = len(TASKS)

    if succeeded == total:
        status, error, exit_code = "complete", None, 0
    elif succeeded > 0:
        status = "partial"
        error = f"Failed: {', '.join(failed)}"
        exit_code = 1
    else:
        status, error, exit_code = "error", "All 7 research tasks failed", 2

    logger.info("Results: %d/%d succeeded", succeeded, total)
    if failed:
        logger.info("Failed: %s", ", ".join(failed))

    manifest = {"status": status, "artifacts": artifacts, "error": error}
    print(json.dumps(manifest, indent=2))
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detailed company profile research via 7 parallel Claude calls with web search"
    )
    parser.add_argument("symbol", help="Stock ticker symbol (e.g. AAPL)")
    parser.add_argument("--workdir", default=None, help="Work directory (default: work/SYMBOL_YYYYMMDD)")
    parser.add_argument("--debug", action="store_true", help="Enable Claude debug logging")
    args = parser.parse_args()

    try:
        symbol = validate_symbol(args.symbol)
    except ValueError as e:
        print(json.dumps({"status": "failed", "artifacts": [], "error": str(e)}))
        return 2
    workdir = Path(args.workdir) if args.workdir else Path(default_workdir(symbol))
    workdir.mkdir(parents=True, exist_ok=True)

    logger.info("Fetching detailed profile info for %s in %s", symbol, workdir)
    return asyncio.run(run(symbol, workdir, debug=args.debug))


if __name__ == "__main__":
    sys.exit(main())
