# Research Quality Improvements — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 6 systemic issues found in the 7-agent audit of NVDA research findings: data conflicts, inconsistent artifact usage, dropped quantitative anchors, section-tag blinders, unsourced claims, and weak gap detection.

**Architecture:** A new Python script (`build_key_facts.py`) extracts authoritative metrics from structured artifacts (CSVs/JSONs) into `artifacts/key_facts.json`. This runs as a DAG task after `fundamental` and before research agents. Combined with prompt improvements to `research_output_instructions`, individual research prompts, and `tag_research`/`tag_chunks`, this gives research agents a single source of truth plus better search discipline.

**Tech Stack:** Python 3, pathlib, JSON, CSV — same patterns as existing `skills/` scripts. All other changes are YAML prompt edits in `dags/sra.yaml`.

**Source priority (user-specified):**
1. SEC filings (10-K, 10-Q, 8-K) — current authoritative
2. Market data from MCP servers (FMP, OpenBB, Finnhub)
3. Structured artifacts (CSV/JSON derived from above)
4. Perplexity analysis / custom research
5. Wikipedia and web pages — lowest priority, background only

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `skills/build_key_facts/build_key_facts.py` | Create | Extract authoritative metrics from CSVs/JSONs into `key_facts.json` |
| `skills/build_key_facts/__init__.py` | Create | Package init |
| `dags/sra.yaml` lines 4-18 (dag.vars) | Modify | Update `research_output_instructions` with source priority, reconciliation rules, quantitative anchor requirements, hallucination guardrail |
| `dags/sra.yaml` ~line 236 | Modify | Add `build_key_facts` task definition |
| `dags/sra.yaml` lines 238-687 | Modify | Update 7 research task prompts: add mandatory cross-section queries, checklist verification step, key_facts.json reference |
| `dags/sra.yaml` lines 199-221 | Modify | Improve `tag_chunks` prompt for better cross-tagging |
| `dags/sra.yaml` lines 704-732 | Modify | Improve `tag_research` prompt with quantitative signal detection and cross-section routing |

---

## Task 1: Create `build_key_facts.py`

**Files:**
- Create: `skills/build_key_facts/__init__.py`
- Create: `skills/build_key_facts/build_key_facts.py`

This script reads structured artifacts and extracts a flat, authoritative `key_facts.json` that research agents read before searching the index. When structured artifacts disagree with text sources, this file wins.

- [ ] **Step 1: Create package init**

```python
# skills/build_key_facts/__init__.py
```

- [ ] **Step 2: Write `build_key_facts.py`**

The script should:
1. Read `income_statement.csv` → extract latest-year revenue, gross profit, operating income, net income, EPS, R&D, SGA, EBITDA, shares outstanding, gross/operating/net margins (computed)
2. Read `balance_sheet.csv` → extract total assets, total equity, total debt, cash, inventory, working capital, shares outstanding
3. Read `cash_flow.csv` → extract FCF, operating cash flow, capex, buybacks, dividends, acquisitions (Purchase Of Business)
4. Read `key_ratios.csv` → extract all ratios for the subject ticker AND peers (P/E, EV/EBITDA, etc.)
5. Read `profile.json` → extract market cap, price, beta, 52-week range, sector, industry, employees, company name
6. Read `analyst_recommendations.json` → extract rating distribution, count
7. Read `8k_summary.json` → extract count of recent 8-Ks, most recent filing date
8. Read `sec_10k_metadata.json` and `sec_10q_metadata.json` → extract filing dates, fiscal year

Output structure:
```json
{
  "meta": {"ticker": "NVDA", "company_name": "...", "generated": "2026-03-15"},
  "financials": {
    "revenue": {"value": 215938000000, "display": "$215.9B", "period": "FY2026", "source": "income_statement.csv"},
    "gross_profit": {"value": "...", "display": "...", "period": "...", "source": "..."},
    "..."
  },
  "balance_sheet": { "..." },
  "cash_flow": { "..." },
  "ratios": {
    "NVDA": {"trailing_pe": 36.79, "forward_pe": 16.69, "..."},
    "TSM": { "..." },
    "..."
  },
  "profile": { "..." },
  "analyst": { "..." },
  "filings": { "..." }
}
```

