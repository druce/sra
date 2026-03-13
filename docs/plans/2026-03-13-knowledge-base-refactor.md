# Knowledge Base Refactor: Artifacts vs Knowledge Base

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate structured artifacts (JSON/CSV/PNG) from unstructured text (MD/TXT) by routing text through a knowledge base pipeline (LanceDB) and having research agents produce markdown files instead of SQLite findings.

**Architecture:** Two data paths: structured artifacts stay as files in `artifacts/` (directly readable by writers), while all text flows through a `knowledge/` staging directory → chunk → tag → LanceDB index. Research agents write `knowledge/findings_{tag}.md` files that go through the same pipeline. LanceDB becomes the single knowledge base; text files are staging, not permanent artifacts.

**Tech Stack:** Python, LanceDB, OpenAI embeddings, existing chunk/tag/index pipeline

---

## Current vs Target Architecture

### Current

```
Data tasks → artifacts/ (all formats mixed)
                ↓ (text files only)
           chunk_documents → tag_chunks → build_index → LanceDB
                                                           ↓
           Research agents → finding-add → research.db → index_research → LanceDB (append)
                                                                            ↓
           Writers query LanceDB + read inline artifacts (JSON/CSV)
```

### Target

```
Data tasks → artifacts/ (JSON/CSV/PNG only — structured data)
           → knowledge/ (MD/TXT — staging for knowledge base)
                ↓
           chunk_documents → tag_chunks → build_index → LanceDB
                                                           ↓
           Research agents query LanceDB + read artifacts/ + use MCP
           Research agents → knowledge/findings_{tag}.md
                ↓
           chunk_research → tag_research → append_index → LanceDB
                                                            ↓
           Writers query LanceDB + read inline artifacts (JSON/CSV)
```

### Key principles

1. **`artifacts/`** = structured data (JSON, CSV, PNG). Directly readable. Persists through the run. Writers get these inline.
2. **`knowledge/`** = staging directory for text destined for LanceDB. Files here are intermediate — they exist on disk only until chunked, tagged, and indexed.
3. **LanceDB** = the knowledge base of record for all unstructured text. Research agents and writers query it, never read text files directly.
4. **Research agents** produce markdown files, not individual `finding-add` calls. One file per section tag (`findings_profile.md`, etc.).

---

## Increment 1: Research agents → markdown findings (remove finding-add)

This increment changes research agents from making `finding-add` tool calls to writing markdown files, and updates `index_research.py` to read those files instead of the `research_findings` table. MCP cache ingestion is removed — agents synthesize MCP responses into their markdown.

### File inventory

| File | Action | Purpose |
|------|--------|---------|
| `dags/sra.yaml` | Modify | Update 7 research task prompts + outputs; add `knowledge/` dir; remove MCP cache ingestion from `index_research` deps |
| `skills/chunk_index/index_research.py` | Rewrite | Read `knowledge/findings_*.md`, chunk, embed, tag from filename, append to LanceDB |
| `skills/db.py` | Modify | Drop `research_findings` table from schema |
| `skills/db_commands.py` | Modify | Remove `cmd_finding_add` and `cmd_finding_list` |
| `research.py` | Modify | Create `knowledge/` dir at init; remove finding-add from process_results if referenced |
| `tests/test_index_research.py` | Rewrite | Test new markdown-based ingestion |
| `tests/test_db_tasks.py` | Modify | Remove finding-add/finding-list tests |
| `CLAUDE.md` | Modify | Update docs: remove finding-add/finding-list from command reference |

### Task 1: Update research agent prompts in YAML

**Files:**
- Modify: `dags/sra.yaml:229-457` (all 7 research tasks)

- [ ] **Step 1: Add `knowledge/` directory creation**

The orchestrator (`research.py`) needs to create `knowledge/` before research agents run. Add to `init_pipeline()` or the main loop before the first wave that includes research tasks. Actually — simpler: each research agent's prompt can just `mkdir -p knowledge/`. But cleanest: `research.py` creates it alongside `artifacts/` and `drafts/`.

Add to `research.py` in `init_pipeline()` after workdir creation:
```python
(workdir / "knowledge").mkdir(exist_ok=True)
```

And in `run_single_task()` similarly:
```python
(workdir / "knowledge").mkdir(exist_ok=True)
```

- [ ] **Step 2: Add research task outputs to YAML**

Each research task gets a `findings_{tag}.md` output in `knowledge/`:

