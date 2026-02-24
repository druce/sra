---
name: fetch_perplexity_analysis
description: Generate business model, competitive, risk, and investment analysis via Perplexity AI
type: python
---

# fetch_perplexity_analysis

Generates four focused analysis documents using Perplexity AI: business model analysis, competitive analysis, risk analysis, and investment thesis with SWOT.

## Usage

```bash
./skills/fetch_perplexity_analysis/fetch_perplexity_analysis.py SYMBOL --workdir DIR
```

## Outputs

- `artifacts/perplexity_analysis_business_model.md` — Revenue mechanics, unit economics, moat analysis
- `artifacts/perplexity_analysis_competitive.md` — Market share, positioning, differentiation
- `artifacts/perplexity_analysis_risk.md` — Operational, financial, regulatory, market risks
- `artifacts/perplexity_analysis_investment_thesis.md` — Bull/bear/base cases, SWOT, catalysts
