# Implementation Plan: Claude Code Orchestrator Rewrite

## Context

The existing `stock_research_agent` project uses a Python orchestrator (`research_stock.py`) with subprocess calls to run a multi-phase equity research pipeline. We're rewriting this as a new project (`sra5`) where Claude Code is the orchestrator, using a generic DAG runner that executes tasks defined in YAML. The existing project stays as a working reference — scripts are copied and adapted, not moved.

Build order: **DAG infrastructure first** → **individual skills** → **end-to-end wiring**.

---

## Phase 1: Project Skeleton + DAG Infrastructure

### Step 1.1: Create new project directory

```
sra5/
├── dags/                     # DAG YAML definitions
├── skills/                   # Python scripts + Claude Code skill definitions
├── templates/                # Jinja report templates
├── work/                     # Runtime output (gitignored)
├── requirements.txt
├── .gitignore
└── CLAUDE.md                 # Project instructions for Claude Code
```

- [x] Create directory structure
- [x] Initialize git repo
- [x] Create `.gitignore` (copy from existing project, add `work/`, `*.db`)
- [x] Create `requirements.txt` / `pyproject.toml` with PyYAML + existing project deps

**Runtime artifact layout** (each run creates `work/{SYMBOL}_{DATE}/`):

```
work/TSLA_20260222/
├── research.db
├── dag.yaml                          # working copy of DAG (may differ from dags/sra.yaml if user edited)
└── artifacts/
    ├── profile.json
    ├── peers_list.json
    ├── chart.png
    ├── technical_analysis.json
    ├── income_statement.csv
    ├── balance_sheet.csv
    ├── cash_flow.csv
    ├── key_ratios.csv
    ├── analyst_recommendations.json
    ├── news_stories.md
    ├── business_profile.md
    ├── executive_profiles.md
    ├── sec_filings_index.json
    ├── sec_10k_metadata.json
    ├── sec_10k_item1_business.md
    ├── sec_10k_item1a_risk_factors.md
    ├── sec_10k_item7_mda.md
    ├── sec_10q_metadata.json
    ├── sec_10q_item2_mda.md
    ├── sec_10q_financial_tables.csv
    ├── sec_income_annual.csv / sec_income_quarterly.csv
    ├── sec_balance_annual.csv / sec_balance_quarterly.csv
    ├── sec_cashflow_annual.csv / sec_cashflow_quarterly.csv
    ├── sec_8k_summary.json
    ├── wikipedia_summary.txt
    ├── business_model_analysis.md
    ├── competitive_analysis.md
    ├── risk_analysis.md
    ├── investment_thesis.md
    ├── {task_id}_draft.md            # intermediate critic-optimizer files
    ├── {task_id}_critique.md
    ├── 00_executive_summary.md
    ├── 01_fundamental_analysis.md
    ├── 02_company_profile.md
    ├── 03_business_model.md
    ├── 04_competitive_landscape.md
    ├── 05_supply_chain.md
    ├── 06_leverage.md
    ├── 07_valuation.md
    ├── 08_news.md
    ├── 09_risks.md
    ├── 10_thesis.md
    ├── 11_conclusion.md
    ├── research_report.md            # assembled (pre-polish)
    └── final_report.md               # polished final output
```

### Step 1.2: Build `skills/db.py`

The SQLite CLI utility. Foundation everything else depends on.

**New file — no existing equivalent.**

**SQLite Schema:**

```sql
CREATE TABLE research (
    id            INTEGER PRIMARY KEY,
    ticker        TEXT NOT NULL,
    date          TEXT NOT NULL,
    dag_file      TEXT NOT NULL,           -- path to YAML DAG file
    template_dir  TEXT NOT NULL,           -- path to templates directory
    workdir       TEXT NOT NULL,           -- directory for artifacts and reports
    status        TEXT DEFAULT 'not started',  -- not started|running|complete|failed
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT
);

CREATE TABLE tasks (
    id            TEXT PRIMARY KEY,        -- matches YAML task id
    skill         TEXT NOT NULL,           -- python|claude|shell (stored as 'skill' column)
    description   TEXT,                    -- human-readable description from YAML
    params        TEXT NOT NULL,           -- JSON: type-specific config + outputs + sets_vars
    concurrency   TEXT DEFAULT 'parallel',
    status        TEXT DEFAULT 'pending',  -- pending|running|complete|failed|skipped
    started_at    TEXT,
    completed_at  TEXT,
    error         TEXT,
    summary       TEXT                     -- brief result for downstream tasks
);

CREATE TABLE task_deps (
    task_id       TEXT NOT NULL REFERENCES tasks(id),
    depends_on    TEXT NOT NULL REFERENCES tasks(id),
    PRIMARY KEY (task_id, depends_on)
);

CREATE TABLE artifacts (
    id            INTEGER PRIMARY KEY,
    task_id       TEXT NOT NULL REFERENCES tasks(id),
    name          TEXT NOT NULL,
    path          TEXT NOT NULL,           -- relative to workdir
    format        TEXT NOT NULL,           -- json|csv|md|png|txt
    description   TEXT,                    -- static description of artifact content (from YAML)
    source        TEXT,                    -- yfinance|finnhub|perplexity|claude
    summary       TEXT,                    -- runtime summary of what was produced
    size_bytes    INTEGER,
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE dag_vars (
    name          TEXT PRIMARY KEY,        -- variable name (e.g. company_name)
    value         TEXT NOT NULL,           -- resolved value (e.g. "Tesla, Inc.")
    source_task   TEXT REFERENCES tasks(id), -- task that produced it (nullable)
    created_at    TEXT DEFAULT (datetime('now'))
);
```