```yaml
research_profile:
    outputs:
      findings: {path: "knowledge/findings_profile.md", format: md, description: "Research findings on company profile, history, and management"}
```

Mapping for all 7:

| Task | Output path | Primary tag |
|------|------------|-------------|
| research_profile | knowledge/findings_profile.md | profile |
| research_business | knowledge/findings_business_model.md | business_model |
| research_competitive | knowledge/findings_competitive.md | competitive |
| research_supply_chain | knowledge/findings_supply_chain.md | supply_chain |
| research_financial | knowledge/findings_financial.md | financial |
| research_valuation | knowledge/findings_valuation.md | valuation |
| research_risk_news | knowledge/findings_risk_news.md | risk_news |

- [ ] **Step 3: Rewrite research agent prompts**

Remove all `finding-add` instructions. Replace with instructions to write a well-structured markdown document. The agent should:

1. Search the LanceDB index (unchanged)
2. Use MCP tools to fill gaps (unchanged)
3. **Synthesize everything into a single markdown file** with clear sections, inline source citations, and well-separated findings

Add a DAG-level var for the research output instructions:

```yaml
vars:
  research_output_instructions: |
    Write all your findings to a single markdown document.
    Structure it with clear ## headings for major topics.
    Separate distinct findings with --- horizontal rules.
    Cite sources inline: (Source: Wikipedia), (Source: 10-K Item 1A), (Source: FMP company-profile), etc.
    Include specific data points: numbers, dates, percentages, dollar amounts.
    Each finding should be a self-contained paragraph that makes sense without surrounding context
    (the document will be split into chunks for a search index).
```

Each research prompt becomes:

```yaml
prompt: |
  ${artifact_context}

  You are a research analyst investigating ${company_name} (${symbol}).
  [domain-specific instructions unchanged]

  1. Search the artifact index for relevant material:
     Run: uv run python skills/search_index/search_index.py "..." --workdir ${workdir} --sections ... --top-k 15

  2. Use available MCP tools to fill gaps.

  3. Read relevant structured artifacts in artifacts/ (JSON, CSV files) for specific data points.

  ${research_output_instructions}

  Save your findings to knowledge/findings_{tag}.md
```

- [ ] **Step 4: Run tests to verify YAML parses**

```bash
uv run pytest tests/test_schema_validation.py -x -v
```

Expected: all pass (the `vars` and `outputs` additions are valid v2 schema)

- [ ] **Step 5: Commit**

```bash
git add dags/sra.yaml research.py
git commit -m "feat: research agents write markdown findings instead of finding-add"
```

### Task 2: Rewrite index_research.py to read markdown files

**Files:**
- Rewrite: `skills/chunk_index/index_research.py`
- Test: `tests/test_index_research.py`

- [ ] **Step 1: Write the failing test**

```python
def test_index_research_reads_findings_md(tmp_path):
    """index_research reads knowledge/findings_*.md files and indexes them."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    knowledge_dir = workdir / "knowledge"
    knowledge_dir.mkdir()

    # Write a findings file
    (knowledge_dir / "findings_profile.md").write_text(
        "## Company History\n\nFounded in 1976 by Steve Jobs. (Source: Wikipedia)\n\n"
        "---\n\n## Recent News\n\nQ4 2025 revenue was $120B. (Source: 10-K)\n"
    )

    # Create a minimal LanceDB index to append to
    # ... (setup existing index with at least one record)

    # Run index_research
    chunks = read_research_findings(workdir)
    assert len(chunks) > 0
    assert all('"profile"' in c["tags"] for c in chunks)
    assert chunks[0]["doc_type"] == "research_finding"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_index_research.py::test_index_research_reads_findings_md -x -v
```

- [ ] **Step 3: Rewrite index_research.py**

The new version:

1. Globs `knowledge/findings_*.md`
2. For each file: strip `findings_` prefix and `.md` suffix → primary tag
3. Chunk using existing `chunk_text()` from `chunk_documents.py`
4. Assign primary tag from filename to all chunks
5. Embed all chunks
6. Append to existing LanceDB index
7. Rebuild FTS index

Key changes from current version:
- **Remove** `read_mcp_cache()` — agents now synthesize MCP responses into their markdown
- **Remove** `read_findings()` — no more `research_findings` table
- **Remove** `TASK_TO_SECTION` mapping — tag comes from filename
- **Add** `read_research_findings(workdir)` — reads `knowledge/findings_*.md`

