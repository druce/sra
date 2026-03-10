# Company Profile Skill Spec — `fetch_profile.py`

## Overview

Extracts company profile data and identifies peer companies. Profile data is available as a dependency-free first step in the DAG, and multiple downstream tasks (technical, fundamental, analysis) consume its outputs.

## Goals

1. Fetch company profile metadata from yfinance: name, sector, industry, description, employee count, website, country
2. Fetch key valuation snapshot: market cap, current price, 52-week range, beta
3. Identify peer companies using a provider fallback chain (Finnhub -> OpenBB/FMP)
4. Optionally filter peers to true industry peers using Claude API
5. Output standardized JSON for both profile and peers, consumable by downstream tasks

## Non-Goals

- Detailed financial ratios (that's `fetch_fundamental.py`)
- Technical indicators or charts (that's `fetch_technical.py`)
- Deep business model analysis (that's `fetch_analysis.py`)
- Ticker lookup/validation (that's `lookup_ticker.py`, called before the DAG starts)

## Dependencies

### Python packages (already in requirements.txt)
```
yfinance
finnhub-python
openbb
anthropic
python-dotenv
```

### Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `FINNHUB_API_KEY` | No (fallback chain) | Peer detection via Finnhub (free tier) |
| `OPENBB_PAT` | No (fallback chain) | Peer detection via OpenBB/FMP |

Peer detection degrades gracefully: if all providers fail, an empty peers list is saved and downstream tasks proceed without peer comparison.

> Scripts call `load_environment()` from `utils.py` at startup to load the project root `.env` file. Scripts that need env vars MUST call this before accessing them. The `.env` file is not committed to version control.

## Configuration (`config.py`)

### Constants used
```python
MAX_PEERS_TO_FETCH = 15      # Maximum peers to fetch from API
CLAUDE_MODEL = 'claude-sonnet-4-5-20250929'
```

## CLI

```bash
./skills/fetch_profile/fetch_profile.py SYMBOL --workdir DIR [--peers SYM1,SYM2,...] [--no-filter-peers]
```

**Exit codes:**

- `0` — all steps succeeded
- `1` — partial success (profile or peers missing)
- `2` — total failure (nothing produced)

**Stdout:** JSON manifest of produced artifacts.
**Stderr:** All progress/diagnostic logging.

## Output Structure

```
work/SYMBOL_YYYYMMDD/artifacts/
├── profile.json              # Company profile metadata + valuation snapshot
└── peers_list.json           # Peer companies with names and market data
```

Both files go in the flat `artifacts/` directory. The DAG defines the output paths; the script writes to `{workdir}/artifacts/`.

## Output Schemas

### `profile.json`

```json
{
  "symbol": "TSLA",
  "timestamp": "2026-02-22T10:30:00",
  "company_name": "Tesla, Inc.",
  "sector": "Consumer Cyclical",
  "industry": "Auto Manufacturers",
  "country": "United States",
  "website": "https://www.tesla.com",
  "employees": 140473,
  "business_summary": "Tesla, Inc. designs, develops, manufactures...",
  "market_cap": 892000000000,
  "enterprise_value": 870000000000,
  "current_price": 280.50,
  "52_week_high": 488.54,
  "52_week_low": 138.80,
  "beta": 2.31,
  "shares_outstanding": 3180000000,
  "float_shares": 2860000000
}
```

### `peers_list.json`

```json
{
  "symbol": ["GM", "F", "RIVN", "NIO", "LI"],
  "name": ["General Motors Company", "Ford Motor Company", "Rivian Automotive", "NIO Inc.", "Li Auto Inc."],
  "price": [52.30, 12.80, 14.50, 5.20, 28.90],
  "market_cap": [58000000000, 51000000000, 14000000000, 10000000000, 30000000000],
  "provider": "Finnhub",
  "filtered": true,
  "filter_rationale": "Filtered from 12 to 5 peers using Claude API"
}
```

When all peer providers fail, an empty peers list is saved with the same schema plus `"provider": null, "filtered": false, "filter_rationale": null`.

The list-of-lists format matches the existing `peers_list.json` structure that `fetch_fundamental.py` already knows how to read.

## Functions

### `get_company_profile(symbol) -> Tuple[bool, Optional[Dict], Optional[str]]`

Fetch company profile from yfinance. Extracts identity fields (name, sector, industry, description, employees, website, country) and valuation snapshot fields (market cap, enterprise value, current price, 52-week range, beta, shares outstanding, float).

Returns the profile dict on success. On failure (e.g., invalid ticker, yfinance timeout), returns error string.

### `get_peers(symbol) -> Tuple[bool, Optional[Dict], Optional[str]]`

Identify peer companies using a provider fallback chain:

1. **Finnhub** (`company_peers` endpoint) — uses GICS sub-industry classification
2. **OpenBB/FMP** (`equity.compare.peers`) — alternative peer source

For each peer found, enriches with yfinance data (name, current price, market cap) via `_enrich_peers_with_yfinance()`. Caps at `MAX_PEERS_TO_FETCH`.

Returns the peers dict (list-of-lists format) with `provider` field indicating which source succeeded.

### `get_peers_from_list(peer_symbols) -> Tuple[bool, Optional[Dict], Optional[str]]`

Build peers data from an explicit list of symbols (used when `--peers` flag is provided). Validates symbols via `validate_symbol()`, skips invalid ones, enriches valid ones with yfinance data. Sets `provider` to `"custom"`.

### `filter_peers(symbol, company_name, industry, peers_data) -> Tuple[Optional[Dict], Optional[str]]`

Peer filtering is now handled as a Claude Code workflow task (not a direct API call). Returns filtered peers dict and rationale string. Returns `None, None` if filtering fails or Claude filters out all peers — caller falls back to unfiltered list.

### Internal helpers

- **`_get_peers_finnhub(symbol)`** — Fetch peer symbols from Finnhub. Removes target symbol, caps at `MAX_PEERS_TO_FETCH`.
- **`_get_peers_openbb(symbol)`** — Fetch peer symbols from OpenBB/FMP. Handles multiple response formats.
- **`_enrich_peers_with_yfinance(peer_symbols)`** — Enrich peer symbol list with name, price, and market cap from yfinance. Includes 0.1s pause between calls to avoid rate limiting. Returns dict in list-of-lists format.

### `main() -> int`

Entry point. CLI interface:

```
./skills/fetch_profile/fetch_profile.py SYMBOL --workdir DIR [--peers SYM1,SYM2,...] [--no-filter-peers]
```

**Arguments:**

| Argument | Required | Default | Purpose |
|----------|----------|---------|---------|
| `SYMBOL` | Yes | — | Stock ticker symbol |
| `--workdir` | Yes | — | Work directory path |
| `--peers` | No | auto-detect | Comma-separated custom peer tickers |
| `--no-filter-peers` | No | filter on | Disable Claude-based peer filtering |

**Execution sequence:**

1. `get_company_profile(symbol)` — always runs
2. `get_peers(symbol)` or `get_peers_from_list(symbols)` — auto-detect unless `--peers` provided
3. `filter_peers(...)` — runs if peers found, filtering enabled, and provider is not `"custom"`
4. Save `profile.json` and `peers_list.json` to `{workdir}/artifacts/`
5. Print JSON manifest to stdout

**Exit codes:** 0 (success), 1 (partial — profile or peers missing), 2 (failure — nothing produced)

**Stdout manifest:**

```json
{
  "status": "complete",
  "artifacts": [
    {
      "name": "profile",
      "path": "artifacts/profile.json",
      "format": "json",
      "source": "yfinance",
      "summary": "TSLA Tesla, Inc. | Auto Manufacturers | Market cap $892B"
    },
    {
      "name": "peers_list",
      "path": "artifacts/peers_list.json",
      "format": "json",
      "source": "Finnhub+yfinance",
      "summary": "5 peers: GM, F, RIVN, NIO, LI (filtered from 12)"
    }
  ],
  "error": null
}
```

## Error Handling

- If yfinance can't find the ticker: exit 2 with error in manifest. Profile is required — without it we don't even know the company name.
- If all peer providers fail: save empty peers list `{"symbol":[],"name":[],"price":[],"market_cap":[],"provider":null,"filtered":false,"filter_rationale":null}`, log warning, exit 1 (partial). Downstream tasks (fundamental ratio comparison) will run without peer data.
- If peer filtering fails (no API key, Claude error, all peers excluded): keep unfiltered peer list, log warning, continue. Not a failure — just less precise peers.
- If `--peers` custom list contains invalid tickers: skip invalid ones, continue with valid subset.
- All progress/diagnostic output goes to stderr. Only the JSON manifest goes to stdout.

## Integration with DAG

### DAG entry (`sra.yaml`)

```yaml
profile:
  description: Get company profile data based on symbol
  type: python
  config:
    script: skills/fetch_profile/fetch_profile.py
    args:
      ticker: "${ticker}"
      workdir: "${workdir}"
  outputs:
    profile:    {path: "artifacts/profile.json", format: json}
    peers_list: {path: "artifacts/peers_list.json", format: json}
```

Profile has **no dependencies** — it runs in the first DAG iteration alongside `technical`.

### Downstream consumers

| Task | What it reads | Why |
|------|---------------|-----|
| `fundamental` | `peers_list.json` | Peer ratio comparison |
| `technical` | depends on `profile` | Runs after profile completes |
| `write_company_profile` | `profile.json` (via `reads_from`) | Company name, sector, industry, description |
| `write_competitive_landscape` | `peers_list.json` (via fundamental artifacts) | Peer names for comparison |
