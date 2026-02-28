"""DAG Schema v2 — Pydantic models for YAML validation.

Defines typed models for each task execution environment (python, claude,
shell). YAML is loaded and validated through these models at db.py init time.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Discriminator, Tag


class OutputDef(BaseModel):
    """A named artifact produced by a task."""
    path: str
    format: str
    description: str = ""


class SetsVarDef(BaseModel):
    """Defines how to extract a runtime variable from a task's output artifact."""
    artifact: str  # path relative to workdir, e.g. "artifacts/profile.json"
    key: str       # top-level JSON key to extract


class DagHeader(BaseModel):
    """Top-level DAG metadata."""
    version: Literal[2]
    name: str
    inputs: dict[str, str] = {}
    root_dir: str = "."
    template_dir: str = "templates"


class PythonConfig(BaseModel):
    """Config for type: python — runs a Python script with argparse-style args."""
    script: str
    args: dict[str, str] = {}


class ClaudeConfig(BaseModel):
    """Config for type: claude — invokes Claude Code CLI.

    Fields map to ``claude -p`` flags:
      prompt             → the prompt text
      system             → --system-prompt
      append_system      → --append-system-prompt
      model              → --model
      fallback_model     → --fallback-model
      tools              → --tools ("all", [], or explicit list)
      allowed_tools      → --allowed-tools  (permission-scoped, e.g. "Bash(git:*)")
      disallowed_tools   → --disallowed-tools
      permission_mode    → --permission-mode
      skip_permissions   → --dangerously-skip-permissions
      max_budget_usd     → --max-budget-usd
      output_format      → --output-format
      json_schema        → --json-schema
      effort             → --effort
      add_dirs           → --add-dir
      mcp_config         → --mcp-config
    """
    prompt: str
    system: str | None = None
    append_system: str | None = None
    model: str | None = None
    fallback_model: str | None = None
    tools: list[str] | str = []
    allowed_tools: list[str] = []
    disallowed_tools: list[str] = []
    permission_mode: Literal[
        "default", "plan", "bypassPermissions", "acceptEdits", "dontAsk"
    ] | None = None
    skip_permissions: bool = False
    max_budget_usd: float | None = None
    output_format: Literal["text", "json", "stream-json"] | None = None
    json_schema: dict | str | None = None
    effort: Literal["low", "medium", "high"] | None = None
    add_dirs: list[str] = []
    mcp_config: list[str] = []

    # Critic-optimizer loop
    critic_prompt: str | None = None
    rewrite_prompt: str | None = None
    n_iterations: int = 0
    critic_model: str | None = None
    rewrite_model: str | None = None
    critic_disallowed_tools: list[str] = []
    rewrite_disallowed_tools: list[str] = []


class ShellConfig(BaseModel):
    """Config for type: shell — runs a shell command."""
    command: str


# ---------------------------------------------------------------------------
# Task models (one per execution type) and discriminated union
# ---------------------------------------------------------------------------

class _TaskBase(BaseModel):
    """Common fields for all task types."""
    description: str
    depends_on: list[str] = []
    outputs: dict[str, OutputDef] = {}
    sets_vars: dict[str, SetsVarDef] = {}


class PythonTask(_TaskBase):
    type: Literal["python"]
    config: PythonConfig


class ClaudeTask(_TaskBase):
    type: Literal["claude"]
    config: ClaudeConfig


class ShellTask(_TaskBase):
    type: Literal["shell"]
    config: ShellConfig


Task = Annotated[
    Union[
        Annotated[PythonTask, Tag("python")],
        Annotated[ClaudeTask, Tag("claude")],
        Annotated[ShellTask, Tag("shell")],
    ],
    Discriminator("type"),
]


# ---------------------------------------------------------------------------
# Root DAG file model
# ---------------------------------------------------------------------------

class DagFile(BaseModel):
    """Root model representing a complete DAG YAML file."""
    dag: DagHeader
    tasks: dict[str, Task]


# ---------------------------------------------------------------------------
# Cross-reference validation
# ---------------------------------------------------------------------------

def validate_dag(raw: dict) -> DagFile:
    """Parse raw YAML dict into a validated DagFile.

    Performs:
    1. Pydantic structural validation (types, required fields)
    2. Dependency reference validation (all depends_on targets exist)
    3. Cycle detection (topological sort)
    4. Output path uniqueness
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

    # Cycle detection via topological sort (Kahn's algorithm)
    in_degree = {tid: 0 for tid in task_ids}
    for task_id, task in dag.tasks.items():
        for dep in task.depends_on:
            in_degree[task_id] += 1

    queue = [tid for tid, deg in in_degree.items() if deg == 0]
    visited = 0
    adj: dict[str, list[str]] = {tid: [] for tid in task_ids}
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

    # Output path uniqueness (skip directory paths — multiple outputs can share a dir)
    seen_paths: dict[str, str] = {}
    for task_id, task in dag.tasks.items():
        for out_name, out_def in task.outputs.items():
            if out_def.path.endswith("/"):
                continue
            if out_def.path in seen_paths:
                raise ValueError(
                    f"Duplicate output path '{out_def.path}' in tasks "
                    f"'{seen_paths[out_def.path]}' and '{task_id}'"
                )
            seen_paths[out_def.path] = task_id

    # Validate critic-optimizer config consistency
    for task_id, task in dag.tasks.items():
        if not hasattr(task.config, 'n_iterations'):
            continue
        n = task.config.n_iterations
        if n > 0:
            if not task.config.critic_prompt:
                raise ValueError(
                    f"Task '{task_id}' has n_iterations={n} but no critic_prompt. "
                    f"Both critic_prompt and rewrite_prompt are required when n_iterations > 0."
                )
            if not task.config.rewrite_prompt:
                raise ValueError(
                    f"Task '{task_id}' has n_iterations={n} but no rewrite_prompt. "
                    f"Both critic_prompt and rewrite_prompt are required when n_iterations > 0."
                )

    return dag


# ---------------------------------------------------------------------------
# Variable substitution and entry point
# ---------------------------------------------------------------------------

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
