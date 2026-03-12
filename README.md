# Stock Research Agent

An async Python-orchestrated equity research pipeline that generates comprehensive analyst-style reports. A single script drives a DAG of data-gathering tasks and Claude writing agents, producing a polished report from one command.

## How It Works

Write an equity research report in a structured format with 7 main sections (profile, business model, competitive landscape, supply chain, financials, valuation, risk/news). The pipeline:

- **Gather 'foundational' data** — fetch company profile, peers, technical indicators, fundamentals, SEC filings (10-K/10-Q/8-K), Wikipedia summary, charts, news. Each task outputs JSON/text artifacts to `work/{SYMBOL}_{DATE}/artifacts/`
- **Chunk & index** — split the longer text artifacts into chunks, embed them, and store in LanceDB with vector + BM25 indexes. Tag each chunk by which report section(s) it's relevant to (profile, competitive, financial, etc.)
- **Research** — 7 agents run in parallel, one per section. Each queries LanceDB for relevant chunks and uses MCP market data tools to dig deeper. Findings go back into LanceDB, tagged by section — one researcher can surface material useful to other sections
- **Write** — 7 writers run in parallel, each querying the unified LanceDB index (original data + all research findings) with section-specific filters. Each goes through a critic-rewrite loop
- **Assemble** — concatenate 7 sections, write conclusion, write intro, assemble full text, run a final critique-polish pass, render to markdown/HTML/PDF

```bash
./research.py AMD --date 20260225
```

This triggers a 33-task pipeline:

```mermaid
flowchart TD
    profile["profile + peers"]
    fetch["data gathering\n(technical, fundamental,\nedgar, wikipedia, custom)"]
    chunk["chunk → tag → index\n(LanceDB hybrid search)"]
    research["7 research agents\n(parallel, MCP-enabled)"]
    index_research["index_research\n(append MCP + findings)"]
    writers["7 section writers\n(parallel, query index)"]
    assemble_body["assemble_body"]
    write_conclusion["write_conclusion"]
    write_intro["write_intro"]
    assemble["assemble_text"]
    critique["critique_body_final"]
    polish["polish_body_final"]
    final["final_assembly\n(Jinja2 + pandoc)"]

    profile --> fetch
    fetch --> chunk
    chunk --> research
    research --> index_research
    index_research --> writers
    writers --> assemble_body
    assemble_body --> write_conclusion
    write_conclusion --> write_intro
    write_intro --> assemble
    assemble --> critique
    critique --> polish
    polish --> final

    style profile fill:#e1f5fe
    style fetch fill:#e1f5fe
    style chunk fill:#e1f5fe
    style research fill:#f3e5f5
    style index_research fill:#f3e5f5
    style writers fill:#fff3e0
    style assemble_body fill:#fff3e0
    style write_conclusion fill:#fff3e0
    style write_intro fill:#fff3e0
    style critique fill:#fff3e0
    style polish fill:#fff3e0
    style assemble fill:#e8f5e9
    style final fill:#e8f5e9
```

Colors: blue = data gathering & indexing, purple = research, orange = writing & editing, green = final assembly.

## Architecture

**Orchestrator:** `research.py` — a single async Python script that reads the DAG, initializes the database, and runs waves of tasks as parallel subprocesses. Python data-gathering tasks run via `uv run python`, Claude writing tasks run via `claude --dangerously-skip-permissions -p`. All database writes are centralized in the orchestrator.

**State management**: One SQLite database per run (`work/{SYMBOL}_{DATE}/research.db`) tracks task status, dependencies, artifacts, and runtime variables. All components access state through `skills/db.py` — no direct SQL elsewhere.

**Artifact context**: A `manifest.json` file is maintained before each wave, listing all produced artifacts. Claude tasks read this file to discover available research data.

**DAG definition**: `dags/sra.yaml` declares tasks, types, dependencies, configs, and expected outputs in a version-2 schema validated by Pydantic.

## Data Sources

| Source | What it provides |
|--------|-----------------|
| **yfinance** | Price history, fundamentals, analyst recommendations |
| **TA-Lib** | Technical indicators (SMA, RSI, MACD, ATR, Bollinger Bands) |
| **OpenBB / FMP** | Financial statements, key ratios, peer comparisons |
| **Finnhub** | Peer company detection |
| **SEC EDGAR** | 10-K, 10-Q, 8-K filings via edgartools |
| **Wikipedia** | Company history and background |
| **Claude subagents** | Report writing, critique, and revision |

## Output

Each run produces `work/{SYMBOL}_{DATE}/artifacts/` containing 40+ files:

- `final_report.md` — the complete formatted report
- `chart.png` — stock price chart with technical overlays
- `profile.json`, `technical_analysis.json` — structured data
- `income_statement.csv`, `balance_sheet.csv`, `cash_flow.csv`, `key_ratios.csv` — financials
- `draft_report_body.md`, `draft_report_conclusion.md`, `draft_intro.md` — draft sections
- `report_body.md`, `report_critique.md`, `report_body_final.md` — critique/revise cycle
- SEC filing extracts, Wikipedia summaries

## Setup

### Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) package manager
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI
- System libraries: `pandoc`, `ta-lib`

### Install

