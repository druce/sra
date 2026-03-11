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
    task-context    Resolve dependency artifacts for a task
    artifact-add    Register an artifact
    artifact-list   List artifacts as JSON
    status          Overview: research status, all tasks, artifact counts
    research-update Update research.status field
    var-set         Set a runtime DAG variable
    var-get         Get one or all runtime DAG variables

Command implementations live in db_commands.py. This module provides the
schema, helper functions, and CLI argument parser.
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Union

_SKILLS_DIR = Path(__file__).resolve().parent
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

from utils import setup_logging  # noqa: E402

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
    drafts_dir    TEXT NOT NULL DEFAULT 'drafts',
    workdir       TEXT NOT NULL,
    status        TEXT DEFAULT 'not started',
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT PRIMARY KEY,
    sort_order    INTEGER DEFAULT 0,
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
    description   TEXT,
    source        TEXT,
    summary       TEXT,
    size_bytes    INTEGER,
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS dag_vars (
    name          TEXT PRIMARY KEY,
    value         TEXT NOT NULL,
    source_task   TEXT REFERENCES tasks(id),
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS research_findings (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL REFERENCES tasks(id),
    content     TEXT NOT NULL,
    source      TEXT,
    tags        TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


# ============================================================================
# Helpers
# ============================================================================

def configure_connection(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Apply standard connection settings (WAL mode, busy timeout, foreign keys)."""
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_db(workdir: Union[str, Path]) -> sqlite3.Connection:
    """Open the SQLite database in the given workdir."""
    db_path = Path(workdir) / 'research.db'
    if not db_path.exists():
        error_exit("research.db not found — run 'init' first")
    conn = sqlite3.connect(str(db_path))
    return configure_connection(conn)


def error_exit(message: str) -> None:
    """Print error to stderr and exit with code 1."""
    print(json.dumps({"status": "error", "error": message}), file=sys.stdout)
    sys.exit(1)


# ============================================================================
# CLI
# ============================================================================

def main() -> int:
    # Lazy import to avoid circular dependency (db_commands imports from db)
    from db_commands import (
        cmd_init, cmd_task_ready, cmd_task_get, cmd_task_update,
        cmd_task_context, cmd_artifact_add, cmd_artifact_list,
        cmd_status, cmd_research_update, cmd_var_set, cmd_var_get,
        cmd_finding_add, cmd_finding_list, cmd_validate,
    )

    parser = argparse.ArgumentParser(
        description='SQLite CLI utility for DAG pipeline state management'
    )
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # init
    p_init = subparsers.add_parser('init', help='Initialize database from DAG YAML')
    p_init.add_argument('--workdir', help='Work directory path (default: work/{TICKER}_{DATE})')
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
    p_get.add_argument('--task-id', required=True, dest='task_id', help='Task ID')

    # task-update
    p_update = subparsers.add_parser('task-update', help='Update task status')
    p_update.add_argument('--workdir', required=True)
    p_update.add_argument('--task-id', required=True, dest='task_id', help='Task ID')
    p_update.add_argument('--status', choices=['pending', 'running', 'complete', 'failed', 'skipped'])
    p_update.add_argument('--summary', help='Brief result summary')
    p_update.add_argument('--error', help='Error message')

    # task-context
    p_ctx = subparsers.add_parser('task-context', help='Resolve dependency artifacts for a task')
    p_ctx.add_argument('--workdir', required=True)
    p_ctx.add_argument('--task-id', required=True, dest='task_id', help='Task ID')

    # artifact-add
    p_add = subparsers.add_parser('artifact-add', help='Register an artifact')
    p_add.add_argument('--workdir', required=True)
    p_add.add_argument('--task-id', required=True, dest='task', help='Task ID')
    p_add.add_argument('--name', required=True, help='Artifact name')
    p_add.add_argument('--path', required=True, help='Path relative to workdir')
    p_add.add_argument('--format', required=True, help='File format (json|csv|md|png|txt)')
    p_add.add_argument('--description', default=None, help='Static description of artifact content')
    p_add.add_argument('--source', default=None, help='Data source')
    p_add.add_argument('--summary', default=None, help='Brief runtime result summary')

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

    # var-set
    p_vset = subparsers.add_parser('var-set', help='Set a runtime DAG variable')
    p_vset.add_argument('--workdir', required=True)
    p_vset.add_argument('--name', required=True, help='Variable name')
    p_vset.add_argument('--value', required=True, help='Variable value')
    p_vset.add_argument('--source-task', default=None, dest='source_task',
                        help='Task that produced this variable')

    # var-get
    p_vget = subparsers.add_parser('var-get', help='Get runtime DAG variables')
    p_vget.add_argument('--workdir', required=True)
    p_vget.add_argument('--name', default=None, help='Variable name (omit for all)')

    # finding-add
    p_fadd = subparsers.add_parser('finding-add', help='Add a research finding')
    p_fadd.add_argument('--workdir', required=True)
    p_fadd.add_argument('--task-id', required=True, dest='task_id')
    p_fadd.add_argument('--content', required=True)
    p_fadd.add_argument('--source', default=None)
    p_fadd.add_argument('--tags', nargs='*', default=[])

    # finding-list
    p_flist = subparsers.add_parser('finding-list', help='List research findings')
    p_flist.add_argument('--workdir', required=True)
    p_flist.add_argument('--tags', nargs='*', default=None)

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
        'task-context': cmd_task_context,
        'artifact-add': cmd_artifact_add,
        'artifact-list': cmd_artifact_list,
        'status': cmd_status,
        'research-update': cmd_research_update,
        'var-set': cmd_var_set,
        'var-get': cmd_var_get,
        'finding-add': cmd_finding_add,
        'finding-list': cmd_finding_list,
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
