#!/usr/bin/env python3
"""
Database Utility — SQLite CLI for DAG pipeline state management.

Shared state layer for the entire pipeline. Every task reads and writes
through db.py — no direct SQLite access elsewhere.

Usage:
    ./skills/db.py <command> --workdir <path> [command-specific args]

Commands:
    init            Create db, parse DAG YAML, populate tasks + deps
    validate        Validate a DAG YAML file without touching the database
    task-ready      JSON array of dispatchable tasks
    task-get        Full task config as JSON
    task-update     Update task state
    artifact-add    Register an artifact
    artifact-list   List artifacts as JSON
    status          Overview: research status, all tasks, artifact counts
    research-update Update research.status field
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import setup_logging

logger = setup_logging(__name__)


# ============================================================================
# Schema
# ============================================================================

SCHEMA = """
CREATE TABLE IF NOT EXISTS research (
    id            INTEGER PRIMARY KEY,
    ticker        TEXT NOT NULL,
    date          TEXT NOT NULL,
    dag_file      TEXT NOT NULL,
    template_dir  TEXT NOT NULL,
    workdir       TEXT NOT NULL,
    status        TEXT DEFAULT 'not started',
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT PRIMARY KEY,
    skill         TEXT NOT NULL,
    description   TEXT,
    params        TEXT NOT NULL,
    concurrency   TEXT DEFAULT 'parallel',
    status        TEXT DEFAULT 'pending',
    started_at    TEXT,
    completed_at  TEXT,
    error         TEXT,
    summary       TEXT
);

CREATE TABLE IF NOT EXISTS task_deps (
    task_id       TEXT NOT NULL REFERENCES tasks(id),
    depends_on    TEXT NOT NULL REFERENCES tasks(id),
    PRIMARY KEY (task_id, depends_on)
);

