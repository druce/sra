# Research Swarm Refactor — Design

**Date:** 2026-03-07
**Status:** Approved

## Overview

Refactor the equity research pipeline to separate research from writing. Research agents populate a shared, cross-tagged findings store; writing agents synthesize from it. This eliminates duplicate tool calls across section writers, improves cross-section consistency, and reduces total runtime ~50%.

---

## Architecture

Seven phases, each independently testable:

```
Phase 1: Chunk + Embed
  chunk_documents (python) → tag_chunks (claude) → build_index (python)

Phase 2: MCP Tool Caching Proxy
  mcp_proxy.py wraps all research servers → mcp-research.json config

Phase 3: Research Store + Research Agents
  7 parallel claude tasks → write findings to research_findings table (tagged)

Phase 4: Writing Agents
  7 parallel claude tasks → read research store by section tag → critic-optimizer loop

Phase 5: Assembly (unchanged)
  assemble_body → write_intro → write_conclusion → assemble_text

Phase 6: Final Rendering (unchanged)
  final_assembly → render_final

Phase 7: End-to-end integration + full pipeline test
```

The existing DAG wave model handles parallelism throughout. Phases 1-4 add new waves to `sra.yaml`. Phases 5-6 are the current pipeline, unchanged except for dependency wiring.

---

## Phase 1: Document Chunking & Indexing

### New DAG tasks (all python, run after data-gathering wave)

**`chunk_documents`** — `skills/chunk_index/chunk_documents.py`

Loads text artifacts (10-K sections, news, wikipedia, perplexity analyses, fundamental summaries). Splits into 400-800 token chunks at semantic boundaries (section headers, paragraph breaks). Embeds all chunks in one batched OpenAI call (`text-embedding-3-small`). Writes `artifacts/chunks.json`:

```json
[{"id": "chunk_001", "text": "...", "source": "sec_10k_item1.md", "doc_type": "10-K", "embedding": [...]}]
```

**`tag_chunks`** — claude task, depends on `chunk_documents`

Reads `chunks.json`. In a single pass, assigns each chunk relevance to one or more of the 7 sections: `profile`, `business_model`, `competitive`, `supply_chain`, `financial`, `valuation`, `risk_news`. Writes `artifacts/chunk_tags.json`:

```json
[{"id": "chunk_001", "tags": ["competitive", "supply_chain"]}]
```

**`build_index`** — `skills/chunk_index/build_index.py`, depends on `tag_chunks`

Merges `chunks.json` + `chunk_tags.json` into a LanceDB table at `artifacts/index/`. Adds a BM25 full-text index alongside vectors. Also produces `skills/search_index/search_index.py` — a CLI that research agents call via Bash:

```bash
./skills/search_index/search_index.py "query" --workdir ... --sections competitive supply_chain --top-k 10
```

### New dependencies

```
uv add lancedb openai rank-bm25
```

### Test

Manually call `search_index.py "who are NVDA's main competitors" --workdir work/NVDA_test` and verify relevant 10-K chunks come back ranked correctly. Verify BM25 and vector results both contribute to ranking.

---

## Phase 2: MCP Tool Caching Proxy

Per `DESIGN_TOOL_CACHING.md`.

### `skills/mcp_proxy/mcp_proxy.py`

Runs as a stdio MCP server. On startup, connects to the real server (stdio subprocess or HTTP), introspects via `tools/list`, re-exposes all tools. On `tools/call`, computes `sha256(tool_name + sorted_args)`, checks `mcp-cache.db`:
- Hit: return cached result immediately
- Miss: call real server, store `(key, server, tool, args, result, timestamp)`, return result

Cache is per-workdir (`MCP_CACHE_WORKDIR` env var) — automatically fresh each run. If unset, passes through without caching (coding profile).

### `scripts/gen_mcp_configs.py`

Reads `~/Library/Application Support/Claude/claude_desktop_config.json`, generates:
- `.mcp.json` — coding profile: context7, playwright, filesystem (direct, no proxy)
- `mcp-research.json` — research profile: FMP, alphavantage, yfinance, perplexity-ask, brave-search, wikipedia, openbb-mcp — all routed through `mcp_proxy.py`

Run once to bootstrap; re-run if desktop config changes.

### `research.py` update

`_invoke_claude` gets two new optional params: `mcp_config: str | None` and `extra_env: dict | None`. When `mcp_config` is set, adds `--mcp-config {mcp_config}` to the claude command and injects `MCP_CACHE_WORKDIR` into subprocess env. Research agent tasks set `mcp_config: mcp-research.json` in their DAG config.

### New dependency

```
uv add mcp
```

### Tests

`tests/test_mcp_proxy.py` — integration tests, marked `@pytest.mark.integration`. One test per service, all follow the same pattern:
1. Create temp workdir, set `MCP_CACHE_WORKDIR`
2. Start proxy, make tool call, check `mcp-cache.db` has 1 row, result is non-empty
3. Make identical call, check `mcp-cache.db` still has 1 row (cache hit), result matches
4. Clean up