**Commands (implemented):**

| Command | Purpose |
|---------|---------|
| `init --workdir W --dag D --ticker T [--date D]` | Create db, parse YAML, populate tasks + task_deps |
| `validate --dag D [--ticker T]` | Parse and validate DAG YAML without creating a database |
| `task-ready --workdir W` | JSON array of dispatchable tasks (pending, all deps terminal: complete/skipped/failed) |
| `task-get --workdir W --task-id T` | Full task config as JSON |
| `task-update --workdir W --task-id T --status S [--summary S] [--error E]` | Update task state |
| `artifact-add --workdir W --task-id T --name N --path P --format F [--description D] [--source S] [--summary S]` | Register artifact |
| `artifact-list --workdir W [--task T]` | List artifacts as JSON (includes description field) |
| `status --workdir W` | Overview: research status, all tasks, artifact counts |
| `research-update --workdir W --status S` | Update overall research status (not started\|running\|complete\|failed) |
| `var-set --workdir W --name N --value V [--source-task T]` | Set a runtime DAG variable (upsert) |
| `var-get --workdir W [--name N]` | Get one variable (with metadata) or all as `{name: value}` dict |
| `task-context --workdir W --task-id T` | Resolve dependency artifacts for a task (returns all artifacts from depends_on tasks) |

**Two-phase variable substitution:**

1. **Init time (static):** `init` substitutes `${ticker}`, `${date}`, `${workdir}` in all string values before inserting into the database. These are baked in — downstream consumers never see template variables for these.

2. **Dispatch time (runtime):** Tasks can declare `sets_vars` that extract values from their output artifacts after completion. Each entry maps a variable name to an artifact path + JSON key. After a task completes, the runner reads the artifact, extracts the value, and stores it via `var-set`. Before dispatching a downstream task, the runner calls `var-get` and substitutes any remaining `${var}` placeholders (like `${company_name}`) in task params.

**`sets_vars` format in YAML:**
```yaml
sets_vars:
  symbol:       {artifact: "artifacts/profile.json", key: "symbol"}
  company_name: {artifact: "artifacts/profile.json", key: "company_name"}
```

**Pydantic model:** `SetsVarDef(artifact: str, key: str)` — stored in task params JSON as `params.sets_vars`.

**Duplicate artifact handling:** If `artifact-add` is called with the same `(task_id, name)` pair, update the existing row rather than inserting a new one.

**Error handling rules:**
- `--workdir` doesn't exist: create it (for `init`); error for other commands
- `research.db` doesn't exist for non-init commands: error with "run init first"
- `task_id` doesn't exist: error with message
- Artifact path doesn't exist on disk: still register it (`size_bytes` = null), log warning to stderr

**Dependencies:** stdlib only (`sqlite3`, `json`, `argparse`, `pathlib`) + PyYAML for DAG parsing + Pydantic for schema validation (`skills/schema.py`).

- [x] Implement SQLite schema creation (research, tasks, task_deps, artifacts, dag_vars)
- [x] Implement DAG YAML parser via Pydantic models (`skills/schema.py`)
- [x] Implement `init` command with recursive variable substitution
- [x] Implement `task-ready` query (the core DAG algorithm)
- [x] Implement `task-get`, `task-update` (set `started_at` on running, `completed_at` on terminal)
- [x] Implement `artifact-add` (upsert on task+name) with `--description`, `artifact-list` with description field
- [x] Implement `status` overview
- [x] Implement `research-update`
- [x] Implement `task-context` (resolve dependency artifacts for a task)
- [x] Implement `var-set` (upsert runtime variable), `var-get` (get one or all)
- [x] Implement `validate` (parse + check deps reference valid task IDs, no cycles, no duplicate output paths)
- [x] Add `--help` for all commands

### Step 1.3: Copy supporting files from existing project

**Copy as-is:**
- `skills/utils.py` — logging, formatting, directory helpers (100% reusable)
- `skills/lookup_ticker.py` — ticker validation

**Copy and adapt:**
- `skills/config.py` — remove orchestrator-specific constants (`MAX_PARALLEL_WORKERS`, phase execution config), keep API keys, timeouts, technical indicators, research config