```python
def read_research_findings(workdir: Path) -> list[dict]:
    """Read research findings markdown files and convert to chunks."""
    knowledge_dir = workdir / "knowledge"
    if not knowledge_dir.exists():
        logger.info("No knowledge directory found, skipping")
        return []

    all_chunks = []
    for md_file in sorted(knowledge_dir.glob("findings_*.md")):
        text = md_file.read_text(encoding="utf-8", errors="ignore")
        if not text.strip():
            continue

        # Derive primary tag from filename: findings_profile.md → profile
        tag = md_file.stem.removeprefix("findings_")
        source = f"knowledge/{md_file.name}"

        sub_chunks = chunk_text(text, source)
        for idx, chunk in enumerate(sub_chunks):
            chunk["id"] = f"findings_{tag}_{idx:04d}"
            chunk["tags"] = json.dumps([tag])
            chunk["doc_type"] = "research_finding"
        all_chunks.extend(sub_chunks)

    logger.info(f"Read {len(all_chunks)} chunks from {len(list(knowledge_dir.glob('findings_*.md')))} findings files")
    return all_chunks
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_index_research.py -x -v
```

- [ ] **Step 5: Commit**

```bash
git add skills/chunk_index/index_research.py tests/test_index_research.py
git commit -m "feat: index_research reads knowledge/findings_*.md instead of research.db"
```

### Task 3: Add tag_research and append_index tasks

After research findings are chunked and embedded, they need cross-tagging (a `profile` finding might also be relevant to `risk_news`). This mirrors the existing `tag_chunks → build_index` pattern.

**Files:**
- Modify: `dags/sra.yaml` — split `index_research` into `chunk_research` → `tag_research` → `append_index`

- [ ] **Step 1: Restructure post-research pipeline in YAML**

Replace single `index_research` task with three tasks:

```yaml
  # --- Post-research: chunk, tag, and index research findings ---
  chunk_research:
    sort_order: 27
    description: Chunk and embed research findings for knowledge base
    type: python
    depends_on: [research_profile, research_business, research_competitive,
                 research_supply_chain, research_financial, research_valuation, research_risk_news]
    config:
      script: skills/chunk_index/chunk_research.py
      args:
        ticker: "${ticker}"
        workdir: "${workdir}"
    outputs:
      chunks: {path: "lancedb/research_chunks.json", format: json, description: "Chunked and embedded research findings"}

  tag_research:
    sort_order: 28
    description: Cross-tag research findings chunks with additional section relevance
    type: claude
    depends_on: [chunk_research]
    config:
      prompt: |
        Read lancedb/research_chunks.json. Each chunk already has a primary tag from its source file.
        Your job is to add ADDITIONAL cross-relevant tags only.

        Valid tags: profile, business_model, competitive, supply_chain, financial, valuation, risk_news

        For each chunk, decide if it is relevant to sections BEYOND its primary tag.
        Examples:
        - A profile finding about CEO resignation → also tag risk_news
        - A financial finding about margin compression → also tag competitive, valuation
        - A supply chain finding about tariff risk → also tag risk_news, financial

        Only add cross-tags when the content would genuinely help a writer in that other section.
        Do NOT remove existing tags. Output ALL tags (primary + any additions).

        Write to lancedb/research_chunk_tags.json in this exact format:
        [{"id": "<chunk_id>", "tags": ["primary_tag", "cross_tag_1", ...]}, ...]

        Include every chunk. Do not skip any.
      disallowed_tools: [WebSearch, WebFetch, yfinance, alphavantage, brave-search, wikipedia, openbb-mcp, playwright]
    outputs:
      chunk_tags: {path: "lancedb/research_chunk_tags.json", format: json, description: "Cross-tagged research findings chunks"}

  append_index:
    sort_order: 29
    description: Append tagged research findings to LanceDB knowledge base
    type: python
    depends_on: [tag_research]
    config:
      script: skills/chunk_index/append_index.py
      args:
        ticker: "${ticker}"
        workdir: "${workdir}"
    outputs:
      research_index: {path: "lancedb/index/", format: lancedb,
                       description: "LanceDB index enriched with cross-tagged research findings"}
```

- [ ] **Step 2: Create `chunk_research.py`**

New script `skills/chunk_index/chunk_research.py`. Reads `knowledge/findings_*.md`, chunks, embeds, assigns primary tag from filename, writes `lancedb/research_chunks.json`.

This is essentially a focused version of `chunk_documents.py` but reads from `knowledge/` instead of `artifacts/manifest.json`.