Each value includes `source` field for traceability. Use `display` field for human-readable formatting (e.g., "$215.9B" not "215938000000").

Follow existing conventions:
- `#!/usr/bin/env python3` shebang
- `pathlib.Path` for all paths
- `logger = setup_logging(__name__)`
- `argparse` with `--workdir` flag
- JSON manifest to stdout: `{"status": "complete", "artifacts": [...]}`
- Return exit codes from `main()`
- Import constants from `config.py`, utilities from `utils.py`

```bash
# Usage:
./skills/build_key_facts/build_key_facts.py SYMBOL --workdir work/SYMBOL_DATE
```

- [ ] **Step 3: Make executable**

```bash
chmod +x skills/build_key_facts/build_key_facts.py
```

- [ ] **Step 4: Test locally against NVDA data**

```bash
uv run python skills/build_key_facts/build_key_facts.py NVDA --workdir work/NVDA_20260315
```

Expected: JSON manifest on stdout, `artifacts/key_facts.json` created with all sections populated. Verify a few values manually against the CSVs.

- [ ] **Step 5: Commit**

```bash
git add skills/build_key_facts/
git commit -m "feat: add build_key_facts.py to extract authoritative metrics from structured artifacts"
```

---

## Task 2: Add `build_key_facts` task to DAG

**Files:**
- Modify: `dags/sra.yaml` (~line 236, before research tasks)

- [ ] **Step 1: Add task definition**

Insert after `build_index` (sort_order 12) and before `research_profile` (sort_order 20). Use sort_order 13. This task depends on `fundamental` (which produces the CSVs) and `profile` (which produces profile.json) and `fetch_edgar` (which produces 8k_summary, sec_*_metadata). It runs in parallel with `chunk_documents` → `tag_chunks` → `build_index` since they're independent pipelines.

```yaml
  build_key_facts:
    sort_order: 13
    description: Extract authoritative financial metrics from structured artifacts
    type: python
    depends_on: [fundamental, profile, fetch_edgar]
    config:
      script: skills/build_key_facts/build_key_facts.py
      args:
        ticker: "${ticker}"
        workdir: "${workdir}"
    outputs:
      key_facts: {path: "artifacts/key_facts.json", format: json, description: "Authoritative financial metrics extracted from structured artifacts — source of truth for all research agents"}
```

- [ ] **Step 2: Update research task dependencies**

All 7 research tasks currently depend on `[build_index]`. Update each to depend on `[build_index, build_key_facts]`:

```yaml
  research_profile:
    depends_on: [build_index, build_key_facts]
  research_business:
    depends_on: [build_index, build_key_facts]
  research_competitive:
    depends_on: [build_index, build_key_facts]
  research_supply_chain:
    depends_on: [build_index, build_key_facts]
  research_financial:
    depends_on: [build_index, build_key_facts]
  research_valuation:
    depends_on: [build_index, build_key_facts]
  research_risk_news:
    depends_on: [build_index, build_key_facts]
```

- [ ] **Step 3: Validate DAG**

```bash
./skills/db.py validate --dag dags/sra.yaml --ticker TEST
```

Expected: validation passes, no cycles.

- [ ] **Step 4: Commit**

```bash
git add dags/sra.yaml
git commit -m "feat: add build_key_facts task to DAG, update research task dependencies"
```

---

## Task 3: Update `research_output_instructions` with source priority and guardrails

**Files:**
- Modify: `dags/sra.yaml` lines 11-18 (dag.vars.research_output_instructions)

- [ ] **Step 1: Replace `research_output_instructions`**

Replace the existing `research_output_instructions` variable (lines 11-18) with:

