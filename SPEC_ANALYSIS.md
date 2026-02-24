# Perplexity Analysis Skill Spec — `fetch_perplexity_analysis.py`

## Overview

Generates four focused analysis documents using Perplexity AI: business model analysis, competitive analysis, risk analysis, and investment thesis. Queries are self-contained (Perplexity searches the web); writer subagents later synthesize these into report sections.

## Goals

1. Generate business model analysis via Perplexity (revenue mechanics, unit economics, moat)
2. Generate competitive analysis via Perplexity (market share, positioning, differentiation)
3. Generate risk analysis via Perplexity (categorized: operational, financial, regulatory, market)
4. Generate investment thesis via Perplexity (bull/bear/base cases, SWOT, catalysts)
5. Output JSON manifest to stdout

## Non-Goals

- Synthesizing across data sources (that's the writer subagents)
- Using Claude for these queries (Perplexity provides sourced, factual research)
- Reading other artifacts (Perplexity queries are self-contained with web search)

## Dependencies

### Python packages
```
openai          # Perplexity uses OpenAI-compatible API
yfinance        # Fallback for company name
python-dotenv
```

### Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `PERPLEXITY_API_KEY` | Yes | Perplexity API access |

> Scripts call `load_environment()` from `utils.py` at startup to load the project root `.env` file. Scripts that need env vars MUST call this before accessing them. The `.env` file is not committed to version control.

## Config Constants

All sourced from `config.py`:

| Constant | Purpose |
|----------|---------|
| `PERPLEXITY_MODEL` | Model name (sonar-pro) |
| `PERPLEXITY_TEMPERATURE` | Sampling temperature (0.2) |
| `PERPLEXITY_MAX_TOKENS` | Dict with per-section limits: `business_model`, `competitive`, `risk`, `thesis` |
| `MAX_RETRIES` | API retry attempts |
| `RETRY_DELAY_SECONDS` | Initial retry delay |
| `RETRY_BACKOFF_MULTIPLIER` | Exponential backoff multiplier |

## Output Structure

```
work/SYMBOL_YYYYMMDD/artifacts/
├── perplexity_analysis_business_model.md
├── perplexity_analysis_competitive.md
├── perplexity_analysis_risk.md
└── perplexity_analysis_investment_thesis.md
```

## Functions

### `get_company_name(symbol, workdir) -> str`
Resolve company name from `profile.json`, yfinance fallback, or symbol fallback.

### `query_perplexity(prompt, max_tokens) -> Optional[str]`
Query Perplexity AI with exponential backoff retry.

### `save_business_model_analysis(symbol, workdir, company_identifier) -> bool`
Revenue streams & mix, unit economics & margins, competitive moat, supply chain dependencies, growth drivers & reinvestment.

### `save_competitive_analysis(symbol, workdir, company_identifier) -> bool`
Market share & ranking, top 5 direct competitors, advantages/disadvantages, consolidation trends, disruption risks.

### `save_risk_analysis(symbol, workdir, company_identifier) -> bool`
Categorized risks: operational (supply chain, key person, technology), financial (leverage, currency, capital allocation), regulatory (legislation, compliance, antitrust), market (cyclicality, competition, geopolitical).

### `save_investment_thesis(symbol, workdir, company_identifier) -> bool`
Bull/bear/base cases with specific catalysts and estimates, SWOT analysis table, key watchpoints with thresholds.

## CLI Interface

```
./skills/fetch_perplexity_analysis/fetch_perplexity_analysis.py SYMBOL --workdir DIR
```

| Argument | Required | Purpose |
|----------|----------|---------|
| `SYMBOL` | Yes | Stock ticker symbol |
| `--workdir` | Yes | Work directory path |

**Exit codes:** 0 (all 4 succeed), 1 (partial), 2 (nothing produced)

## Manifest Output

```json
{
  "status": "complete",
  "artifacts": [
    {"name": "business_model", "path": "artifacts/perplexity_analysis_business_model.md", "format": "md", "source": "perplexity", "summary": "Revenue segments, unit economics, competitive moat"},
    {"name": "competitive", "path": "artifacts/perplexity_analysis_competitive.md", "format": "md", "source": "perplexity", "summary": "Market share, 5 direct competitors, positioning"},
    {"name": "risk", "path": "artifacts/perplexity_analysis_risk.md", "format": "md", "source": "perplexity", "summary": "Risks across 4 categories with specific data"},
    {"name": "investment", "path": "artifacts/perplexity_analysis_investment_thesis.md", "format": "md", "source": "perplexity", "summary": "Bull/bear/base cases, SWOT, catalysts"}
  ],
  "error": null
}
```

## DAG Entry

```yaml
analysis:
  skill: script
  params:
    script: skills/fetch_perplexity_analysis/fetch_perplexity_analysis.py
    args: {ticker: "${ticker}", workdir: "${workdir}"}
  depends_on: [profile]
  outputs:
    business_model: {path: "artifacts/perplexity_analysis_business_model.md", format: md}
    competitive:    {path: "artifacts/perplexity_analysis_competitive.md", format: md}
    risk:           {path: "artifacts/perplexity_analysis_risk.md", format: md}
    investment:     {path: "artifacts/perplexity_analysis_investment_thesis.md", format: md}
```

Depends on `profile` (needs company name for Perplexity queries). Runs in parallel with `fundamental`, `perplexity`, `sec_edgar`, `wikipedia`.

## Design Decisions

- **Perplexity, not Claude:** These queries benefit from Perplexity's web search grounding. Writer subagents (Claude) synthesize into polished report sections.
- **Four separate queries:** Each analysis type has different focus and token budget. Separate queries allow partial success.
- **Sequential execution:** Perplexity rate limits make parallel queries unreliable.
- **No reading of other artifacts:** Perplexity searches the web; writer subagents cross-reference artifacts.
