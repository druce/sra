# DAG Schema v2 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the inconsistent, unvalidated DAG YAML schema with a typed, Pydantic-validated v2 schema and migrate the existing DAG file.

**Architecture:** New `skills/schema.py` defines Pydantic models for each task type (python, claude, shell, perplexity, openai) using a discriminated union on the `type` field. `db.py init` loads YAML, passes it through Pydantic, gets clean typed objects, then populates SQLite. The YAML format changes but stays YAML-only authoring.

**Tech Stack:** Pydantic v2 (already in pyproject.toml), PyYAML (already used), pytest (add as dev dependency)

---

### Task 1: Add pytest and create test infrastructure

**Files:**
- Modify: `pyproject.toml` (add pytest dev dependency)
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_schema.py` (placeholder)

**Step 1: Add pytest as dev dependency**

Run: `cd /Users/drucev/projects/sra5 && uv add --dev pytest`

**Step 2: Create test directory and files**

Create `tests/__init__.py` (empty).

Create `tests/conftest.py`:

```python
import sys
from pathlib import Path

# Allow imports from skills/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills"))
```

Create `tests/test_schema.py`:

```python
"""Tests for DAG schema v2 Pydantic models."""


def test_placeholder():
    assert True
```

**Step 3: Verify pytest runs**

Run: `cd /Users/drucev/projects/sra5 && uv run pytest tests/ -v`
Expected: 1 test collected, PASS

**Step 4: Commit**

```bash
git add pyproject.toml uv.lock tests/
git commit -m "Add pytest and test infrastructure"
```

---

### Task 2: Define output and base config Pydantic models

**Files:**
- Create: `skills/schema.py`
- Modify: `tests/test_schema.py`

**Step 1: Write failing tests for OutputDef and base models**

Add to `tests/test_schema.py`:

```python
import pytest
from schema import OutputDef, DagHeader


def test_output_def_valid():
    out = OutputDef(path="artifacts/profile.json", format="json")
    assert out.path == "artifacts/profile.json"
    assert out.format == "json"


def test_output_def_missing_path():
    with pytest.raises(Exception):
        OutputDef(format="json")


def test_output_def_missing_format():
    with pytest.raises(Exception):
        OutputDef(path="artifacts/profile.json")


def test_dag_header_valid():
    header = DagHeader(
        version=2,
        name="Test DAG",
        inputs={"ticker": "${ticker}", "workdir": "${workdir}"},
        root_dir="..",
        template_dir="../templates",
    )
    assert header.version == 2
    assert header.name == "Test DAG"


def test_dag_header_wrong_version():
    with pytest.raises(Exception):
        DagHeader(
            version=1,
            name="Test DAG",
            inputs={},
            root_dir="..",
            template_dir="../templates",
        )