```python
def main() -> int:
    workdir = Path(args.workdir)
    knowledge_dir = workdir / "knowledge"

    all_chunks = []
    for md_file in sorted(knowledge_dir.glob("findings_*.md")):
        text = md_file.read_text(encoding="utf-8", errors="ignore")
        if not text.strip():
            continue
        tag = md_file.stem.removeprefix("findings_")
        source = f"knowledge/{md_file.name}"
        sub_chunks = chunk_text(text, source)
        for idx, chunk in enumerate(sub_chunks):
            chunk["id"] = f"findings_{tag}_{idx:04d}"
            chunk["primary_tag"] = tag  # preserved for tag_research to read
            chunk["doc_type"] = "research_finding"
        all_chunks.extend(sub_chunks)

    client = OpenAI()
    all_chunks = embed_chunks(all_chunks, client)

    out_path = workdir / "lancedb" / "research_chunks.json"
    out_path.write_text(json.dumps(all_chunks, indent=2))
    # ... manifest output
```

- [ ] **Step 3: Create `append_index.py`**

New script `skills/chunk_index/append_index.py`. Mirrors `build_index.py` but appends instead of rebuilding:

1. Reads `lancedb/research_chunks.json` + `lancedb/research_chunk_tags.json`
2. Merges tags into chunks (same logic as `build_index.py`)
3. Appends to existing LanceDB `chunks` table
4. Rebuilds FTS index

```python
def main() -> int:
    # ... arg parsing
    chunks = json.loads(chunks_path.read_text())
    tags_list = json.loads(tags_path.read_text())
    tags_map = {t["id"]: t["tags"] for t in tags_list}

    for c in chunks:
        c["tags"] = json.dumps(tags_map.get(c["id"], [c.get("primary_tag", "research")]))

    db = lancedb.connect(str(index_dir))
    table = db.open_table("chunks")
    table.add(records)
    table.create_fts_index("text", replace=True)
```

- [ ] **Step 4: Write tests for chunk_research and append_index**

- [ ] **Step 5: Update writer `depends_on` in YAML**

Writers currently depend on `[index_research]`. Change to `[append_index]`:

```yaml
  write_profile:
    depends_on: [append_index]
```

- [ ] **Step 6: Run full test suite**

```bash
uv run pytest tests/ -x -v
```

- [ ] **Step 7: Commit**

```bash
git add skills/chunk_index/chunk_research.py skills/chunk_index/append_index.py dags/sra.yaml tests/
git commit -m "feat: three-stage post-research pipeline (chunk → tag → append)"
```

### Task 4: Remove research_findings table and finding-add/finding-list commands

**Files:**
- Modify: `skills/db.py:104-112` — remove `research_findings` CREATE TABLE from schema
- Modify: `skills/db_commands.py:460-506` — remove `cmd_finding_add` and `cmd_finding_list`
- Modify: `skills/db.py` — remove `finding-add` and `finding-list` subparser definitions
- Modify: `tests/test_db_tasks.py` — remove finding-related tests
- Modify: `CLAUDE.md` — remove finding-add/finding-list from command reference

- [ ] **Step 1: Remove schema table**

In `skills/db.py`, remove the `research_findings` CREATE TABLE from the SCHEMA string.

- [ ] **Step 2: Remove command implementations**

In `skills/db_commands.py`, remove `cmd_finding_add()` and `cmd_finding_list()`.

- [ ] **Step 3: Remove subparser definitions**

In `skills/db.py`, remove the `finding-add` and `finding-list` argument parser definitions.

- [ ] **Step 4: Remove tests**

In `tests/test_db_tasks.py` (or wherever finding tests live), remove finding-related test functions.

- [ ] **Step 5: Update CLAUDE.md**

