---
name: fetch_profile
description: Fetch company profile and detect peer companies via yfinance and Finnhub/OpenBB
type: python
---

# fetch_profile

Fetches company profile metadata from yfinance and identifies peer companies using a provider fallback chain (Finnhub → OpenBB/FMP). Optionally filters peers to true industry peers using the Claude API.

## Usage

```bash
./skills/fetch_profile/fetch_profile.py SYMBOL --workdir DIR [--peers SYM1,SYM2,...] [--no-filter-peers]
```

## Outputs

- `artifacts/profile.json` — Company identity, sector, industry, valuation snapshot
- `artifacts/peers_list.json` — Peer company list with enrichment data
