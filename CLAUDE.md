# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Stock Research Agent — an async Python-orchestrated equity research pipeline. `research.py` reads a DAG defined in YAML, initializes a SQLite database, and runs waves of tasks as async subprocesses. Python data-gathering scripts run via `uv run python`, Claude writing tasks run via `claude --dangerously-skip-permissions -p`.

## Architecture

**Single orchestrator:** `research.py` (asyncio) handles the full lifecycle:
1. Validates DAG YAML and initializes SQLite via `db.py`
2. Loops: query `db.py task-ready` → dispatch all ready tasks in parallel → collect results → update DB → repeat
3. Python tasks: spawns `uv run python {script}`, parses JSON manifest from stdout
4. Claude tasks: spawns `claude -p` with prompt (system + artifact context + task prompt), checks output files
5. All DB writes centralized in orchestrator (tasks never touch the database)

**Artifact context:** `manifest.json` is written before each wave, listing all artifacts produced so far. Claude tasks read this file to discover available research data.

**Data layer:** SQLite + files hybrid. One database per run at `work/{SYMBOL}_{DATE}/research.db`. All components access shared state through `db.py` CLI only — no direct SQLite access elsewhere.

**DAG execution order** (driven by dependencies, not hardcoded stages):
1. `profile` (no deps)
2. `technical`, `fundamental`, `fetch_edgar`, `wikipedia`, `custom_research` (depend on profile/peers)
3. `chunk_documents` → `tag_chunks` → `build_index` (chunk, tag, and index text artifacts into LanceDB)
4. 7 `research_*` tasks in parallel (depend on `build_index` + relevant data tasks; use MCP tools via proxy, record findings)
5. `index_research` (appends MCP cache responses + research findings to LanceDB index)
6. 7 `write_*` tasks in parallel (depend on `index_research` + data tasks; query unified LanceDB index)
7. `assemble_body` (concatenates 7 sections into assembled_body.md)
8. `write_conclusion` (depends on assemble_body), then `write_intro` (depends on both)
9. `assemble_text` (depends on all writers)
10. `critique_body_final` → `polish_body_final` → `final_assembly`

## Key Files

| File | Purpose |
|------|---------|
| `research.py` | Async DAG orchestrator — entry point for full pipeline |
| `skills/db.py` | Core SQLite CLI — init, validate, task-ready, task-get, task-update, artifact-add, artifact-list, finding-add, finding-list, status, research-update |
| `skills/schema.py` | Pydantic models for DAG YAML v2 schema validation |
| `skills/config.py` | Centralized constants (timeouts, API keys, indicator params, model settings) |
| `skills/utils.py` | Logging, formatting, directory helpers |
| `skills/fetch_profile/` | Company profile + peer identification |
| `skills/fetch_technical/` | Stock chart + technical indicators |
| `skills/fetch_fundamental/` | Financial statements, ratios, analyst data |
| `skills/fetch_edgar/` | SEC filings (10-K, 10-Q, 8-K) |
| `skills/fetch_wikipedia/` | Wikipedia company summary |
| `skills/custom_research/` | Run user-provided investigation prompts via parallel Claude subprocesses |
| `skills/chunk_index/chunk_documents.py` | Split text artifacts into chunks, embed via OpenAI |
| `skills/chunk_index/build_index.py` | Build LanceDB hybrid index from chunks + tags |
| `skills/chunk_index/index_research.py` | Append MCP cache responses + research findings to LanceDB index |
| `skills/search_index/search_index.py` | Hybrid vector + BM25 search over LanceDB index |
| `skills/mcp_proxy/mcp_proxy.py` | MCP caching proxy with requestor tracking |
| `dags/sra.yaml` | Default DAG (v2 schema) defining all tasks with typed configs and dependencies |
| `templates/*.md.j2` | Jinja2 report assembly templates |
| `docs/plans/` | Design docs and implementation plans |

## Commands

### Install dependencies
```bash
uv sync
# Also needs system deps:
# brew install pandoc ta-lib
# export TA_INCLUDE_PATH="$(brew --prefix ta-lib)/include"
# export TA_LIBRARY_PATH="$(brew --prefix ta-lib)/lib"
```

### Add a dependency
```bash
uv add <package>
```

### Run individual Python skills
```bash
# All scripts are executable and follow the same pattern:
./skills/fetch_profile/fetch_profile.py SYMBOL --workdir work/SYMBOL_DATE
./skills/fetch_technical/fetch_technical.py SYMBOL --workdir work/SYMBOL_DATE
./skills/fetch_fundamental/fetch_fundamental.py SYMBOL --workdir work/SYMBOL_DATE
./skills/fetch_edgar/fetch_edgar.py SYMBOL --workdir work/SYMBOL_DATE
./skills/fetch_wikipedia/fetch_wikipedia.py SYMBOL --workdir work/SYMBOL_DATE
./skills/fetch_analysis/fetch_analysis.py SYMBOL --workdir work/SYMBOL_DATE
./skills/custom_research/custom_research.py SYMBOL --workdir work/SYMBOL_DATE
```

### Database CLI

All flags are named (not positional). `--path` for `artifact-add` is relative to workdir.