Source: `../stock_research_agent/skills/`

- [x] Copy `utils.py`
- [x] Copy and adapt `config.py`
- [x] Copy `lookup_ticker.py`

### Step 1.4: Build `dags/sra.yaml`

The default DAG. Defines all tasks with typed configs, dependencies, expected outputs with descriptions, and optional `sets_vars` for runtime variable extraction. Uses the v2 schema validated by `skills/schema.py`.

**`sets_vars` field:** Tasks can declare variables they provide to downstream tasks. Format: `{artifact: "path", key: "json_key"}` — after the task completes, the runner reads the artifact file and extracts the JSON key value, then registers it via `var-set`. These runtime variables are available as `${var_name}` in downstream task params.

**`outputs.description` field:** Each output includes a `description` string providing static metadata about the artifact's contents. This helps downstream tasks understand what data is available without reading the file.

The authoritative DAG is `dags/sra.yaml`. Condensed structure (showing one example of each type):

```yaml
dag:
  version: 2
  name: Equity Research Report Bot
  inputs:
    ticker: "${ticker}"
    workdir: "${workdir}"
  root_dir: /Users/drucev/projects/sra5
  template_dir: templates

tasks:
  # --- Data gathering (type: python) ---
  profile:
    description: Get company profile data based on symbol
    type: python
    config:
      script: skills/fetch_profile/fetch_profile.py
      args:
        ticker: "${ticker}"
        workdir: "${workdir}"
    outputs:
      profile:    {path: "artifacts/profile.json", format: json, description: "Company identity and valuation snapshot"}
      peers_list: {path: "artifacts/peers_list.json", format: json, description: "Peer companies with symbol, name, price, market cap"}
    sets_vars:
      symbol:       {artifact: "artifacts/profile.json", key: "symbol"}
      company_name: {artifact: "artifacts/profile.json", key: "company_name"}

  technical:
    description: Generate stock chart and technical indicators
    type: python
    depends_on: [profile]
    config:
      script: skills/fetch_technical/fetch_technical.py
      args: {ticker: "${ticker}", workdir: "${workdir}"}
    outputs:
      chart:              {path: "artifacts/chart.png", format: png, description: "1-year price chart with SMA, volume, RSI, MACD"}
      technical_analysis: {path: "artifacts/technical_analysis.json", format: json, description: "Technical indicators and trend signals"}

  # fundamental, perplexity, fetch_edgar, wikipedia, perplexity_analysis — all type: python, depends_on: [profile]

  # --- Body writing (type: claude) ---
  write_body:
    description: Write the main report body (sections 2-8)
    type: claude
    depends_on: [profile, technical, fundamental, perplexity, fetch_edgar, wikipedia, perplexity_analysis]
    config:
      system: "You are a senior equity research analyst writing a professional report."
      tools: all
      prompt: |
        Write a comprehensive report on ${ticker} covering sections 2-8:
        Extended Profile, Business Model, Competitive Landscape, Supply Chain,
        Financial Leverage, Valuation, and Risk Factors...
    outputs:
      section: {path: "artifacts/draft_report_body.md", format: md, description: "Draft report body sections 2-8"}

  write_conclusion:
    depends_on: [write_body]
    # ...

  write_intro:
    depends_on: [write_body, write_conclusion]
    # ...

  # --- Assembly + Critique + Polish ---
  assemble_text:
    type: python
    depends_on: [write_intro, write_body, write_conclusion]
    # ...

  critique_body_final:
    type: claude
    depends_on: [assemble_text]
    # ...

  polish_body_final:
    type: claude
    depends_on: [critique_body_final]
    # ...

  final_assembly:
    type: python
    depends_on: [polish_body_final, technical, fundamental]
    # ...
```

**Pydantic schema models** (`skills/schema.py`):
- `OutputDef(path, format, description)` — artifact definition with static description
- `SetsVarDef(artifact, key)` — runtime variable extraction rule
- `PythonConfig(script, args)` — runs a Python script
- `ClaudeConfig(prompt, system, tools, ...)` — invokes Claude Code CLI (maps to `claude -p` flags)
- `ShellConfig(command)` — runs a shell command
- `_TaskBase(description, depends_on, outputs, sets_vars)` — common task fields
- `DagFile(dag: DagHeader, tasks: dict[str, Task])` — root model

**ClaudeConfig fields** map to `claude -p` CLI flags: `prompt`, `system`, `append_system`, `model`, `fallback_model`, `tools` (string "all" or list), `allowed_tools`, `disallowed_tools`, `permission_mode`, `skip_permissions`, `max_budget_usd`, `output_format`, `json_schema`, `effort`, `add_dirs`, `mcp_config`.

- [x] Create `dags/sra.yaml`

### Step 1.5: Verify DAG infrastructure

Run this sequence to prove the DAG walker works:

