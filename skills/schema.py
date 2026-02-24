"""DAG Schema v2 — Pydantic models for YAML validation.

Defines typed models for each task execution environment (python, claude,
shell, perplexity, openai). YAML is loaded and validated through these
models at db.py init time.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Discriminator, Tag


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


# ---------------------------------------------------------------------------
# Task models (one per execution type) and discriminated union
# ---------------------------------------------------------------------------

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