def test_dag_header_defaults():
    header = DagHeader(version=2, name="Test")
    assert header.inputs == {}
    assert header.root_dir == "."
    assert header.template_dir == "templates"
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/drucev/projects/sra5 && uv run pytest tests/test_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'schema'`

**Step 3: Implement OutputDef and DagHeader**

Create `skills/schema.py`:

```python
"""DAG Schema v2 — Pydantic models for YAML validation.

Defines typed models for each task execution environment (python, claude,
shell, perplexity, openai). YAML is loaded and validated through these
models at db.py init time.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator


class OutputDef(BaseModel):
    """A named artifact produced by a task."""
    path: str
    format: str


class DagHeader(BaseModel):
    """Top-level DAG metadata."""
    version: Literal[2]
    name: str
    inputs: dict[str, str] = {}
    root_dir: str = "."
    template_dir: str = "templates"
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/drucev/projects/sra5 && uv run pytest tests/test_schema.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add skills/schema.py tests/test_schema.py
git commit -m "Add OutputDef and DagHeader Pydantic models"
```

---

### Task 3: Define per-type config models

**Files:**
- Modify: `skills/schema.py`
- Modify: `tests/test_schema.py`

**Step 1: Write failing tests for config models**

Add to `tests/test_schema.py`:

```python
from schema import PythonConfig, ClaudeConfig, ShellConfig, PerplexityConfig, OpenAIConfig


def test_python_config_valid():
    cfg = PythonConfig(script="skills/research_profile.py", args={"ticker": "AAPL"})
    assert cfg.script == "skills/research_profile.py"
    assert cfg.args == {"ticker": "AAPL"}


def test_python_config_missing_script():
    with pytest.raises(Exception):
        PythonConfig(args={"ticker": "AAPL"})


def test_python_config_no_args():
    cfg = PythonConfig(script="skills/run.py")
    assert cfg.args == {}


def test_claude_config_valid():
    cfg = ClaudeConfig(
        prompt="Write a report about ${ticker}",
        system="You are an analyst.",
        model="claude-sonnet-4-6",
        max_turns=10,
        tools=["read", "write"],
        reads_from=["profile", "technical"],
    )
    assert cfg.prompt == "Write a report about ${ticker}"
    assert cfg.tools == ["read", "write"]
    assert cfg.reads_from == ["profile", "technical"]


def test_claude_config_minimal():
    cfg = ClaudeConfig(prompt="Do something")
    assert cfg.system is None
    assert cfg.model is None
    assert cfg.max_turns is None
    assert cfg.tools == []
    assert cfg.reads_from == []


def test_claude_config_missing_prompt():
    with pytest.raises(Exception):
        ClaudeConfig(model="claude-sonnet-4-6")


def test_shell_config_valid():
    cfg = ShellConfig(command="pandoc input.md -o output.pdf")
    assert cfg.command == "pandoc input.md -o output.pdf"


def test_shell_config_missing_command():
    with pytest.raises(Exception):
        ShellConfig()


def test_perplexity_config_valid():
    cfg = PerplexityConfig(prompt="Research news about AAPL", model="sonar-pro")
    assert cfg.model == "sonar-pro"


def test_perplexity_config_defaults():
    cfg = PerplexityConfig(prompt="Research news")
    assert cfg.model is None
    assert cfg.reads_from == []


def test_openai_config_valid():
    cfg = OpenAIConfig(prompt="Summarize this", model="gpt-4o")
    assert cfg.model == "gpt-4o"
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/drucev/projects/sra5 && uv run pytest tests/test_schema.py -v -x`
Expected: FAIL — `ImportError`

**Step 3: Implement config models**

Add to `skills/schema.py`:

```python
class PythonConfig(BaseModel):
    """Config for type: python — runs a Python script with argparse-style args."""
    script: str
    args: dict[str, str] = {}


class ClaudeConfig(BaseModel):
    """Config for type: claude — invokes Claude Code CLI."""
    prompt: str
    system: str | None = None
    model: str | None = None
    max_turns: int | None = None
    tools: list[str] = []
    reads_from: list[str] = []


class ShellConfig(BaseModel):
    """Config for type: shell — runs a shell command."""
    command: str


class PerplexityConfig(BaseModel):
    """Config for type: perplexity — calls Perplexity API."""
    prompt: str
    model: str | None = None
    reads_from: list[str] = []


class OpenAIConfig(BaseModel):
    """Config for type: openai — calls OpenAI API."""
    prompt: str
    model: str | None = None
    reads_from: list[str] = []
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/drucev/projects/sra5 && uv run pytest tests/test_schema.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add skills/schema.py tests/test_schema.py
git commit -m "Add per-type config Pydantic models"
```

---

### Task 4: Define Task discriminated union and DagFile model

**Files:**
- Modify: `skills/schema.py`
- Modify: `tests/test_schema.py`

**Step 1: Write failing tests for Task union and DagFile**

Add to `tests/test_schema.py`:

```python
from schema import Task, DagFile


def test_task_python():
    task = Task(
        description="Get profile",
        type="python",
        config={"script": "skills/research_profile.py", "args": {"ticker": "AAPL"}},
        outputs={"profile": {"path": "artifacts/profile.json", "format": "json"}},
    )
    assert task.type == "python"
    assert isinstance(task.config, PythonConfig)


def test_task_claude():
    task = Task(
        description="Write section",
        type="claude",
        depends_on=["profile"],
        config={"prompt": "Write a report", "tools": ["read"]},
        outputs={"section": {"path": "artifacts/section.md", "format": "md"}},
    )
    assert task.type == "claude"
    assert isinstance(task.config, ClaudeConfig)
    assert task.depends_on == ["profile"]


def test_task_shell():
    task = Task(
        description="Convert to PDF",
        type="shell",
        config={"command": "pandoc in.md -o out.pdf"},
    )
    assert task.type == "shell"
    assert isinstance(task.config, ShellConfig)


def test_task_unknown_type():
    with pytest.raises(Exception):
        Task(
            description="Bad task",
            type="unknown",
            config={"script": "foo.py"},
        )


def test_task_wrong_config_for_type():
    """Python type with claude config should fail."""
    with pytest.raises(Exception):
        Task(
            description="Mismatch",
            type="python",
            config={"prompt": "This is a claude field"},
        )


def test_task_defaults():
    task = Task(
        description="Minimal",
        type="shell",
        config={"command": "echo hi"},
    )
    assert task.depends_on == []
    assert task.outputs == {}


def test_dagfile_valid():
    dag = DagFile(
        dag={"version": 2, "name": "Test"},
        tasks={
            "step1": {
                "description": "First",
                "type": "shell",
                "config": {"command": "echo hello"},
            },
            "step2": {
                "description": "Second",
                "type": "python",
                "depends_on": ["step1"],
                "config": {"script": "run.py"},
            },
        },
    )
    assert len(dag.tasks) == 2
    assert dag.tasks["step2"].depends_on == ["step1"]


def test_dagfile_version_1_rejected():
    with pytest.raises(Exception):
        DagFile(
            dag={"version": 1, "name": "Old"},
            tasks={},
        )
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/drucev/projects/sra5 && uv run pytest tests/test_schema.py -v -x`
Expected: FAIL — `ImportError: cannot import name 'Task'`

**Step 3: Implement Task and DagFile models**

Add to `skills/schema.py`, below the config models:

```python
from typing import Annotated, Union
from pydantic import Discriminator, Tag


class _TaskBase(BaseModel):
    """Common fields for all task types."""
    description: str
    depends_on: list[str] = []
    outputs: dict[str, OutputDef] = {}


class PythonTask(_TaskBase):
    type: Literal["python"]
    config: PythonConfig


class ClaudeTask(_TaskBase):
    type: Literal["claude"]
    config: ClaudeConfig


class ShellTask(_TaskBase):
    type: Literal["shell"]
    config: ShellConfig


class PerplexityTask(_TaskBase):
    type: Literal["perplexity"]
    config: PerplexityConfig


class OpenAITask(_TaskBase):
    type: Literal["openai"]
    config: OpenAIConfig


Task = Annotated[
    Union[
        Annotated[PythonTask, Tag("python")],
        Annotated[ClaudeTask, Tag("claude")],
        Annotated[ShellTask, Tag("shell")],
        Annotated[PerplexityTask, Tag("perplexity")],
        Annotated[OpenAITask, Tag("openai")],
    ],
    Discriminator("type"),
]


class DagFile(BaseModel):
    """Root model representing a complete DAG YAML file."""
    dag: DagHeader
    tasks: dict[str, Task]
```

Note: The `Task` type alias uses Pydantic v2's `Discriminator` on the `type` field. When Pydantic sees `type: "python"`, it validates against `PythonTask`; `type: "claude"` validates against `ClaudeTask`, etc. Unknown types or mismatched configs produce clear errors.

**Step 4: Run tests to verify they pass**

Run: `cd /Users/drucev/projects/sra5 && uv run pytest tests/test_schema.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add skills/schema.py tests/test_schema.py
git commit -m "Add Task discriminated union and DagFile model"
```

---

### Task 5: Add cross-reference validation (dependencies, reads_from, cycles)

**Files:**
- Modify: `skills/schema.py`
- Modify: `tests/test_schema.py`

**Step 1: Write failing tests for cross-reference validation**

Add to `tests/test_schema.py`:

```python
from schema import validate_dag


def test_validate_dag_valid():
    raw = {
        "dag": {"version": 2, "name": "Test"},
        "tasks": {
            "a": {"description": "A", "type": "shell", "config": {"command": "echo a"}},
            "b": {"description": "B", "type": "shell", "depends_on": ["a"], "config": {"command": "echo b"}},
        },
    }
    dag = validate_dag(raw)
    assert len(dag.tasks) == 2


def test_validate_dag_bad_dependency_ref():
    raw = {
        "dag": {"version": 2, "name": "Test"},
        "tasks": {
            "a": {"description": "A", "type": "shell", "depends_on": ["nonexistent"], "config": {"command": "echo a"}},
        },
    }
    with pytest.raises(ValueError, match="nonexistent"):
        validate_dag(raw)


def test_validate_dag_bad_reads_from_ref():
    raw = {
        "dag": {"version": 2, "name": "Test"},
        "tasks": {
            "a": {"description": "A", "type": "python", "config": {"script": "run.py"}},
            "b": {
                "description": "B",
                "type": "claude",
                "config": {"prompt": "do it", "reads_from": ["nonexistent"]},
            },
        },
    }
    with pytest.raises(ValueError, match="nonexistent"):
        validate_dag(raw)


def test_validate_dag_cycle():
    raw = {
        "dag": {"version": 2, "name": "Test"},
        "tasks": {
            "a": {"description": "A", "type": "shell", "depends_on": ["b"], "config": {"command": "echo a"}},
            "b": {"description": "B", "type": "shell", "depends_on": ["a"], "config": {"command": "echo b"}},
        },
    }
    with pytest.raises(ValueError, match="[Cc]ycle"):
        validate_dag(raw)


def test_validate_dag_duplicate_output_paths():
    raw = {
        "dag": {"version": 2, "name": "Test"},
        "tasks": {
            "a": {
                "description": "A",
                "type": "shell",
                "config": {"command": "echo a"},
                "outputs": {"out": {"path": "same.txt", "format": "txt"}},
            },
            "b": {
                "description": "B",
                "type": "shell",
                "config": {"command": "echo b"},
                "outputs": {"out": {"path": "same.txt", "format": "txt"}},
            },
        },
    }
    with pytest.raises(ValueError, match="same.txt"):
        validate_dag(raw)
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/drucev/projects/sra5 && uv run pytest tests/test_schema.py::test_validate_dag_valid -v -x`
Expected: FAIL — `ImportError: cannot import name 'validate_dag'`

**Step 3: Implement validate_dag function**

Add to `skills/schema.py`:

```python
from pydantic import TypeAdapter


_task_adapter = TypeAdapter(Task)


def validate_dag(raw: dict) -> DagFile:
    """Parse raw YAML dict into a validated DagFile.

    Performs:
    1. Pydantic structural validation (types, required fields)
    2. Dependency reference validation (all depends_on targets exist)
    3. reads_from reference validation (all reads_from targets exist)
    4. Cycle detection (topological sort)
    5. Output path uniqueness
    """
    dag = DagFile(**raw)
    task_ids = set(dag.tasks.keys())

    # Validate dependency references
    for task_id, task in dag.tasks.items():
        for dep in task.depends_on:
            if dep not in task_ids:
                raise ValueError(
                    f"Task '{task_id}' depends on '{dep}' which does not exist. "
                    f"Available tasks: {sorted(task_ids)}"
                )

    # Validate reads_from references
    for task_id, task in dag.tasks.items():
        reads_from = getattr(task.config, "reads_from", [])
        for ref in reads_from:
            if ref not in task_ids:
                raise ValueError(
                    f"Task '{task_id}' reads_from '{ref}' which does not exist. "
                    f"Available tasks: {sorted(task_ids)}"
                )

    # Cycle detection via topological sort (Kahn's algorithm)
    in_degree = {tid: 0 for tid in task_ids}
    for task_id, task in dag.tasks.items():
        for dep in task.depends_on:
            in_degree[task_id] += 1

    queue = [tid for tid, deg in in_degree.items() if deg == 0]
    visited = 0
    adj = {tid: [] for tid in task_ids}
    for task_id, task in dag.tasks.items():
        for dep in task.depends_on:
            adj[dep].append(task_id)

    while queue:
        node = queue.pop(0)
        visited += 1
        for neighbor in adj[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if visited != len(task_ids):
        raise ValueError(
            "Cycle detected in task dependencies. "
            "Check depends_on fields for circular references."
        )

    # Output path uniqueness
    seen_paths: dict[str, str] = {}
    for task_id, task in dag.tasks.items():
        for out_name, out_def in task.outputs.items():
            if out_def.path in seen_paths:
                raise ValueError(
                    f"Duplicate output path '{out_def.path}' in tasks "
                    f"'{seen_paths[out_def.path]}' and '{task_id}'"
                )
            seen_paths[out_def.path] = task_id

    return dag
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/drucev/projects/sra5 && uv run pytest tests/test_schema.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add skills/schema.py tests/test_schema.py
git commit -m "Add cross-reference validation for deps, reads_from, cycles, output paths"
```

---

### Task 6: Add variable substitution to schema layer

**Files:**
- Modify: `skills/schema.py`
- Modify: `tests/test_schema.py`

**Step 1: Write failing tests for variable substitution**

Add to `tests/test_schema.py`:

```python
from schema import load_dag


def test_load_dag_substitutes_variables():
    raw = {
        "dag": {"version": 2, "name": "Test", "inputs": {"ticker": "${ticker}", "workdir": "${workdir}"}},
        "tasks": {
            "profile": {
                "description": "Get profile",
                "type": "python",
                "config": {"script": "skills/run.py", "args": {"ticker": "${ticker}", "workdir": "${workdir}"}},
                "outputs": {"profile": {"path": "artifacts/profile.json", "format": "json"}},
            },
        },
    }
    variables = {"ticker": "AAPL", "workdir": "work/AAPL_20260223"}
    dag = load_dag(raw, variables)
    task = dag.tasks["profile"]
    assert task.config.args["ticker"] == "AAPL"
    assert task.config.args["workdir"] == "work/AAPL_20260223"


def test_load_dag_substitutes_in_prompt():
    raw = {
        "dag": {"version": 2, "name": "Test"},
        "tasks": {
            "write": {
                "description": "Write",
                "type": "claude",
                "config": {"prompt": "Analyze ${ticker} stock"},
            },
        },
    }
    variables = {"ticker": "MSFT"}
    dag = load_dag(raw, variables)
    assert dag.tasks["write"].config.prompt == "Analyze MSFT stock"
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/drucev/projects/sra5 && uv run pytest tests/test_schema.py::test_load_dag_substitutes_variables -v -x`
Expected: FAIL — `ImportError: cannot import name 'load_dag'`

**Step 3: Implement load_dag**

Add to `skills/schema.py`:

```python
import re


def _substitute_vars(obj, variables: dict):
    """Recursively substitute ${var} placeholders in strings."""
    if isinstance(obj, str):
        for key, value in variables.items():
            obj = obj.replace(f"${{{key}}}", str(value))
        return obj
    elif isinstance(obj, dict):
        return {k: _substitute_vars(v, variables) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_substitute_vars(item, variables) for item in obj]
    return obj


def load_dag(raw: dict, variables: dict | None = None) -> DagFile:
    """Load, substitute variables, validate, and return a DagFile.

    This is the main entry point for parsing a DAG YAML dict.

    Args:
        raw: The raw dict from yaml.safe_load()
        variables: Variable substitutions (ticker, workdir, date, etc.)

    Returns:
        Validated DagFile instance

    Raises:
        ValueError: On validation errors (bad refs, cycles, etc.)
        pydantic.ValidationError: On structural errors (missing fields, wrong types)
    """
    if variables:
        raw = _substitute_vars(raw, variables)
    return validate_dag(raw)
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/drucev/projects/sra5 && uv run pytest tests/test_schema.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add skills/schema.py tests/test_schema.py
git commit -m "Add variable substitution and load_dag entry point"
```

---

### Task 7: Migrate dags/sra.yaml to v2 format

**Files:**
- Modify: `dags/sra.yaml`

This is a manual migration of the entire YAML file. The key changes:
- `dag.version` → 2
- All tasks use `type:` (not `skill:`)
- All dependencies use `depends_on:` (not `dependencies:`)
- All task-specific config goes under `config:` (not `params:` or top-level `args:`)
- `command:` field removed
- `reads_from` uses exact task IDs (not aliases)
- Fix copy-paste bugs (write_supply_chain)

**Step 1: Rewrite dags/sra.yaml**

Replace the full contents of `dags/sra.yaml` with the v2 format. Key transformations:

- `profile`: `type: skill` + `command: profile` + `args: {...}` → `type: python` + `config: {script: ..., args: ...}`
- `technical` through `analysis`: same pattern as profile, move `params.script` and `params.args` into `config`
- `write_*` tasks: `skill: subagent:writer` + `params: {title, guidelines, reads_from}` → `type: claude` + `config: {prompt: ..., reads_from: [...]}`
- `assembly`: `skill: script:assemble` → `type: python` + `config: {script: ..., args: ...}`
- `polish`: `skill: subagent:polish` → `type: claude` + `config: {prompt: ..., reads_from: ...}`
- Fix `write_supply_chain` title/guidelines (currently copy-pasted from competitive_landscape)
- Fix `reads_from: [sec]` → `reads_from: [sec_edgar]` everywhere
- Remove all `dependencies:` keys, keep only `depends_on:`
- Remove all duplicate dependency declarations

**Step 2: Validate the migrated YAML**

Write a quick test in `tests/test_schema.py`:

```python
from pathlib import Path
import yaml


def test_sra_yaml_validates():
    """The actual project DAG file passes v2 validation."""
    yaml_path = Path(__file__).parent.parent / "dags" / "sra.yaml"
    with yaml_path.open() as f:
        raw = yaml.safe_load(f)
    dag = validate_dag(raw)
    assert dag.dag.version == 2
    assert len(dag.tasks) > 0
```

Run: `cd /Users/drucev/projects/sra5 && uv run pytest tests/test_schema.py::test_sra_yaml_validates -v`
Expected: PASS

**Step 3: Commit**

```bash
git add dags/sra.yaml tests/test_schema.py
git commit -m "Migrate dags/sra.yaml to schema v2"
```

---

### Task 8: Wire Pydantic validation into db.py init

**Files:**
- Modify: `skills/db.py` (lines 128-225, the `cmd_init` function)
- Modify: `tests/test_schema.py`

**Step 1: Write a test for db.py init with v2 YAML**

Add to `tests/test_schema.py`:

```python
import subprocess


def test_db_init_with_v2_yaml(tmp_path):
    """db.py init successfully loads the v2 YAML and populates the database."""
    workdir = tmp_path / "test_run"
    result = subprocess.run(
        [
            "uv", "run", "python", "skills/db.py", "init",
            "--workdir", str(workdir),
            "--dag", "dags/sra.yaml",
            "--ticker", "TEST",
        ],
        capture_output=True,
        text=True,
        cwd="/Users/drucev/projects/sra5",
    )
    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    import json
    output = json.loads(result.stdout)
    assert output["status"] == "ok"
    assert output["tasks"] > 0
```

**Step 2: Run test (it should pass with current db.py since YAML is valid, but we want to confirm baseline)**

Run: `cd /Users/drucev/projects/sra5 && uv run pytest tests/test_schema.py::test_db_init_with_v2_yaml -v`

**Step 3: Refactor cmd_init to use schema validation**

Modify `skills/db.py` `cmd_init` function (lines 128-225). Replace the manual parsing with:

```python
def cmd_init(args: argparse.Namespace) -> None:
    """Create db, parse DAG YAML, populate tasks + deps."""
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    db_path = workdir / 'research.db'
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)

    # Parse and validate DAG YAML
    dag_path = Path(args.dag)
    if not dag_path.exists():
        error_exit(f"DAG file not found: {dag_path}")

    with dag_path.open('r') as f:
        raw = yaml.safe_load(f)

    # Variable substitution + Pydantic validation
    variables = {
        'ticker': args.ticker,
        'date': args.date,
        'workdir': str(workdir),
    }

    try:
        from schema import load_dag
        dag = load_dag(raw, variables)
    except Exception as e:
        error_exit(f"DAG validation failed: {e}")

    # Extract dag-level metadata
    template_dir = dag.dag.template_dir

    # Insert research row
    conn.execute(
        """INSERT INTO research (ticker, date, dag_file, template_dir, workdir, status)
           VALUES (?, ?, ?, ?, ?, 'not started')""",
        (args.ticker, args.date, str(dag_path), template_dir, str(workdir))
    )

    # Process tasks from validated model
    task_count = 0
    for task_id, task in dag.tasks.items():
        params = task.config.model_dump()
        params['outputs'] = {k: v.model_dump() for k, v in task.outputs.items()}

        conn.execute(
            """INSERT INTO tasks (id, skill, description, params, concurrency)
               VALUES (?, ?, ?, ?, ?)""",
            (task_id, task.type, task.description, json.dumps(params), 'parallel')
        )
        task_count += 1

    # Process dependencies from validated model
    for task_id, task in dag.tasks.items():
        for dep in task.depends_on:
            conn.execute(
                "INSERT OR IGNORE INTO task_deps (task_id, depends_on) VALUES (?, ?)",
                (task_id, dep)
            )

    conn.commit()
    conn.close()

    print(json.dumps({
        "status": "ok",
        "tasks": task_count,
        "workdir": str(workdir)
    }))
```

Key changes:
- `from schema import load_dag` replaces all manual parsing
- No more `.get()` fallback chains
- `task.type` replaces the old `skill` field logic
- `task.config.model_dump()` produces clean JSON for the `params` column
- Dependencies come from validated `task.depends_on` — no more guessing between two field names

**Step 4: Run the test again**

Run: `cd /Users/drucev/projects/sra5 && uv run pytest tests/test_schema.py::test_db_init_with_v2_yaml -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `cd /Users/drucev/projects/sra5 && uv run pytest tests/ -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add skills/db.py
git commit -m "Wire Pydantic schema validation into db.py init"
```

---

### Task 9: Add db.py validate command

**Files:**
- Modify: `skills/db.py`
- Modify: `tests/test_schema.py`

**Step 1: Write failing test**

Add to `tests/test_schema.py`:

```python
def test_db_validate_command_valid(tmp_path):
    """db.py validate succeeds on valid v2 YAML."""
    result = subprocess.run(
        [
            "uv", "run", "python", "skills/db.py", "validate",
            "--dag", "dags/sra.yaml",
            "--ticker", "TEST",
        ],
        capture_output=True,
        text=True,
        cwd="/Users/drucev/projects/sra5",
    )
    assert result.returncode == 0
    import json
    output = json.loads(result.stdout)
    assert output["status"] == "ok"


def test_db_validate_command_invalid(tmp_path):
    """db.py validate fails on invalid YAML with clear error."""
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("""
dag:
  version: 2
  name: Bad