```bash
./skills/db.py init --workdir /tmp/test --dag dags/sra.yaml --ticker TSLA --date 20260222

./skills/db.py task-ready --workdir /tmp/test
# expect: ["profile"] (no deps — root task)

./skills/db.py task-update --workdir /tmp/test --task-id profile --status complete
./skills/db.py var-set --workdir /tmp/test --name symbol --value TSLA --source-task profile
./skills/db.py var-set --workdir /tmp/test --name company_name --value "Tesla, Inc." --source-task profile
./skills/db.py var-get --workdir /tmp/test
# expect: {"company_name": "Tesla, Inc.", "symbol": "TSLA"}

./skills/db.py task-ready --workdir /tmp/test
# expect: ["technical", "fundamental", "perplexity", "fetch_edgar", "wikipedia", "perplexity_analysis"]

./skills/db.py task-update --workdir /tmp/test --task-id technical --status complete
./skills/db.py task-update --workdir /tmp/test --task-id fundamental --status complete
./skills/db.py task-update --workdir /tmp/test --task-id perplexity --status complete
./skills/db.py task-update --workdir /tmp/test --task-id fetch_edgar --status failed --error "timeout"
./skills/db.py task-update --workdir /tmp/test --task-id wikipedia --status complete
./skills/db.py task-update --workdir /tmp/test --task-id perplexity_analysis --status complete
./skills/db.py task-ready --workdir /tmp/test
# expect: write_body (all its deps are terminal: complete, skipped, or failed)

./skills/db.py status --workdir /tmp/test
# expect: overview showing 6 complete, 1 failed, 7 pending (14 total tasks)
```

Verified via automated tests (`tests/test_db.py`, `tests/test_schema.py`):
- [x] Run verification sequence
- [x] Confirm `task-ready` returns correct tasks at each step
- [x] Confirm failed deps don't block downstream tasks (complete/skipped both satisfy deps)

---

## Phase 2: Claude Code Skills

### Step 2.1: Build `skills/taskrunner.md`

Claude Code skill that dispatches a single task. Receives `task_id` + `workdir`. Entry sequence:

```bash
task = db.py task-get --workdir {workdir} --task-id {task_id}
db.py task-update --workdir {workdir} --task-id {task_id} --status running

# Resolve runtime variables in task config before dispatch
vars = db.py var-get --workdir {workdir}
resolved_config = substitute_vars(task.params, vars)

# ... dispatch based on task.skill using resolved_config ...

# After task completes: extract sets_vars → register as dag_vars
# (See "Variable extraction after task completion" below)
```

---

#### Type: `python`

Run the Python script with args converted from `config.args` dict to CLI flags:

```bash
cd {root_dir} && python {config.script} {ticker} --workdir {workdir} [--key value ...]
```

**Arg conversion rule:** Each key-value pair in `config.args` (other than `ticker` which is positional) becomes `--key value`. Underscores in keys become hyphens: `peers_file` → `--peers-file`.

**Environment note:** Scripts call `load_environment()` from `utils.py` at startup to self-load the project root `.env`. The taskrunner does not need to pre-export env vars.

**Manifest parsing:** Capture stdout as JSON:

```json
{
  "status": "complete|partial|failed",
  "artifacts": [
    {"name": "profile", "path": "artifacts/profile.json", "format": "json",
     "source": "yfinance", "summary": "TSLA market cap $892B, P/E 64.2"}
  ],
  "error": null
}
```

**Exit code + manifest status → task status mapping:**

| Exit code | Manifest status | Task status | Note |
|-----------|----------------|-------------|------|
| 0 | `complete` | `complete` | |
| 0 or 1 | `partial` | `complete` | summary notes what's missing |
| 2+ | any | `failed` | error from manifest.error |

**Artifact registration:** For each artifact in manifest:

```bash
db.py artifact-add --workdir {workdir} --task-id {task_id} \
  --name {name} --path {path} --format {format} \
  --source {source} --summary {summary}
```

**Variable extraction after task completion:** If the task's params include `sets_vars`, extract values from output artifacts and register them:

```python
# For each sets_var: var_name → {artifact: "path", key: "json_key"}
# e.g. sets_vars: {company_name: {artifact: "artifacts/profile.json", key: "company_name"}}
for var_name, var_def in task.params.get("sets_vars", {}).items():
    artifact_path = workdir / var_def["artifact"]
    data = json.loads(artifact_path.read_text())
    value = data[var_def["key"]]
    db.py var-set --workdir {workdir} --name {var_name} --value {value} --source-task {task_id}
```

Extraction is skipped for failed tasks (no variables produced, downstream prompts keep unresolved `${var}` placeholders).

---

#### Type: `claude` (critic-loop pattern)

The taskrunner IS a Claude Code agent, so it runs this loop directly. The `config.agent_pattern` determines the execution strategy.

**For `critic-loop` pattern (body writers):**