CREATE TABLE IF NOT EXISTS artifacts (
    id            INTEGER PRIMARY KEY,
    task_id       TEXT NOT NULL REFERENCES tasks(id),
    name          TEXT NOT NULL,
    path          TEXT NOT NULL,
    format        TEXT NOT NULL,
    source        TEXT,
    summary       TEXT,
    size_bytes    INTEGER,
    created_at    TEXT DEFAULT (datetime('now'))
);
"""


# ============================================================================
# Helpers
# ============================================================================

def get_db(workdir: str) -> sqlite3.Connection:
    """Open the SQLite database in the given workdir."""
    db_path = Path(workdir) / 'research.db'
    if not db_path.exists():
        error_exit("research.db not found — run 'init' first")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def error_exit(message: str) -> None:
    """Print error to stderr and exit with code 1."""
    print(json.dumps({"status": "error", "error": message}), file=sys.stdout)
    sys.exit(1)


def substitute_vars(obj, variables: dict):
    """Recursively substitute ${var} placeholders in strings."""
    if isinstance(obj, str):
        for key, value in variables.items():
            obj = obj.replace(f"${{{key}}}", str(value))
        return obj
    elif isinstance(obj, dict):
        return {k: substitute_vars(v, variables) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [substitute_vars(item, variables) for item in obj]
    return obj


# ============================================================================
# Commands
# ============================================================================

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


def cmd_task_ready(args: argparse.Namespace) -> None:
    """Return tasks that are pending with all deps satisfied."""
    conn = get_db(args.workdir)

    rows = conn.execute("""
        SELECT t.id, t.skill, t.params, t.description
        FROM tasks t
        WHERE t.status = 'pending'
        AND NOT EXISTS (
            SELECT 1 FROM task_deps d
            JOIN tasks dep ON d.depends_on = dep.id
            WHERE d.task_id = t.id
            AND dep.status NOT IN ('complete', 'skipped')
        )
    """).fetchall()

    result = []
    for row in rows:
        result.append({
            "id": row["id"],
            "skill": row["skill"],
            "description": row["description"],
            "params": json.loads(row["params"]),
        })

    conn.close()
    print(json.dumps(result, indent=2))


def cmd_task_get(args: argparse.Namespace) -> None:
    """Return full task info as JSON."""
    conn = get_db(args.workdir)

    row = conn.execute(
        "SELECT * FROM tasks WHERE id = ?", (args.task_id,)
    ).fetchone()

    if not row:
        conn.close()
        error_exit(f"Task not found: {args.task_id}")

    # Get dependencies
    deps = conn.execute(
        "SELECT depends_on FROM task_deps WHERE task_id = ?", (args.task_id,)
    ).fetchall()

    # Get artifact count
    artifact_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM artifacts WHERE task_id = ?", (args.task_id,)
    ).fetchone()["cnt"]

    result = {
        "id": row["id"],
        "skill": row["skill"],
        "description": row["description"],
        "params": json.loads(row["params"]),
        "status": row["status"],
        "depends_on": [d["depends_on"] for d in deps],
        "artifact_count": artifact_count,
        "summary": row["summary"],
        "error": row["error"],
    }

    conn.close()
    print(json.dumps(result, indent=2))


def cmd_task_update(args: argparse.Namespace) -> None:
    """Update task status and optional fields."""
    conn = get_db(args.workdir)

    # Verify task exists
    row = conn.execute(
        "SELECT id, status FROM tasks WHERE id = ?", (args.task_id,)
    ).fetchone()
    if not row:
        conn.close()
        error_exit(f"Task not found: {args.task_id}")

    updates = []
    params = []

    if args.status:
        updates.append("status = ?")
        params.append(args.status)

        # Set timestamps based on status
        if args.status == 'running':
            updates.append("started_at = datetime('now')")
        elif args.status in ('complete', 'failed', 'skipped'):
            updates.append("completed_at = datetime('now')")

    if args.summary:
        updates.append("summary = ?")
        params.append(args.summary)

    if args.error:
        updates.append("error = ?")
        params.append(args.error)

    if not updates:
        conn.close()
        error_exit("No updates specified")

    params.append(args.task_id)
    conn.execute(
        f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?",
        params
    )

    # Update research.updated_at
    conn.execute("UPDATE research SET updated_at = datetime('now')")

    conn.commit()
    conn.close()

    print(json.dumps({
        "status": "ok",
        "task": args.task_id,
        "new_status": args.status or row["status"]
    }))


def cmd_artifact_add(args: argparse.Namespace) -> None:
    """Register an artifact."""
    conn = get_db(args.workdir)

    # Verify task exists
    row = conn.execute(
        "SELECT id FROM tasks WHERE id = ?", (args.task,)
    ).fetchone()
    if not row:
        conn.close()
        error_exit(f"Task not found: {args.task}")

    # Compute size_bytes if file exists
    size_bytes = None
    full_path = Path(args.workdir) / args.path
    if full_path.exists():
        size_bytes = full_path.stat().st_size
    else:
        logger.warning("Artifact file not found: %s", full_path)

    # Check for duplicate (same task + name) — update if exists
    existing = conn.execute(
        "SELECT id FROM artifacts WHERE task_id = ? AND name = ?",
        (args.task, args.name)
    ).fetchone()

    if existing:
        conn.execute(
            """UPDATE artifacts SET path = ?, format = ?, source = ?,
               summary = ?, size_bytes = ?, created_at = datetime('now')
               WHERE id = ?""",
            (args.path, args.format, args.source, args.summary, size_bytes, existing["id"])
        )
        artifact_id = existing["id"]
    else:
        cursor = conn.execute(
            """INSERT INTO artifacts (task_id, name, path, format, source, summary, size_bytes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (args.task, args.name, args.path, args.format, args.source, args.summary, size_bytes)
        )
        artifact_id = cursor.lastrowid

    conn.commit()
    conn.close()

    print(json.dumps({
        "status": "ok",
        "artifact_id": artifact_id,
        "task": args.task,
        "name": args.name
    }))