Services covered:
- **FMP** (HTTP) — `quote` for AAPL
- **alphavantage** (stdio/python) — `TIME_SERIES_DAILY` for AAPL
- **yfinance** (stdio/uv) — `get_stock_info` for AAPL
- **perplexity-ask** (stdio/npx) — simple factual query
- **brave-search** (stdio/npx) — company news query
- **wikipedia** (stdio/python) — `get_article` for Apple Inc.
- **openbb-mcp** (stdio) — equity quote for AAPL

---

## Phase 3: Research Store + Research Agents

### Research store

New table in `research.db`:

```sql
CREATE TABLE research_findings (
  id          TEXT PRIMARY KEY,  -- uuid
  task_id     TEXT NOT NULL,     -- which research agent wrote this
  content     TEXT NOT NULL,     -- finding text (markdown)
  source      TEXT,              -- artifact path or tool name
  tags        TEXT NOT NULL,     -- JSON array: ["competitive", "supply_chain"]
  created_at  TEXT NOT NULL
);
```

New `db.py` commands:
- `finding-add --workdir ... --task-id ... --content ... --source ... --tags tag1 tag2`
- `finding-list --workdir ... [--tags tag1 tag2]` — returns findings matching any given tag

### 7 new DAG tasks

All `claude` type, parallel wave, depend on `build_index` + all data-gathering tasks, use `mcp_config: mcp-research.json`:

`research_profile`, `research_business`, `research_competitive`, `research_supply_chain`, `research_financial`, `research_valuation`, `research_risk_news`

Each agent prompt instructs it to:
1. Search the index via `search_index.py` for its domain
2. Call external tools (FMP, Perplexity, etc.) for live or missing data
3. Write findings via `./skills/db.py finding-add`, tagging anything cross-relevant to other sections (e.g. supply chain risk findings tagged `["supply_chain", "risk_news"]`)

### Test

After research wave:
- `./skills/db.py finding-list --workdir work/NVDA_test` shows ≥5 findings per section
- Cross-tagging is present (some findings have multiple tags)
- `mcp-cache.db` shows cache hits for any tool called by multiple agents

---

## Phase 4: Writing Agents

### DAG changes

All 7 writing tasks add all 7 `research_*` tasks to `depends_on` (not just their own) so they can access cross-tagged findings.

### Prompt preamble

Added before each section's existing prompt:

```
Before writing, retrieve your section's research findings:
  ./skills/db.py finding-list --workdir {workdir} --tags {section_tag}

These findings were produced by specialist research agents and include
cross-tagged evidence from other domains. Use them as your primary source.
Fall back to artifact files for detail or supporting data.
```

Critic-optimizer loop unchanged.

### Test

Run `write_competitive` in isolation against a populated research store. Verify:
1. Output cites sources appearing in `finding-list --tags competitive`
2. Cross-tagged findings are referenced
3. Critic loop produces measurably improved rewrite (diff v0 vs v1)
4. No new tool calls to FMP/Perplexity during writing (check `tools.log`)

---

## Phase 5 & 6: Assembly & Rendering

No code changes. Only update: dependency wiring in `sra.yaml` so `assemble_body` depends on all 7 writing tasks as before, which now depend on the research wave.

**Test**: Verify assembled report has no contradictions in competitive/supply chain overlap areas.

---

## Phase 7: End-to-End Integration

Run full pipeline for two tickers (one large-cap, one mid-cap).

| Metric | Baseline | Target |
|---|---|---|
| Total runtime | ~25 min | ~12 min |
| Duplicate tool calls | unmeasured | 0 (via `mcp-cache.db` hit rate) |
| Cross-section consistency | manual | findings overlap ≥30% |

**Regression test**: Run new pipeline on NVDA and verify final report structure is identical to a pre-refactor baseline (same sections, same template output).

---

## New Files

| Path | Type | Purpose |
|---|---|---|
| `skills/chunk_index/chunk_documents.py` | python | Load, chunk, embed docs |
| `skills/chunk_index/build_index.py` | python | Build LanceDB from chunks + tags |
| `skills/search_index/search_index.py` | python | CLI hybrid search for agents |
| `skills/mcp_proxy/mcp_proxy.py` | python | MCP caching proxy server |
| `scripts/gen_mcp_configs.py` | python | Generate .mcp.json + mcp-research.json |
| `mcp-research.json` | config | Research profile MCP config |
| `tests/test_mcp_proxy.py` | test | Integration tests for proxy + all services |

## Modified Files

| Path | Change |
|---|---|
| `skills/db.py` | Add `research_findings` table + `finding-add`/`finding-list` commands |
| `research.py` | Add `mcp_config` + `extra_env` params to `_invoke_claude` |
| `dags/sra.yaml` | Add phases 1-3 tasks, update writing task dependencies |