```yaml
    research_output_instructions: |
      **Source Priority (use the highest-priority source available for each claim):**
      1. SEC filings (10-K, 10-Q, 8-K) — current authoritative for all disclosed facts
      2. MCP tool results (FMP, OpenBB, Finnhub) — authoritative for current market data
      3. Structured artifacts (CSV/JSON in artifacts/) — derived from sources 1-2
      4. Perplexity analysis, custom research — supplementary
      5. Wikipedia and web pages — background context only, never cite for financial data

      **Reconciliation rule:** When two sources disagree on a number (e.g., revenue, margins,
      EBITDA), use the SEC filing value. If no SEC value exists, use the structured artifact
      (key_facts.json). Note the discrepancy: "Note: [source A] reports X while [source B]
      reports Y; using SEC filing value."

      **Quantitative anchor rule:** Every section heading MUST have at least one specific
      number (dollar amount, percentage, date, or count) in the paragraph below it.
      Before saving, scan every ## heading — if any lacks a data point, search for one.

      **Citation rule:** Every quantitative claim MUST have an inline source citation.
      Format: (Source: 10-K Item 1A), (Source: key_facts.json), (Source: FMP company-profile), etc.
      If you cannot cite a source for a number, prefix with [UNVERIFIED] so downstream
      writers know to validate or omit it.

      Write all your findings to a single markdown document.
      Structure it with clear ## headings for major topics.
      Separate distinct findings with --- horizontal rules.
      Each finding should be a self-contained paragraph that makes sense without surrounding context
      (the document will be split into chunks for a search index).
```

- [ ] **Step 2: Commit**

```bash
git add dags/sra.yaml
git commit -m "feat: add source priority, reconciliation, quantitative anchors to research_output_instructions"
```

---

## Task 4: Update research task prompts with cross-section queries and checklist verification

**Files:**
- Modify: `dags/sra.yaml` lines 238-687 (all 7 research task prompts)

Each research task prompt gets two additions:
1. **Step 0.5** (after decomposing questions, before searching): Read `artifacts/key_facts.json` as the authoritative baseline.
2. **Step 1 addendum**: 2-3 mandatory cross-section search queries specific to the task.
3. **Final step**: Checklist verification against the research brief before saving.

- [ ] **Step 1: Add key_facts.json read to all 7 research prompts**

After the existing Step 0 ("Decompose your research brief...") in each prompt, insert:

```
        **Step 0.5: Read authoritative baselines.**
        Read artifacts/key_facts.json — this contains verified financial metrics extracted
        from structured data. Use these numbers as ground truth. If you find a conflicting
        number in the index or MCP results, note the discrepancy and prefer key_facts.json
        for SEC-derived data, or MCP for current market data.
```

- [ ] **Step 2: Add mandatory cross-section queries to each research task**

Insert after Step 1's primary section search in each task. These are the specific additions per task:

**research_profile** (currently searches `--sections profile`):
```
          Also run cross-section searches to catch profile-relevant data tagged elsewhere:
          Run: uv run python skills/search_index/search_index.py --workdir ${workdir} --query "customer concentration revenue top customer" --sections financial business_model --top-k 10
          Run: uv run python skills/search_index/search_index.py --workdir ${workdir} --query "board director resignation appointment governance" --sections risk_news --top-k 5
```

**research_business** (currently searches `--sections business_model`):
```
          Also run cross-section searches:
          Run: uv run python skills/search_index/search_index.py --workdir ${workdir} --query "market share decline ASIC custom silicon displacement" --sections competitive --top-k 10
          Run: uv run python skills/search_index/search_index.py --workdir ${workdir} --query "working capital DSO DIO DPO cash conversion" --sections financial --top-k 5
```

**research_competitive** (currently searches `--sections competitive`):
```
          Also run cross-section searches:
          Run: uv run python skills/search_index/search_index.py --workdir ${workdir} --query "TSMC supply chain foundry packaging CoWoS" --sections supply_chain --top-k 10
          Run: uv run python skills/search_index/search_index.py --workdir ${workdir} --query "segment revenue market share growth rate" --sections business_model --top-k 10
```