**Step 1: GATHER**
- Use `db.py task-context --workdir {workdir} --task-id {task_id}` to resolve `config.reads_from` → artifact metadata *(not yet implemented)*
- Read each artifact file at `{workdir}/{artifact.path}`
- Also read `task.summary` from `db.py task-get` for context on what the task found
- Bookend tasks (executive_summary, conclusion) also read the section `.md` files from body writers

**Step 2: DRAFT** (using `config.steps.write.prompt`)
- Write section markdown
- Save to `{workdir}/artifacts/{task_id}_draft.md`

**Step 3: CRITIQUE** (using `config.steps.critique.prompt`)
- Re-read the draft against all five criteria:
  1. **Data accuracy** — does every claim trace to a specific artifact?
  2. **Specificity** — concrete numbers, not vague assertions?
  3. **Thesis clarity** — clear analytical point, not just description?
  4. **Completeness** — anything important from the data left out?
  5. **Conciseness** — anything redundant or filler?
- Save critique to `{workdir}/artifacts/{task_id}_critique.md`

**Step 4: REVISE** (using `config.steps.revise.prompt`)
- Rewrite addressing every critique point
- Repeat steps 3-4 until grade >= `config.agent_pattern.min_grade` or `config.agent_pattern.max_iterations` reached
- Save final to the output path from the DAG (e.g. `{workdir}/artifacts/02_company_profile.md`)
- Register artifact + update task status complete

**For `simple` pattern (bookend writers, polish):** Execute `config.steps.write.prompt` once, no critique loop.

**Section guidelines by task** (defined in DAG step prompts):

| Section | Key guidelines |
|---------|---------------|
| Executive Summary | Max 300 words. Investment stance in first sentence. Key metrics. Why now. |
| Fundamental Analysis | Use tables for financials. Highlight YoY changes. Compare to peer medians. |
| Company Profile | Focus on origin story, history, milestones, current operations. |
| Business Model | What makes money and why. Revenue segments. Competitive moat. |
| Competitive Landscape | Peer comparison table. Market share. Differentiation. |
| Supply Chain | Key suppliers, dependencies, geographic exposure, logistics. |
| Leverage | Sensitivity to interest rates, economic environment, input/output prices. |
| Valuation | Appropriate methodologies, metrics, peer comparisons. |
| Recent Developments | Recent news, exec changes, rating changes. |
| Risk Analysis | Be specific: "revenue concentration: 48% from X". Categorize: operational, financial, regulatory, market. |
| Investment Thesis & SWOT | Bull/bear/base cases with price implications. SWOT table. |
| Conclusion | Synthesize across sections. Clear recommendation. Key watchpoints. Catalysts. |

---

#### Type: `shell`

Run a shell command directly:

```bash
{config.command}
```

Not currently used in the default DAG but supported for extensibility.

---

- [ ] Write `skills/taskrunner.md` skill definition
- [ ] Implement `python` type dispatch with arg conversion and exit code mapping
- [ ] Implement `claude` type dispatch: critic-loop (GATHER → DRAFT → CRITIQUE → REVISE) and simple patterns
- [ ] Implement `shell` type dispatch
- [ ] Verify artifact registration uses `db.py artifact-add` for every produced file

### Step 2.2: Build `skills/research.md`

Claude Code skill — the `/research` entry point. Runs four phases:

---

#### Phase 1: INTAKE

1. Parse arguments: ticker (required), dag_file (default: `dags/sra.yaml`)
2. Validate ticker — use `lookup_ticker.py` if symbol looks ambiguous
3. Create workdir: `work/{TICKER}_{YYYYMMDD}/`
4. Read DAG YAML and present task summary to user:

```
Equity Research Report Bot for TSLA

Tasks (18):
  profile          → python       (no deps)
  technical        → python       (depends: profile)
  fundamental      → python       (depends: profile)
  perplexity       → python       (depends: profile)
  fetch_edgar      → python       (depends: profile)
  wikipedia        → python       (depends: profile)
  analysis         → python       (depends: profile)
  write_company_profile      → claude  (depends: perplexity, wikipedia, fetch_edgar)
  write_business_model       → claude  (depends: perplexity, wikipedia, fetch_edgar)
  ...
  write_executive_summary    → claude  (depends: all body writers)
  write_conclusion           → claude  (depends: all body writers)
  assembly         → python       (depends: write_executive_summary, write_conclusion)
  polish           → claude       (depends: assembly)

Proceed? [Y/n/edit]
```

5. If user chooses **edit** — interactive DAG customization (conversational):
   - **Remove tasks:** "remove sec_edgar, wikipedia" — remove from YAML; tasks that *only* depend on removed tasks are also removed or marked skipped
   - **Edit guidelines:** "change write_risks guidelines to: Focus on regulatory risk only"
   - **Add tasks:** "add write_esg after analysis with guidelines: ESG scoring and sustainability"
   - User makes edits conversationally, then confirms