def cmd_artifact_list(args: argparse.Namespace) -> None:
    """List artifacts as JSON."""
    conn = get_db(args.workdir)

    if args.task:
        rows = conn.execute(
            """SELECT id, task_id, name, path, format, source, summary, size_bytes
               FROM artifacts WHERE task_id = ? ORDER BY id""",
            (args.task,)
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, task_id, name, path, format, source, summary, size_bytes
               FROM artifacts ORDER BY id"""
        ).fetchall()

    result = []
    for row in rows:
        result.append({
            "id": row["id"],
            "task_id": row["task_id"],
            "name": row["name"],
            "path": row["path"],
            "format": row["format"],
            "source": row["source"],
            "summary": row["summary"],
            "size_bytes": row["size_bytes"],
        })

    conn.close()
    print(json.dumps(result, indent=2))


def cmd_status(args: argparse.Namespace) -> None:
    """Overview JSON."""
    conn = get_db(args.workdir)

    # Research info
    research = conn.execute("SELECT * FROM research LIMIT 1").fetchone()

    # Task counts
    counts = {}
    for status in ('pending', 'running', 'complete', 'failed', 'skipped'):
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM tasks WHERE status = ?", (status,)
        ).fetchone()
        counts[status] = row["cnt"]

    total = conn.execute("SELECT COUNT(*) as cnt FROM tasks").fetchone()["cnt"]

    # Task details
    task_rows = conn.execute("""
        SELECT t.id, t.status, t.summary, t.error,
               (SELECT COUNT(*) FROM artifacts a WHERE a.task_id = t.id) as artifact_count
        FROM tasks t ORDER BY t.id
    """).fetchall()

    task_details = []
    for row in task_rows:
        detail = {
            "id": row["id"],
            "status": row["status"],
            "artifact_count": row["artifact_count"],
        }
        if row["summary"]:
            detail["summary"] = row["summary"]
        if row["error"]:
            detail["error"] = row["error"]
        task_details.append(detail)

    # Artifact count
    artifact_total = conn.execute(
        "SELECT COUNT(*) as cnt FROM artifacts"
    ).fetchone()["cnt"]

    result = {
        "research": {
            "ticker": research["ticker"],
            "status": research["status"],
            "created_at": research["created_at"],
        },
        "tasks": {
            "total": total,
            **counts,
        },
        "task_details": task_details,
        "artifacts": {"total": artifact_total},
    }

    conn.close()
    print(json.dumps(result, indent=2))


def cmd_research_update(args: argparse.Namespace) -> None:
    """Update research.status field."""
    conn = get_db(args.workdir)

    conn.execute(
        "UPDATE research SET status = ?, updated_at = datetime('now')",
        (args.status,)
    )
    conn.commit()
    conn.close()

    print(json.dumps({
        "status": "ok",
        "new_status": args.status
    }))


def cmd_validate(args: argparse.Namespace) -> None:
    """Validate a DAG YAML file without touching the database."""
    dag_path = Path(args.dag)
    if not dag_path.exists():
        error_exit(f"DAG file not found: {dag_path}")

    with dag_path.open('r') as f:
        raw = yaml.safe_load(f)

    variables = {
        'ticker': args.ticker,
        'date': getattr(args, 'date', '20260101'),
        'workdir': getattr(args, 'workdir', '/tmp/validate') if hasattr(args, 'workdir') and args.workdir else '/tmp/validate',
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


# ============================================================================
# CLI
# ============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description='SQLite CLI utility for DAG pipeline state management'
    )
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # init
    p_init = subparsers.add_parser('init', help='Initialize database from DAG YAML')
    p_init.add_argument('--workdir', required=True, help='Work directory path')
    p_init.add_argument('--dag', required=True, help='DAG YAML file path')
    p_init.add_argument('--ticker', required=True, help='Stock ticker symbol')
    p_init.add_argument('--date', default=datetime.now().strftime('%Y%m%d'),
                        help='Date string (YYYYMMDD, defaults to today)')

    # task-ready
    p_ready = subparsers.add_parser('task-ready', help='List ready tasks')
    p_ready.add_argument('--workdir', required=True)

    # task-get
    p_get = subparsers.add_parser('task-get', help='Get task details')
    p_get.add_argument('--workdir', required=True)
    p_get.add_argument('task_id', help='Task ID')

    # task-update
    p_update = subparsers.add_parser('task-update', help='Update task status')
    p_update.add_argument('--workdir', required=True)
    p_update.add_argument('task_id', help='Task ID')
    p_update.add_argument('--status', choices=['pending', 'running', 'complete', 'failed', 'skipped'])
    p_update.add_argument('--summary', help='Brief result summary')
    p_update.add_argument('--error', help='Error message')

    # artifact-add
    p_add = subparsers.add_parser('artifact-add', help='Register an artifact')
    p_add.add_argument('--workdir', required=True)
    p_add.add_argument('--task', required=True, help='Task ID')
    p_add.add_argument('--name', required=True, help='Artifact name')
    p_add.add_argument('--path', required=True, help='Path relative to workdir')
    p_add.add_argument('--format', required=True, help='File format (json|csv|md|png|txt)')
    p_add.add_argument('--source', default=None, help='Data source')
    p_add.add_argument('--summary', default=None, help='Brief description')

    # artifact-list
    p_list = subparsers.add_parser('artifact-list', help='List artifacts')
    p_list.add_argument('--workdir', required=True)
    p_list.add_argument('--task', default=None, help='Filter by task ID')

    # status
    p_status = subparsers.add_parser('status', help='Overview status')
    p_status.add_argument('--workdir', required=True)

    # research-update
    p_rupdate = subparsers.add_parser('research-update', help='Update research status')
    p_rupdate.add_argument('--workdir', required=True)
    p_rupdate.add_argument('--status', required=True,
                           choices=['not started', 'running', 'complete', 'failed'])

    # validate
    p_validate = subparsers.add_parser('validate', help='Validate a DAG YAML file')
    p_validate.add_argument('--dag', default='dags/sra.yaml')
    p_validate.add_argument('--ticker', default='VALIDATE')
    p_validate.add_argument('--date', default='20260101')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    commands = {
        'init': cmd_init,
        'task-ready': cmd_task_ready,
        'task-get': cmd_task_get,
        'task-update': cmd_task_update,
        'artifact-add': cmd_artifact_add,
        'artifact-list': cmd_artifact_list,
        'status': cmd_status,
        'research-update': cmd_research_update,
        'validate': cmd_validate,
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args)
    else:
        error_exit(f"Unknown command: {args.command}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
