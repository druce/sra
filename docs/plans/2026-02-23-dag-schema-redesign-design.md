# DAG Schema Redesign

## Problem

The current `dags/sra.yaml` schema has accumulated inconsistencies that make it fragile and hard to extend:

- Two field names for task type (`type:` vs `skill:`)
- Two field names for dependencies (`dependencies:` vs `depends_on:`)
- Arguments placed inconsistently (top-level `args:` vs nested `params.args:`)
- `params:` is an untyped grab-bag with different shapes per task type
- No schema validation — typos and missing fields silently ignored via `.get()` fallbacks
- Copy-paste bugs (e.g., `write_supply_chain` has wrong title/guidelines)
- `reads_from` uses aliases (`sec` instead of `sec_edgar`) — undocumented
- `tools: True` appears in 8 tasks but is never consumed
- 5 tasks have both `dependencies:` and `depends_on:` — redundant

## Design

### Approach

- **YAML-only authoring** — DAG files remain plain YAML, no Python needed to define workflows
- **Pydantic validation at load time** — `db.py init` parses YAML through Pydantic models; malformed files get clear error messages
- **Task types represent execution environments** — `python`, `claude`, `shell`, `perplexity`, `openai` (extensible)
- **`config:` typed per task type** — Pydantic discriminated union on the `type` field validates that each type has the right config shape
- **Break backward compatibility** — version bumped to 2, no migration shim for v1

### Schema version 2

#### DAG header

```yaml
dag:
  version: 2
  name: Equity Research Report Bot
  inputs:
    ticker: ${ticker}
    workdir: ${workdir}
  root_dir: ..
  template_dir: ../templates
```

Version 2 signals the new schema. `db.py init` rejects v1 files with a clear message.

#### Common task fields

Every task has these top-level fields:

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `description` | yes | string | Human-readable task description |
| `type` | yes | string | Execution environment: `python`, `claude`, `shell`, `perplexity`, `openai` |
| `depends_on` | no | list[str] | Task IDs that must complete before this task runs. Default: `[]` |
| `config` | yes | object | Configuration specific to the task type (validated per type) |
| `outputs` | no | dict | Named artifacts produced by this task |

#### Type: `python`

Runner executes: `python <script> --key value --key value`

Config fields:

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `script` | yes | string | Path to Python script (relative to `root_dir`) |
| `args` | no | dict[str, str] | Arguments passed as `--key value` CLI flags |

```yaml
profile:
  description: Get company profile data based on symbol
  type: python
  config:
    script: skills/research_profile.py
    args:
      ticker: "${ticker}"
      workdir: "${workdir}"
  outputs:
    profile:    {path: "artifacts/profile.json", format: json}
    peers_list: {path: "artifacts/peers_list.json", format: json}
```

#### Type: `claude`

Runner executes: `claude` CLI with flags mapped from config. `reads_from` artifacts are injected as context.

Config fields:

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `prompt` | yes | string | The prompt text (supports `${var}` substitution) |
| `system` | no | string | System prompt |
| `model` | no | string | Model ID (e.g., `claude-sonnet-4-6`) |
| `max_turns` | no | int | Max agentic turns |
| `tools` | no | list[str] | Allowed tools (maps to `--allowedTools`) |
| `reads_from` | no | list[str] | Task IDs whose artifacts are injected as context |

```yaml
write_company_profile:
  description: Write the Company Profile section
  type: claude
  depends_on: [perplexity, wikipedia, sec_edgar]
  config:
    prompt: |
      Write a Company Profile section for ${ticker}.
      Focus on origin story, history, milestones, description of current operations.
      Use the provided research artifacts as your source material.
      Output as markdown.
    system: "You are a senior equity research analyst writing a professional report."
    model: claude-sonnet-4-6
    max_turns: 10
    tools: [read, write, grep, glob]
    reads_from: [perplexity, wikipedia, sec_edgar]
  outputs:
    section: {path: "artifacts/02_company_profile.md", format: md}
```