```bash
./skills/db.py init --workdir work/SYMBOL_DATE --dag dags/sra.yaml --ticker SYMBOL
./skills/db.py validate --dag dags/sra.yaml [--ticker SYMBOL]
./skills/db.py task-ready --workdir work/SYMBOL_DATE
./skills/db.py task-get --workdir work/SYMBOL_DATE --task-id TASK_ID
./skills/db.py task-update --workdir work/SYMBOL_DATE --task-id TASK_ID --status STATUS [--summary TEXT] [--error TEXT]
./skills/db.py task-context --workdir work/SYMBOL_DATE --task-id TASK_ID
./skills/db.py artifact-add --workdir work/SYMBOL_DATE --task-id TASK_ID --name NAME --path PATH --format FORMAT [--description TEXT] [--source TEXT] [--summary TEXT]
./skills/db.py artifact-list --workdir work/SYMBOL_DATE [--task TASK_ID]
./skills/db.py finding-add --workdir work/SYMBOL_DATE --task-id TASK_ID --content TEXT --source TEXT [--tags TAG1 TAG2 ...]
./skills/db.py finding-list --workdir work/SYMBOL_DATE [--tags TAG1 TAG2 ...]
./skills/db.py status --workdir work/SYMBOL_DATE
./skills/db.py research-update --workdir work/SYMBOL_DATE --status STATUS
./skills/db.py var-set --workdir work/SYMBOL_DATE --name NAME --value VALUE [--source-task TASK_ID]
./skills/db.py var-get --workdir work/SYMBOL_DATE [--name NAME]
```

### Full pipeline
```bash
./research.py SYMBOL [--dag dags/sra.yaml] [--date YYYYMMDD]
```

## Critic-Optimizer Loop & Drafts

Writing tasks with `n_iterations > 0` run a critic-optimizer loop after the initial write:

1. **Initial write** → `drafts/{stem}.md` (prompt tells Claude to write here)
2. **Publish** → copy to `artifacts/{stem}.md` (clean artifact)
3. **Copy** → `drafts/{stem}_v0.md` (preserve original)
4. **Critic** → `drafts/{stem}_critic_1.md`
5. **Rewrite** → `drafts/{stem}_v1.md`
6. **Publish** → copy `drafts/..._v1.md` to `artifacts/{stem}.md`

With `n_iterations: 2`, repeat: `_critic_2.md`, `_v2.md`, then publish `_v2`.

**Key rules:**
- `artifacts/` only contains clean, published files (no `draft_*` or `_vN` files)
- `drafts/` has full iteration history (initial write, v0, critic_N, vN)
- Only the artifact in `artifacts/` is registered in the DB
- Draft files live on disk only (not in DB)
- Downstream tasks always read from `artifacts/`

## Chunk → Index → Search Pipeline

Text artifacts from data-gathering tasks are processed into a searchable LanceDB index:

1. **chunk_documents.py** — splits `.md`/`.txt` artifacts into paragraph-boundary chunks (~600–800 tokens), embeds via OpenAI `text-embedding-3-small`
2. **tag_chunks** (Claude task) — assigns section tags (`profile`, `financial`, `competitive`, etc.) to each chunk
3. **build_index.py** — merges chunks + tags into LanceDB table at `artifacts/index/` with vector + FTS indexes
4. **7 research agents** — query the index via `search_index.py`, use MCP tools (cached by proxy), record findings via `finding-add`
5. **index_research.py** — reads MCP cache (`mcp-cache.db`) + research findings (`research.db`), chunks/embeds/tags them, appends to existing LanceDB index
6. **7 writers** — query the unified index via `search_index.py` (one source for all data + research)

**search_index.py** performs hybrid search: vector similarity + BM25 full-text, merged via reciprocal rank fusion. Supports `--sections` filtering and `--top-k` control.

## MCP Proxy & Caching

`skills/mcp_proxy/mcp_proxy.py` wraps MCP servers with a SQLite cache at `{workdir}/mcp-cache.db`. Features:
- **Cache key**: SHA256 of `tool_name|arguments_json`
- **Requestor tracking**: `requestors` column tracks which research tasks requested each result (via `MCP_TASK_ID` env var)
- **Schema migration**: automatically adds `requestors` column to existing databases
- Cache is read by `index_research.py` to index MCP responses into LanceDB

## Python Coding Conventions

- `#!/usr/bin/env python3` shebang (never hardcoded paths)
- Import constants from `config.py`, utilities from `utils.py`
- `pathlib.Path` for all path operations (not `os.path`)
- `logger = setup_logging(__name__)` for output (not `print()`)
- Type hints on all functions
- Specific exception handling (no bare `except:`)
- Return `(success: bool, data, error_msg)` tuples from data functions
- Return exit codes from `main()` (0 = success, nonzero = error)
- JSON manifest to stdout: `{"status": "complete", "artifacts": [...], "error": null}`

## Environment

Requires a `.env` file with API keys: `SEC_FIRM`, `SEC_USER`, `OPENAI_API_KEY`, `OPENBB_PAT`, `FMP_API_KEY`, `FINNHUB_API_KEY`, and others. No `ANTHROPIC_API_KEY` needed — all Claude tasks run via Claude Code subprocess.

## Task Endings - "What Else Can I Handle?"

After completing any big task, end with a "Let me take more off your plate" section with three categories:

1. Next actions I can do right now — specific follow-ups I can knock out immediately
2. Automations or systems I can set up — so you never have to do it manually again