6. Save working DAG to `work/{TICKER}_{DATE}/dag.yaml` (this is the file used for `init`, not the original)

---

#### Phase 2: INIT

```bash
./skills/db.py init --workdir {workdir} --dag {workdir}/dag.yaml --ticker {ticker} --date {date}
./skills/db.py research-update --workdir {workdir} --status running
```

---

#### Phase 3: DAG LOOP

```
iteration = 1
repeat:
    ready = ./skills/db.py task-ready --workdir {workdir}

    if ready is empty:
        status = ./skills/db.py status --workdir {workdir}
        if all tasks are terminal (complete/failed/skipped):
            → done, proceed to COMPLETION
        else:
            → DEADLOCK: print which tasks are stuck and their unsatisfied deps
            → ./skills/db.py research-update --workdir {workdir} --status failed
            → exit loop

    dispatch all ready tasks in parallel:
        for each task in ready:
            spawn /taskrunner task_id={task.id} workdir={workdir} as background subagent

    wait for all subagents to complete

    check for stale running tasks:
        query status — if any tasks still in 'running':
            retry once: re-dispatch a new /taskrunner subagent for that task
            if still running after retry: task-update --status failed --error "taskrunner timeout"

    print iteration summary:
        Iteration N complete: K tasks finished
          ✓ fundamental  — 6 artifacts, market cap $892B
          ✓ perplexity   — 3 artifacts, 12 news stories
          ✗ fetch_edgar  — FAILED: SEC EDGAR timeout
          ✓ wikipedia    — 1 artifact

        Next ready: write_company_profile (reduced data: no 10-K), write_business_model, ...

    iteration += 1
```

**Error handling rules for the DAG runner:**

| Scenario | Behavior |
|----------|----------|
| Task fails | Record in `tasks.error`; DAG continues |
| All required deps failed | Mark downstream task as `skipped` |
| Some deps available, some failed | Taskrunner works with reduced data |
| Script exit 1 (partial success) | Mark task `complete`; summary notes missing data |
| Stale running task | Retry once; if retry fails, mark `failed` |
| Deadlock | Print blocked tasks + unsatisfied deps; mark research `failed` |
| User abort (Ctrl+C) | Print status; already-dispatched subagents continue running in background |

**Timeouts** (enforce via `max_turns` or equivalent when spawning subagents):

| Task type | Timeout | Rationale |
|-----------|---------|-----------|
| `python` | 5 min | API calls + fallback chains |
| `claude` (critic-loop) | 3 min | Draft + critique + revise |
| `claude` (simple) | 2 min | Single-pass writing |
| `shell` | 1 min | Quick commands |

---

#### Phase 4: COMPLETION

```bash
./skills/db.py research-update --workdir {workdir} --status complete
```

Print final summary:

```
Research complete for TSLA

Status: 17/18 tasks complete, 1 failed

Outputs:
  - work/TSLA_20260222/artifacts/research_report.md (assembled)
  - work/TSLA_20260222/artifacts/final_report.md (polished)

Failed tasks:
  - fetch_edgar: SEC EDGAR timeout

Run ./skills/db.py status --workdir work/TSLA_20260222 for full details
```

---

- [ ] Write `skills/research.md` skill definition
- [ ] Implement Phase 1 INTAKE: ticker validation, DAG presentation format, conversational edit flow
- [ ] Implement Phase 2 INIT: `db.py init` + `research-update --status running`
- [ ] Implement Phase 3 DAG LOOP: parallel dispatch, stale task retry, deadlock detection, iteration summary format
- [ ] Implement Phase 4 COMPLETION: `research-update --status complete` + final summary output

### Step 2.3: Build `skills/assemble.py`

Python script: reads completed section artifacts from db, runs Jinja template, writes assembled report.

**Source reference:** Simplified adaptation of `stock_research_agent/skills/research_report.py` — but instead of loading raw data from files and building a complex context, it just reads pre-written section markdown and feeds section titles + content to a simple Jinja template.

```python
# Pseudocode
sections = db.artifact_list(task_filter="write_*")
context = {
    "ticker": research.ticker,
    "date": research.date,
    "sections": [{"title": task.params.title, "content": read(artifact.path)} for ...]
}
rendered = jinja_env.get_template(template).render(context)
write(workdir / "research_report.md", rendered)
```

- [ ] Implement `assemble.py`
- [ ] Handle missing sections gracefully (note gaps in output)
- [ ] Print JSON manifest to stdout (consistent with script contract)

---

## Phase 3: Migrate Python Scripts

For each script: copy from `../stock_research_agent/skills/`, then adapt.

### Python Script Contract

Every data-fetching script follows this uniform interface:

**CLI:**
```bash
./skills/fetch_technical/fetch_technical.py TSLA --workdir work/TSLA_20260222
```

**Exit codes:** 0 (success), 1 (partial — some data missing but usable artifacts produced), 2 (failure — no usable output)