Simple one-shot (no tools, no agent loop):

```yaml
polish:
  description: Final polish pass on assembled report
  type: claude
  depends_on: [assembly]
  config:
    prompt: "Review and polish this equity research report for clarity, consistency, and professional tone."
    reads_from: [assembly]
  outputs:
    final_report: {path: "final_report.md", format: md}
```

#### Type: `shell`

Runner executes: `bash -c "<command>"`

Config fields:

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `command` | yes | string | Shell command to execute (supports `${var}` substitution) |

```yaml
convert_pdf:
  description: Convert final report to PDF
  type: shell
  depends_on: [polish]
  config:
    command: "pandoc ${workdir}/final_report.md -o ${workdir}/final_report.pdf"
  outputs:
    pdf: {path: "final_report.pdf", format: pdf}
```

#### Type: `perplexity`

Runner calls Perplexity API with the prompt.

Config fields:

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `prompt` | yes | string | Prompt text (supports `${var}` substitution) |
| `model` | no | string | Perplexity model (e.g., `sonar-pro`). Default from `config.py` |
| `reads_from` | no | list[str] | Task IDs whose artifacts provide additional context |

```yaml
news_research:
  description: Research recent news via Perplexity
  type: perplexity
  depends_on: [profile]
  config:
    prompt: "Find the latest news stories about ${ticker} from the past 30 days."
    model: sonar-pro
  outputs:
    news: {path: "artifacts/news_stories.md", format: md}
```

#### Type: `openai`

Runner calls OpenAI API. Same shape as `perplexity`.

Config fields:

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `prompt` | yes | string | Prompt text |
| `model` | no | string | OpenAI model (e.g., `gpt-4o`) |
| `reads_from` | no | list[str] | Task IDs whose artifacts provide context |

### Pydantic model structure

```
DagFile
  dag: DagHeader
    version: int (must be 2)
    name: str
    inputs: dict[str, str]
    root_dir: str
    template_dir: str
  tasks: dict[str, Task]

Task (discriminated union on `type`)
  ├── PythonTask   (type="python")
  ├── ClaudeTask   (type="claude")
  ├── ShellTask    (type="shell")
  ├── PerplexityTask (type="perplexity")
  └── OpenAITask   (type="openai")

Each has:
  description: str
  type: Literal[...]
  depends_on: list[str] = []
  config: PythonConfig | ClaudeConfig | ShellConfig | ...
  outputs: dict[str, OutputDef] = {}
```

### Validation rules (beyond field types)

1. **Dependency references** — every ID in `depends_on` must exist as a task key
2. **`reads_from` references** — every ID in `reads_from` must exist as a task key
3. **No cycles** — topological sort must succeed
4. **Script paths** — `config.script` must point to an existing file (relative to `root_dir`)
5. **Variable substitution** — `${var}` references must match keys in `dag.inputs`
6. **Output path uniqueness** — no two tasks can write to the same output path

### Changes from v1

| v1 | v2 | Reason |
|----|-----|--------|
| `type:` and `skill:` both used | `type:` only | One field, one concept |
| `dependencies:` and `depends_on:` | `depends_on:` only | One field, one concept |
| `params:` untyped JSON blob | `config:` typed per type | Pydantic validates shape |
| `command:` field | removed | Redundant with `config.script` |
| Top-level `args:` and nested `params.args:` | `config.args` only | Consistent location |
| `reads_from` aliases (`sec`) | Must use exact task IDs | Validated against task list |
| `tools: True` (boolean) | `tools: [read, write, ...]` explicit list | Maps to `--allowedTools` |
| Task behavior hardcoded in runner by skill name | Expressed in `type` + `config` | Transparent, extensible |
| No validation | Pydantic models + cross-reference checks | Fail loudly on errors |

### Adding new task types

To add a new execution environment (e.g., `dbt`, `docker`):

1. Define a Pydantic config model for the type
2. Add it to the discriminated union
3. Implement the executor function in the task runner
4. Use it in YAML immediately — validation comes free