Remove `finding-add` and `finding-list` from the db.py command reference table.

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/ -x -v
```

- [ ] **Step 7: Commit**

```bash
git add skills/db.py skills/db_commands.py tests/ CLAUDE.md
git commit -m "feat: remove research_findings table and finding-add/finding-list commands"
```

### Task 5: Remove old index_research.py (replaced by chunk_research + append_index)

**Files:**
- Delete: `skills/chunk_index/index_research.py` (replaced by `chunk_research.py` + `append_index.py`)
- Modify: `tests/test_index_research.py` — rewrite to test new scripts

- [ ] **Step 1: Delete old file, verify no imports reference it**

```bash
grep -r "index_research" skills/ --include="*.py" -l
```

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/ -x -v
```

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: remove old index_research.py, replaced by chunk_research + append_index"
```

---

## Increment 2: Route data-gathering text artifacts through knowledge base

This increment moves text artifacts (MD/TXT) from `artifacts/` to `knowledge/`, so ALL text flows through the same path: `knowledge/` → chunk → tag → LanceDB. Structured data (JSON/CSV/PNG) stays in `artifacts/`.

### Overview

Currently, data-gathering scripts write everything to `artifacts/`. After this change:

- `fetch_edgar` writes 10-K/10-Q markdown extracts to `knowledge/` (metadata JSON stays in `artifacts/`)
- `fetch_wikipedia` writes to `knowledge/` instead of `artifacts/`
- `detailed_profile` writes all 7 markdown files to `knowledge/`
- `custom_research` writes markdown outputs to `knowledge/`
- `chunk_documents` reads from `knowledge/` instead of scanning `artifacts/manifest.json` for text

### Task 6: Introduce `knowledge_base` output format in schema

**Files:**
- Modify: `skills/schema.py` — no schema change needed; `format: md` with path `knowledge/` is sufficient
- Modify: `dags/sra.yaml` — update output paths for text artifacts

The simplest approach: change output paths from `artifacts/*.md` to `knowledge/*.md` for text outputs. No schema model changes needed — the path itself signals the intent. `chunk_documents.py` will read from `knowledge/` instead of filtering `artifacts/manifest.json`.

- [ ] **Step 1: Update YAML output paths for text artifacts**

Change all text artifact outputs to `knowledge/` paths:

**fetch_edgar** (10-K/10-Q markdown items):
```yaml
# These are dynamically named, produced by the script.
# The script needs to write to knowledge/ instead of artifacts/.
# Output declaration stays as-is (the script controls actual paths).
```

Actually — edgar, detailed_profile, wikipedia, and custom_research scripts write their own file paths internally. We need to update the scripts, not just the YAML. The YAML `outputs` declarations need to match.

This is a larger change. For each script:
1. Update the script to write text files to `knowledge/` instead of `artifacts/`
2. Update the YAML `outputs` to point to `knowledge/` paths
3. Keep JSON/CSV/PNG outputs in `artifacts/`

### Task 7: Update fetch_wikipedia to write to knowledge/

**Files:**
- Modify: `skills/fetch_wikipedia/fetch_wikipedia.py` — change output paths
- Modify: `dags/sra.yaml` — update output paths

- [ ] **Step 1: Change script to write to `knowledge/`**

In `fetch_wikipedia.py`, change:
```python
# Old
summary_path = artifacts_dir / "wikipedia_summary.txt"
full_path = artifacts_dir / "wikipedia_full.txt"

# New
knowledge_dir = workdir / "knowledge"
knowledge_dir.mkdir(exist_ok=True)
summary_path = knowledge_dir / "wikipedia_summary.txt"
full_path = knowledge_dir / "wikipedia_full.txt"
```

Update manifest output paths accordingly.

- [ ] **Step 2: Update YAML outputs**

```yaml
wikipedia:
    outputs:
      wikipedia_summary: {path: "knowledge/wikipedia_summary.txt", format: txt, ...}
      wikipedia_full:    {path: "knowledge/wikipedia_full.txt", format: txt, ...}
```

- [ ] **Step 3: Run tests, commit**

### Task 8: Update detailed_profile to write text to knowledge/

**Files:**
- Modify: `skills/fetch_detailed_profile_info/fetch_detailed_profile_info.py`
- Modify: `dags/sra.yaml`

All 7 markdown outputs move from `artifacts/` to `knowledge/`:
- `knowledge/news_stories.md`
- `knowledge/business_profile.md`
- `knowledge/executive_profiles.md`
- `knowledge/risk_analysis.md`
- `knowledge/investment_thesis.md`
- `knowledge/competitive_analysis.md`
- `knowledge/business_model_analysis.md`

- [ ] **Step 1: Update script output paths**
- [ ] **Step 2: Update YAML outputs**
- [ ] **Step 3: Run tests, commit**

### Task 9: Update fetch_edgar to write text extracts to knowledge/

**Files:**
- Modify: `skills/fetch_edgar/fetch_edgar.py`
- Modify: `dags/sra.yaml`

10-K/10-Q markdown extracts → `knowledge/`. JSON metadata + CSV financials stay in `artifacts/`.

- [ ] **Step 1: Update script — text to knowledge/, structured to artifacts/**
- [ ] **Step 2: Update YAML outputs**
- [ ] **Step 3: Run tests, commit**

### Task 10: Update custom_research to write to knowledge/

**Files:**
- Modify: `skills/custom_research/custom_research.py`
- Modify: `dags/sra.yaml`

Custom research markdown → `knowledge/`. Tags JSON stays in `artifacts/`.

- [ ] **Step 1: Update script output paths**
- [ ] **Step 2: Update YAML outputs**
- [ ] **Step 3: Run tests, commit**

### Task 11: Update chunk_documents.py to read from knowledge/

**Files:**
- Modify: `skills/chunk_index/chunk_documents.py`
- Modify: `tests/test_chunk_documents.py`

Instead of reading `artifacts/manifest.json` and filtering for `.md`/`.txt`, read all files in `knowledge/`:

```python
# Old: iterate manifest, filter by extension
for entry in manifest:
    file_path = workdir / entry["file"]
    ext = file_path.suffix.lower()
    if ext not in TEXT_EXTENSIONS:
        continue

# New: glob knowledge/ for text files
knowledge_dir = workdir / "knowledge"
for file_path in sorted(knowledge_dir.glob("*")):
    if file_path.suffix.lower() not in TEXT_EXTENSIONS:
        continue
    text = file_path.read_text(encoding="utf-8", errors="ignore")
    if not text.strip():
        continue
    source = f"knowledge/{file_path.name}"
    chunks = chunk_text(text, source)
    all_chunks.extend(chunks)
```

This is simpler than the manifest approach — `knowledge/` is the single source directory for all text to be indexed.

- [ ] **Step 1: Write failing test for knowledge/ reading**
- [ ] **Step 2: Update chunk_documents.py**
- [ ] **Step 3: Run tests**
- [ ] **Step 4: Commit**

### Task 12: Update writer artifacts_inline lists

**Files:**
- Modify: `dags/sra.yaml` — writer `artifacts_inline` lists

Writers currently list text artifacts inline (e.g., `artifacts/news_stories.md`). Since those now live in `knowledge/`, two options:

**Option A:** Remove text files from `artifacts_inline` — writers get them via LanceDB search only.
**Option B:** Change paths to `knowledge/news_stories.md`.

**Recommendation:** Option A. The whole point of the knowledge base is that writers query LanceDB. Keeping large text files inline bloats the prompt. Writers should have inline: `manifest.json`, `profile.json`, CSVs, JSON summaries (structured data they can't get from LanceDB). Text is searchable via LanceDB.

- [ ] **Step 1: Update all writer artifacts_inline to remove MD/TXT files**

Keep only structured data in inline artifacts:
```yaml
artifacts_inline:
  - artifacts/manifest.json
  - artifacts/profile.json
  - artifacts/peers_list.json
  - artifacts/key_ratios.csv
  - artifacts/income_statement.csv
  - artifacts/balance_sheet.csv
  - artifacts/cash_flow.csv
  - artifacts/analyst_recommendations.json
  - artifacts/technical_analysis.json
  # Removed: news_stories.md, 8k_summary.json (text-like, available via LanceDB)
```

Wait — `8k_summary.json` is JSON, not text. Keep it. `news_stories.md` was text → remove.

- [ ] **Step 2: Run tests, commit**

---

## Increment 3 (Future): Further knowledge base evolution

Not implemented now, but the architecture enables:

1. **MongoDB for structured data** — replace `artifacts/*.json` and `artifacts/*.csv` with MongoDB collections. Writers query MongoDB for structured data, LanceDB for text.
2. **Graph database** — add a Neo4j or similar graph layer for entity relationships (company → competitors, company → suppliers, executive → company).
3. **Single `ingest` pipeline** — a generalized ingestion pipeline that routes any artifact to the right store based on format/type.
4. **Incremental LanceDB updates** — instead of full rebuild, use LanceDB merge/upsert for faster re-runs.

---

## Verification checklist

After each increment:

1. `uv run pytest tests/ -x` — all tests pass
2. `./skills/db.py validate --dag dags/sra.yaml --ticker TEST` — DAG validates
3. `./skills/db.py init --workdir /tmp/test_kb --dag dags/sra.yaml --ticker AAPL --date 20260313` — init succeeds, task count correct
4. Manual inspection: research task params contain resolved `${research_output_instructions}` and output to `knowledge/findings_*.md`
5. Full pipeline run on a test ticker to verify end-to-end