**research_supply_chain** (currently searches `--sections supply_chain`):
```
          Also run cross-section searches:
          Run: uv run python skills/search_index/search_index.py --workdir ${workdir} --query "purchase commitments inventory provisions supply obligations" --sections financial risk_news --top-k 10
          Run: uv run python skills/search_index/search_index.py --workdir ${workdir} --query "export control tariff geopolitical" --sections risk_news --top-k 10
```

**research_financial** (currently searches `--sections financial`):
```
          Also run cross-section searches:
          Run: uv run python skills/search_index/search_index.py --workdir ${workdir} --query "acquisition purchase business M&A deal" --sections profile risk_news --top-k 10
          Run: uv run python skills/search_index/search_index.py --workdir ${workdir} --query "gross margin operating margin trend pressure" --sections business_model competitive --top-k 10
```

**research_valuation** (currently searches `--sections valuation`):
```
          Also run cross-section searches:
          Run: uv run python skills/search_index/search_index.py --workdir ${workdir} --query "buyback repurchase share count dilution" --sections financial --top-k 10
          Run: uv run python skills/search_index/search_index.py --workdir ${workdir} --query "growth driver catalyst forward guidance" --sections business_model risk_news --top-k 10
```

**research_risk_news** (currently searches `--sections risk_news`):
```
          Also run cross-section searches:
          Run: uv run python skills/search_index/search_index.py --workdir ${workdir} --query "HBM memory CoWoS packaging capacity supplier" --sections supply_chain --top-k 10
          Run: uv run python skills/search_index/search_index.py --workdir ${workdir} --query "EU regulation AI act compliance gaming export" --sections competitive business_model --top-k 10
```

- [ ] **Step 3: Add checklist verification step to all 7 research prompts**

Replace the existing step 4 ("Evaluate information sufficiency") in each task with:

```
        4. **Evaluate information sufficiency.** Review your findings against the research brief.
          List any gaps — questions you cannot yet answer with evidence. For each gap:
          - Run additional search_index.py queries with different search terms
          - Try alternative MCP tool calls with different parameters
          Repeat until all brief questions are addressed or you've exhausted available
          sources (max 2 additional search rounds).

        5. **Final checklist before saving.** Re-read your research brief (the "Ensure you have
          all key facts" list above) item by item. For each item:
          - Confirm you have at least one paragraph with a specific data point.
          - If not, run one targeted search_index.py query for that item.
          - If still no data, note "[GAP: {item}] — no data found in index or MCP sources."
          Verify that every ## heading in your output has at least one quantitative anchor.
          Verify that no number lacks a source citation.
```

- [ ] **Step 4: Commit**

```bash
git add dags/sra.yaml
git commit -m "feat: add cross-section queries, key_facts baseline, checklist verification to research prompts"
```

---

## Task 5: Improve `tag_chunks` prompt for better initial cross-tagging

**Files:**
- Modify: `dags/sra.yaml` lines 199-221 (tag_chunks task)

The current prompt says "apply tags generously" but gives no examples of cross-tagging patterns. Improve it.

- [ ] **Step 1: Update tag_chunks prompt**

Replace the existing prompt with:

```yaml
      prompt: |
        ${artifact_context}

        Read lancedb/chunks.json. For each chunk, assign relevance tags from this list:
        profile, business_model, competitive, supply_chain, financial, valuation, risk_news

        A chunk may have multiple tags. Apply tags generously — it is better to over-tag
        than under-tag. Use these cross-tagging rules:

        **Always cross-tag these patterns:**
        - Customer concentration data → business_model + financial + risk_news
        - Revenue/margin by segment → business_model + financial
        - Competitor names, market share → competitive + business_model
        - Supply commitments, purchase obligations → supply_chain + financial + risk_news
        - Inventory provisions, write-downs → financial + supply_chain
        - Export controls, tariffs, sanctions → risk_news + supply_chain + competitive
        - Acquisition/M&A activity → profile + financial + competitive
        - Analyst targets, price estimates → valuation + financial
        - Product roadmap, launches → business_model + competitive
        - Board/executive changes → profile + risk_news
        - Gross margin drivers → financial + business_model + competitive
        - Geographic revenue mix → business_model + supply_chain

        **Quantitative signal rule:** Any chunk containing a specific dollar amount (>$1B),
        percentage, or ratio should be tagged with at least `financial` in addition to its
        primary tag.

        Write a JSON file to lancedb/chunk_tags.json in this exact format:
        [{"id": "<chunk_id>", "tags": ["tag1", "tag2"]}, ...]

        Include every chunk from chunks.json. Do not skip any.
```