**Stdout:** JSON manifest only — all other output goes to stderr:
```json
{
  "status": "complete",
  "artifacts": [
    {
      "name": "profile",
      "path": "artifacts/profile.json",
      "format": "json",
      "source": "yfinance",
      "summary": "TSLA market cap $892B, P/E 64.2, sector: Consumer Cyclical"
    }
  ],
  "error": null
}
```

For partial success: `"status": "partial"`, `"error": "description of what's missing"`, artifacts array contains whatever was produced.

**Changes common to all scripts:**
1. Keep all core logic (API calls, parsing, fallback chains)
2. Standardize CLI: `TICKER --workdir PATH [extra flags]`
3. Add JSON manifest output to stdout (structure above)
4. Standardize exit codes: 0 (success), 1 (partial), 2 (failure)
5. All logging/status output → stderr only (never pollute stdout manifest)

### Step 3.1: `fetch_profile.py`

Fetches company profile and identifies peer companies. First task in the DAG — all others depend on it.

- [x] Create `skills/fetch_profile/fetch_profile.py`
- [x] Output `artifacts/profile.json` (company name, sector, description, market cap, exchange) and `artifacts/peers_list.json` (list of peer tickers)
- [x] Add JSON manifest (2 artifacts: profile, peers_list)
- [x] Standardize exit codes

### Step 3.2: `fetch_technical.py`

Existing CLI: `symbol --work-dir [--peers] [--no-filter-peers]` — minimal changes needed.

- [x] Copy from `../stock_research_agent/skills/fetch_technical.py`
- [x] Normalize `--work-dir` → `--workdir`
- [x] Add JSON manifest (2 artifacts: chart, technical_analysis)
- [x] Standardize exit codes

### Step 3.3: `fetch_fundamental.py`

Existing CLI: `symbol --work-dir [--verbose]`

- [x] Copy from `../stock_research_agent/skills/fetch_fundamental.py`
- [x] Add `--peers-file` flag (currently reads from hardcoded path; pass `artifacts/peers_list.json`)
- [x] Add JSON manifest (5 artifacts: income_statement, balance_sheet, cash_flow, key_ratios, analyst_recommendations)
- [x] Standardize exit codes

### Step 3.4: `fetch_perplexity.py`

Existing CLI: `symbol --work-dir`

- [x] Copy from `../stock_research_agent/skills/fetch_perplexity.py`
- [x] Add JSON manifest (3 artifacts: news_stories, business_profile, executive_profiles)
- [x] Standardize exit codes

### Step 3.5: `fetch_edgar.py`

Existing CLI: `symbol --work-dir`

- [x] Copy from `../stock_research_agent/skills/fetch_edgar.py`
- [x] Add JSON manifest (5 artifacts: filings_index, 10k_items, 10q_items, financials, 8k_summary)
- [x] Standardize exit codes

### Step 3.6: `fetch_wikipedia.py`

Existing CLI: `symbol --work-dir`

- [x] Copy from `../stock_research_agent/skills/fetch_wikipedia.py`
- [x] Add JSON manifest (1 artifact: wikipedia_summary)
- [x] Standardize exit codes

### Step 3.7: `fetch_perplexity_analysis.py`

Existing CLI: `symbol --work-dir`

- [x] Copy from `../stock_research_agent/skills/fetch_analysis.py` → `skills/fetch_perplexity_analysis/fetch_perplexity_analysis.py`
- [x] Accept `--workdir` and discover artifacts from workdir (currently has hardcoded paths)
- [x] Add JSON manifest (4 artifacts: business_model_analysis, competitive_analysis, risk_analysis, investment_thesis)
- [x] Standardize exit codes

---

## Phase 4: Templates

### Step 4.1: Create simplified assembly template

New template that iterates pre-written sections — much simpler than the existing templates since each section is already fully written by a subagent.

```jinja
# {{ ticker }} — Equity Research Report

**Date:** {{ date }}

{% for section in sections %}
## {{ section.title }}

{{ section.content }}

{% endfor %}

---
*Generated by Stock Research Agent*
```

- [ ] Create `templates/equity_research_report.md.j2`

### Step 4.2: Copy existing templates as alternatives

Copy from `../stock_research_agent/templates/` for users who want the structured format with hardcoded sections and detailed variable interpolation.

- [ ] Copy `equity_research_report.md.j2` → `templates/detailed_report.md.j2`
- [ ] Copy `final_report.md.j2` → `templates/final_report.md.j2`

---

## Phase 5: End-to-End Wiring + Verification

### Step 5.1: Write `CLAUDE.md`

Project instructions for Claude Code:
- Project structure and purpose
- How to use `/research` and `/taskrunner` skills
- DAG YAML format reference
- `db.py` command reference
- Python script contract (manifest format, exit codes)

- [x] Write `CLAUDE.md`

### Step 5.2: End-to-end test