tasks:
  broken:
    description: Missing type
    config:
      command: echo hi
""")
    result = subprocess.run(
        [
            "uv", "run", "python", "skills/db.py", "validate",
            "--dag", str(bad_yaml),
            "--ticker", "TEST",
        ],
        capture_output=True,
        text=True,
        cwd="/Users/drucev/projects/sra5",
    )
    assert result.returncode == 1
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/drucev/projects/sra5 && uv run pytest tests/test_schema.py::test_db_validate_command_valid -v -x`
Expected: FAIL — unrecognized argument 'validate'

**Step 3: Add validate command to db.py**

Add a new function and wire it into the argparse subparsers. Add after `cmd_init`:

```python
def cmd_validate(args: argparse.Namespace) -> None:
    """Validate a DAG YAML file without touching the database."""
    dag_path = Path(args.dag)
    if not dag_path.exists():
        error_exit(f"DAG file not found: {dag_path}")

    with dag_path.open('r') as f:
        raw = yaml.safe_load(f)

    variables = {
        'ticker': args.ticker,
        'date': args.date,
        'workdir': args.workdir if hasattr(args, 'workdir') and args.workdir else '/tmp/validate',
    }

    try:
        from schema import load_dag
        dag = load_dag(raw, variables)
    except Exception as e:
        error_exit(f"Validation failed: {e}")

    print(json.dumps({
        "status": "ok",
        "version": dag.dag.version,
        "tasks": len(dag.tasks),
        "task_types": sorted(set(t.type for t in dag.tasks.values())),
    }))
```

Add the subparser in the argparse setup section (find where other subcommands are registered):

```python
p_validate = subparsers.add_parser('validate', help='Validate a DAG YAML file')
p_validate.add_argument('--dag', default='dags/sra.yaml')
p_validate.add_argument('--ticker', default='VALIDATE')
p_validate.add_argument('--date', default='20260101')
p_validate.set_defaults(func=cmd_validate)
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/drucev/projects/sra5 && uv run pytest tests/test_schema.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add skills/db.py tests/test_schema.py
git commit -m "Add db.py validate command for DAG YAML validation"
```

---

### Task 10: Update documentation

**Files:**
- Modify: `CLAUDE.md` (update Database CLI section with validate command, update schema references)

**Step 1: Update CLAUDE.md**

Add `validate` to the Database CLI section:

```bash
./skills/db.py validate --dag dags/sra.yaml --ticker SYMBOL
```

Update any references to the old YAML field names (`skill:`, `dependencies:`, `params:`) to the new ones (`type:`, `depends_on:`, `config:`).

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "Update docs for schema v2"
```

---

### Summary of deliverables

| File | Action | Purpose |
|------|--------|---------|
| `skills/schema.py` | Create | Pydantic models + validation + load_dag() |
| `tests/__init__.py` | Create | Test package |
| `tests/conftest.py` | Create | sys.path setup for skills imports |
| `tests/test_schema.py` | Create | ~25 tests covering all models + validation |
| `dags/sra.yaml` | Rewrite | Migrated to v2 format |
| `skills/db.py` | Modify | cmd_init uses schema, new cmd_validate |
| `pyproject.toml` | Modify | Add pytest dev dep |
| `CLAUDE.md` | Modify | Updated CLI docs |
