# MCP Caching Proxy + Tool Profiles — Design

## Overview

Two independent features:
1. **MCP caching proxy** — a generic wrapper script that sits between Claude and any MCP server, caching responses in SQLite so no external API call is duplicated within a pipeline run.
2. **Tool profiles** — separate MCP configs for coding (interactive) vs. research pipeline sessions.

---

## Architecture

```
Coding session (interactive):
  Claude Code → .mcp.json (coding profile) → real MCP servers directly
                                              (context7, playwright, filesystem)

Research pipeline:
  research.py
    ├── sets MCP_CACHE_WORKDIR=work/SYMBOL_DATE in subprocess env
    └── spawns: claude -p --mcp-config mcp-research.json
                  │
                  └── mcp-research.json lists each finance/research server
                      wrapped through mcp_proxy.py:
                        mcp_proxy.py --transport stdio --command ...yfinance...
                        mcp_proxy.py --transport stdio --command ...perplexity...
                        mcp_proxy.py --transport http  --url https://fmp...
                        ...
                        each proxy: check mcp-cache.db → hit? return cached.
                                    miss? call real server, cache result, return.
```

Cache lifetime: per-run. `work/SYMBOL_DATE/` is a fresh directory each run, so `mcp-cache.db` is automatically fresh. No explicit clearing needed.

---

## `mcp_proxy.py` — The Wrapper Script

**Location:** `skills/mcp_proxy/mcp_proxy.py`

**Role:** Runs as a stdio MCP server (Claude talks to it). Internally connects to the real server as an MCP client — via stdio subprocess or HTTP, depending on transport type. On startup, introspects the real server via `tools/list` and re-exposes all discovered tools. On `tools/call`, checks the cache first.

### Invocation (stdio transport)
```json
{
  "command": "uv",
  "args": [
    "run", "python", "skills/mcp_proxy/mcp_proxy.py",
    "--transport", "stdio",
    "--command", "npx",
    "--args", "-y,@modelcontextprotocol/server-fetch"
  ]
}
```

### Invocation (HTTP transport)
```json
{
  "command": "uv",
  "args": [
    "run", "python", "skills/mcp_proxy/mcp_proxy.py",
    "--transport", "http",
    "--url", "https://financialmodelingprep.com/mcp?apikey=KEY"
  ]
}
```

### Environment
- `MCP_CACHE_WORKDIR` — path to `work/SYMBOL_DATE/`, used to locate `mcp-cache.db`
- If unset, caching is disabled (pass-through mode — used in coding profile)

### Tool call flow
1. Compute cache key: `sha256(tool_name + json.dumps(arguments, sort_keys=True))`
2. Query `mcp-cache.db` — if hit, return stored result immediately (no network call)
3. If miss: forward call to real server, store `(key, server, tool, args, result, timestamp)`, return result

---

## Cache Schema

**File:** `work/SYMBOL_DATE/mcp-cache.db`

```sql
CREATE TABLE IF NOT EXISTS mcp_cache (
  cache_key  TEXT PRIMARY KEY,
  server     TEXT NOT NULL,      -- logical server name (e.g. "yfinance")
  tool_name  TEXT NOT NULL,
  arguments  TEXT NOT NULL,      -- JSON, sorted keys
  result     TEXT NOT NULL,      -- JSON
  created_at TEXT NOT NULL       -- ISO8601
);
```

---

## Tool Profiles

### `.mcp.json` (project root — coding profile)

Loaded automatically by Claude Code in interactive sessions. Direct connections, no proxy, no caching.

Servers: `context7`, `playwright`, `filesystem`

### `mcp-research.json` (research pipeline profile)

Used only by `claude -p` calls in `research.py`. All servers routed through `mcp_proxy.py`.

Servers: `alphavantage`, `yfinance`, `fmp`, `openbb-mcp`, `wikipedia`, `perplexity-ask`, `brave-search`

### `scripts/gen_mcp_configs.py`

Helper script that reads `~/Library/Application Support/Claude/claude_desktop_config.json` and generates both config files:
- Applies proxy wrapper to research servers in `mcp-research.json`
- Writes coding servers directly to `.mcp.json`
- Substitutes API keys from environment / `.env`

Run once to bootstrap; re-run if `claude_desktop_config.json` changes.

---

## `research.py` Integration

Minimal changes:
1. When constructing the `claude -p` subprocess, add `MCP_CACHE_WORKDIR=work/SYMBOL_DATE` to the environment
2. Add `--mcp-config mcp-research.json` to the `claude -p` command args
3. No cache clearing logic needed — fresh workdir = fresh cache

---

## Target Servers for Testing

| Server | Transport | Profile |
|---|---|---|
| alphavantage | stdio (python) | research |
| yfinance | stdio (uv) | research |
| fmp | http/url | research |
| openbb-mcp | stdio | research |
| wikipedia | stdio (python) | research |
| perplexity-ask | stdio (npx) | research |
| brave-search | stdio (npx) | research |
| context7 | stdio (npx) | coding |
| playwright | stdio (npx) | coding |
| filesystem | stdio (npx) | coding |

---

## Implementation Stack

- **Language:** Python 3.11+ with `uv`
- **MCP SDK:** `mcp` Python SDK (handles JSON-RPC framing for both server and client roles)
- **Cache store:** SQLite via `sqlite3` stdlib
- **Config generation:** stdlib `json` + `pathlib`
- **Dependencies to add:** `mcp` (already likely present via other skills)