```
1. /research TSLA
2. Verify intake presents DAG correctly
3. Verify db.py init populates all 14 tasks
4. Verify DAG loop dispatches profile first (iteration 1)
5. Verify parallel dispatch of 6 data gathering tasks (iteration 2: technical, fundamental, perplexity, fetch_edgar, wikipedia, perplexity_analysis)
6. Verify write_body runs after data gathering (iteration 3)
7. Verify write_conclusion runs after write_body (iteration 4)
8. Verify write_intro runs after write_body + write_conclusion (iteration 5)
9. Verify assemble_text assembles sections (iteration 6)
10. Verify critique_body_final critiques assembled report (iteration 7)
11. Verify polish_body_final revises based on critique (iteration 8)
12. Verify final_assembly produces final_report.md (iteration 9)
13. db.py status shows all 14 tasks complete (or some failed with graceful degradation)
```

- [ ] Run full end-to-end test
- [ ] Verify all artifacts registered in db
- [ ] Verify final report contains all sections
- [ ] Review report quality

### Step 5.3: Error handling tests

**Task failure + graceful degradation:**
```
1. Set invalid SEC API key to force fetch_edgar to fail
2. Run /research TSLA
3. Verify DAG continues — fetch_edgar fails, other tasks unaffected
4. Verify write_company_profile runs with reduced data (no 10-K sections)
5. db.py status shows 1 failed, rest complete
6. Verify final report does not crash on missing sections
```

**Partial success (exit code 1):**
```
1. Simulate a script returning exit 1 with partial manifest
2. Verify task marked 'complete' (not 'failed')
3. Verify task summary notes what's missing
4. Verify downstream writers work with available data
```

**Deadlock detection:**
```
1. Manually set a task to 'running' (simulating stale state) in the db
2. Trigger DAG loop — verify deadlock detection fires
3. Verify research.status set to 'failed'
4. Verify blocked tasks and unsatisfied deps are printed
```

- [ ] Run graceful degradation test
- [ ] Run partial success test
- [ ] Run deadlock detection test

---

## Files Summary

### New files to create

| File | Phase | Status | Purpose |
|------|-------|--------|---------|
| `skills/db.py` | 1.2 | **Done** | SQLite CLI — init, validate, task-ready, task-get, task-update, artifact-add/list, var-set/var-get, status, research-update |
| `skills/schema.py` | 1.2 | **Done** | Pydantic models for DAG YAML v2 schema (OutputDef with description, SetsVarDef, typed task configs) |
| `dags/sra.yaml` | 1.4 | **Done** | Default DAG for equity research (v2 schema, output descriptions, sets_vars) |
| `tests/test_schema.py` | 1.2 | **Done** | Schema validation tests |
| `tests/test_db.py` | 1.2 | **Done** | db.py command tests (90 tests total) |
| `skills/taskrunner.md` | 2.1 | TODO | Claude Code skill: dispatch a single task by type (python, claude, shell) |
| `skills/research.md` | 2.2 | TODO | Claude Code skill: intake + DAG runner loop (4 phases) |
| `skills/assemble_text.py` | 2.3 | TODO | Read sections, assemble complete report |
| `templates/equity_research_report.md.j2` | 4.1 | TODO | Simplified section-iteration template |
| `CLAUDE.md` | 5.1 | **Done** | Project instructions for Claude Code |

### Files copied and adapted from `../stock_research_agent/skills/`

| File | Phase | Status | Changes |
|------|-------|--------|---------|
| `utils.py` | 1.3 | **Done** | Copy as-is |
| `config.py` | 1.3 | **Done** | Remove orchestrator constants, keep API/research config |
| `lookup_ticker.py` | 1.3 | **Done** | Copy as-is |
| `fetch_profile/fetch_profile.py` | 3.1 | **Done** | New — company profile + peer identification |
| `fetch_technical/fetch_technical.py` | 3.2 | **Done** | Add JSON manifest, normalize CLI flags |
| `fetch_fundamental/fetch_fundamental.py` | 3.3 | **Done** | Add manifest, add `--peers-file` flag |
| `fetch_perplexity/fetch_perplexity.py` | 3.4 | **Done** | Add manifest |
| `fetch_edgar/fetch_edgar.py` | 3.5 | **Done** | Add manifest (full SEC artifact set) |
| `fetch_wikipedia/fetch_wikipedia.py` | 3.6 | **Done** | Add manifest |
| `fetch_perplexity_analysis/fetch_perplexity_analysis.py` | 3.7 | **Done** | Add manifest, discover artifacts from workdir |

### Files NOT migrated (replaced by Claude Code skills)

| Old File | Replaced By |
|----------|-------------|
| `research_stock.py` | `/research` skill (DAG runner) |
| `research_report.py` | `assemble.py` + `claude` type tasks |
| `research_final.py` | `claude` type polish task |
| `research_deep.py` | `claude` type writer tasks (they ARE Claude) |