- [ ] **Step 2: Commit**

```bash
git add dags/sra.yaml
git commit -m "feat: improve tag_chunks prompt with cross-tagging patterns and quantitative signal rule"
```

---

## Task 6: Improve `tag_research` prompt for cross-section routing

**Files:**
- Modify: `dags/sra.yaml` lines 704-732 (tag_research task)

The current prompt has 3 examples. Expand with the same cross-tagging patterns and add quantitative signal detection.

- [ ] **Step 1: Update tag_research prompt**

Replace the existing prompt (lines 711-729) with:

```yaml
      prompt: |
        Read lancedb/research_chunks.json. Each chunk already has a primary tag from its
        source file (e.g., findings_financial.md → "financial"). Your job is to add
        ADDITIONAL cross-relevant tags so that writers for other sections can discover
        this content via section-filtered search.

        Valid tags: profile, business_model, competitive, supply_chain, financial, valuation, risk_news

        **Cross-tagging rules (apply all that match):**
        - Customer concentration, revenue by customer → business_model, financial, risk_news
        - Revenue/margin by segment → business_model, financial
        - Competitor names, market share, product comparisons → competitive, business_model
        - Supply commitments, inventory, purchase obligations → supply_chain, financial, risk_news
        - Export controls, tariffs, sanctions, regulatory → risk_news, supply_chain, competitive
        - Acquisition/M&A, deal values → profile, financial, competitive
        - Analyst targets, multiples, price estimates → valuation, financial
        - Product roadmap, new launches, pipeline → business_model, competitive
        - Gross margin drivers/pressure → financial, business_model, competitive
        - Geographic revenue/exposure → business_model, supply_chain
        - CEO/board/governance → profile, risk_news
        - Working capital, cash conversion → financial, business_model
        - Capex, R&D spending → financial, business_model, competitive

        **Quantitative signal rule:** If a chunk contains a specific dollar amount (>$1B),
        a percentage, a ratio, or a named metric, always include `financial` as a tag
        (in addition to other tags).

        Only add cross-tags when the content would genuinely help a writer in that section.
        Do NOT remove existing tags. Output ALL tags (primary + any additions).

        Write to lancedb/research_chunk_tags.json in this exact format:
        [{"id": "<chunk_id>", "tags": ["primary_tag", "cross_tag_1", ...]}, ...]

        Include every chunk. Do not skip any.
```

- [ ] **Step 2: Commit**

```bash
git add dags/sra.yaml
git commit -m "feat: improve tag_research prompt with comprehensive cross-tagging rules"
```

---

## Summary of Changes by Issue

| Issue | Fix | Tasks |
|-------|-----|-------|
| 1. Cross-task data reconciliation | `key_facts.json` as source of truth + reconciliation rule in `research_output_instructions` | 1, 2, 3 |
| 2. Inconsistent structured artifact usage | `key_facts.json` pre-extracted + Step 0.5 baseline read | 1, 2, 4 |
| 3. Quantitative anchors dropped | Quantitative anchor rule in `research_output_instructions` + checklist verification | 3, 4 |
| 4. Section-tag blinders | Mandatory cross-section queries per task + improved `tag_chunks` and `tag_research` | 4, 5, 6 |
| 5. No hallucination guardrail | Citation rule with [UNVERIFIED] prefix in `research_output_instructions` | 3, 4 |
| 6. Weak gap detection | Final checklist step with item-by-item brief verification | 4 |
| User: SEC data authoritative | Source priority hierarchy in `research_output_instructions` + `key_facts.json` source tracking | 1, 3 |
| User: MCP > Wikipedia | Source priority hierarchy explicitly ranks MCP above Wikipedia | 3 |