```bash
# Install system dependencies (macOS)
brew install pandoc ta-lib
export TA_INCLUDE_PATH="$(brew --prefix ta-lib)/include"
export TA_LIBRARY_PATH="$(brew --prefix ta-lib)/lib"

# Install Python dependencies
uv sync
```

### Environment

Create a `.env` file in the project root:

```
SEC_FIRM=...              # SEC EDGAR identity (firm name)
SEC_USER=...              # SEC EDGAR identity (email)
OPENAI_API_KEY=...        # for chunk embeddings (text-embedding-3-small)
OPENBB_PAT=...            # OpenBB Platform access token
FMP_API_KEY=...           # Financial Modeling Prep API key
FINNHUB_API_KEY=...       # Finnhub API key (peer detection)
BRAVE_API_KEY=...         # Brave Search (MCP research agents)
ALPHAVANTAGE_API_KEY=...  # Alpha Vantage (MCP research agents)
PERPLEXITY_API_KEY=...    # Perplexity AI (optional, MCP research)
```

No `ANTHROPIC_API_KEY` needed — all Claude tasks run via the Claude Code CLI subprocess.

## Usage

### Full pipeline

```bash
./research.py SYMBOL [--dag dags/sra.yaml] [--date YYYYMMDD]
```

The orchestrator validates the DAG, initializes the database, then executes waves of tasks in dependency order with parallel dispatch. Auto-skips failures and continues.

### Individual data scripts

Each data-gathering script runs standalone:

```bash
uv run ./skills/fetch_profile/fetch_profile.py AMD --workdir work/AMD_20260225
uv run ./skills/fetch_technical/fetch_technical.py AMD --workdir work/AMD_20260225
uv run ./skills/fetch_fundamental/fetch_fundamental.py AMD --workdir work/AMD_20260225
uv run ./skills/fetch_edgar/fetch_edgar.py AMD --workdir work/AMD_20260225
uv run ./skills/fetch_wikipedia/fetch_wikipedia.py AMD --workdir work/AMD_20260225
```

### Database CLI

```bash
uv run ./skills/db.py init --workdir work/AMD_20260225 --dag dags/sra.yaml --ticker AMD
uv run ./skills/db.py task-ready --workdir work/AMD_20260225
uv run ./skills/db.py status --workdir work/AMD_20260225
```

### Template rendering

```bash
# Generic template renderer
./skills/render_template.py \
  --template templates/assemble_report.md.j2 \
  --output work/AMD_20260225/artifacts/report_body.md \
  --json work/AMD_20260225/artifacts/profile.json \
  --file intro=work/AMD_20260225/artifacts/draft_intro.md \
  --file body=work/AMD_20260225/artifacts/draft_report_body.md

# Final report assembly (loads all artifacts automatically)
./skills/render_final.py --workdir work/AMD_20260225
```

## Project Structure

```
├── dags/
│   └── sra.yaml                    # DAG definition (33 tasks, v2 schema)
├── skills/
│   ├── db.py                       # SQLite state management CLI
│   ├── db_commands.py              # DB command implementations
│   ├── schema.py                   # Pydantic DAG validation models
│   ├── config.py                   # Centralized constants
│   ├── utils.py                    # Shared utilities
│   ├── claude_runner.py            # Claude CLI subprocess runner
│   ├── assemble_text.py            # Section concatenation
│   ├── final_assembly.py           # Final report assembly
│   ├── render_template.py          # Generic Jinja2 renderer
│   ├── render_final.py             # Final report rendering (pandoc)
│   ├── fetch_profile/              # Company profile
│   ├── identify_peers/             # Peer identification
│   ├── fetch_technical/            # Chart + technical indicators
│   ├── fetch_fundamental/          # Financials, ratios, analyst data
│   ├── fetch_edgar/                # SEC filings
│   ├── fetch_wikipedia/            # Wikipedia summary
│   ├── fetch_detailed_profile_info/ # Parallel web-search profile tasks
│   ├── custom_research/            # User-provided investigation prompts
│   ├── chunk_index/                # Chunk, tag, build LanceDB index
│   ├── search_index/               # Hybrid vector + BM25 search
│   └── mcp_proxy/                  # MCP caching proxy with requestor tracking
├── templates/
│   ├── assemble_body.md.j2         # Body section concatenation
│   ├── assemble_report.md.j2       # Full report concatenation
│   └── final_report.md.j2          # Final formatted report with charts
├── scripts/
│   └── gen_mcp_configs.py          # Generate MCP configs from Claude Desktop
├── research.py                     # Async DAG orchestrator (entry point)
├── web.py                          # FastAPI web runner + WebSocket logs
├── tests/                          # pytest suite (210+ tests)
└── work/                           # Output (one dir per run)
    └── {SYMBOL}_{DATE}/
        ├── research.db
        ├── mcp-cache.db
        ├── drafts/
        └── artifacts/
```

## Script Conventions

All Python scripts follow a consistent pattern:

- `#!/usr/bin/env python3` shebang
- Import constants from `config.py`, utilities from `utils.py`
- `pathlib.Path` for all path operations
- `logger = setup_logging(__name__)` for output (stderr only)
- JSON manifest to stdout: `{"status": "complete", "artifacts": [...], "error": null}`
- Exit codes: 0 = success, 1 = partial, 2 = failure
- Type hints on all functions, specific exception handling
