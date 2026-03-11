"""
Database command implementations for the db.py CLI.

Each function implements a single CLI subcommand. All database access goes
through get_db() and error_exit() imported from db.py. These functions are
imported lazily by db.main() to avoid circular imports.
"""

import argparse
import json
import sqlite3
from pathlib import Path

import yaml

from db import get_db, error_exit, configure_connection, SCHEMA

import logging
logger = logging.getLogger("db")


def cmd_init(args: argparse.Namespace) -> None:
    """Create db, parse DAG YAML, populate tasks + deps."""
    if args.workdir:
        workdir = Path(args.workdir)
    else:
        workdir = Path("work") / f"{args.ticker}_{args.date}"
    workdir.mkdir(parents=True, exist_ok=True)

    db_path = workdir / 'research.db'
    conn = sqlite3.connect(str(db_path))
    configure_connection(conn)
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
    drafts_dir = dag.dag.drafts_dir

    # Insert research row
    conn.execute(
        """INSERT INTO research (ticker, date, dag_file, template_dir, drafts_dir, workdir, status)
           VALUES (?, ?, ?, ?, ?, ?, 'not started')""",
        (args.ticker, args.date, str(dag_path), template_dir, drafts_dir, str(workdir))
    )

    # Process tasks from validated model
    task_count = 0
    for task_id, task in dag.tasks.items():
        params = task.config.model_dump()
        params['outputs'] = {k: v.model_dump() for k, v in task.outputs.items()}
        if task.sets_vars:
            params['sets_vars'] = {k: v.model_dump() for k, v in task.sets_vars.items()}

        conn.execute(
            """INSERT INTO tasks (id, sort_order, skill, description, params, concurrency)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (task_id, task.sort_order, task.type, task.description, json.dumps(params), 'parallel')
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
    """Return tasks that are pending with all deps satisfied.

    Failed dependencies are treated as satisfied so the pipeline keeps moving.
    Downstream tasks may produce partial results without all inputs — this is
    intentional ("auto-skip failures and continue").
    """
    conn = get_db(args.workdir)

    rows = conn.execute("""
        SELECT t.id, t.skill, t.params, t.description
        FROM tasks t
        WHERE t.status = 'pending'
        AND NOT EXISTS (
            SELECT 1 FROM task_deps d
            JOIN tasks dep ON d.depends_on = dep.id
            WHERE d.task_id = t.id
            AND dep.status NOT IN ('complete', 'skipped', 'failed')
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

    if args.summary is not None:
        updates.append("summary = ?")
        params.append(args.summary)

    if args.error is not None:
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

    # Verify task exists and get params for description fallback
    row = conn.execute(
        "SELECT id, params FROM tasks WHERE id = ?", (args.task,)
    ).fetchone()
    if not row:
        conn.close()
        error_exit(f"Task not found: {args.task}")

    # Fall back to YAML output description if none provided
    description = args.description
    if description is None:
        try:
            params = json.loads(row["params"])
            output_def = params.get("outputs", {}).get(args.name, {})
            description = output_def.get("description") or None
        except (json.JSONDecodeError, AttributeError):
            pass

    # Verify file exists and is non-empty
    full_path = Path(args.workdir) / args.path
    if not full_path.exists():
        conn.close()
        error_exit(f"Artifact file not found: {full_path}")
    size_bytes = full_path.stat().st_size
    if size_bytes == 0:
        conn.close()
        error_exit(f"Artifact file is empty: {full_path}")

    # Check for duplicate (same task + name) — update if exists
    existing = conn.execute(
        "SELECT id FROM artifacts WHERE task_id = ? AND name = ?",
        (args.task, args.name)
    ).fetchone()

    if existing:
        conn.execute(
            """UPDATE artifacts SET path = ?, format = ?, description = ?, source = ?,
               summary = ?, size_bytes = ?, created_at = datetime('now')
               WHERE id = ?""",
            (args.path, args.format, description, args.source, args.summary, size_bytes, existing["id"])
        )
        artifact_id = existing["id"]
    else:
        cursor = conn.execute(
            """INSERT INTO artifacts (task_id, name, path, format, description, source, summary, size_bytes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (args.task, args.name, args.path, args.format, description, args.source, args.summary, size_bytes)
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
            """SELECT id, task_id, name, path, format, description, source, summary, size_bytes
               FROM artifacts WHERE task_id = ? ORDER BY id""",
            (args.task,)
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, task_id, name, path, format, description, source, summary, size_bytes
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
            "description": row["description"],
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
        FROM tasks t ORDER BY t.sort_order, t.id
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



def cmd_var_set(args: argparse.Namespace) -> None:
    """Set a runtime DAG variable."""
    conn = get_db(args.workdir)

    conn.execute(
        """INSERT INTO dag_vars (name, value, source_task)
           VALUES (?, ?, ?)
           ON CONFLICT(name) DO UPDATE SET
               value = excluded.value,
               source_task = excluded.source_task,
               created_at = datetime('now')""",
        (args.name, args.value, args.source_task)
    )
    conn.commit()
    conn.close()

    print(json.dumps({
        "status": "ok",
        "name": args.name,
        "value": args.value,
    }))


def cmd_var_get(args: argparse.Namespace) -> None:
    """Get one or all runtime DAG variables."""
    conn = get_db(args.workdir)

    if args.name:
        row = conn.execute(
            "SELECT name, value, source_task FROM dag_vars WHERE name = ?",
            (args.name,)
        ).fetchone()
        conn.close()

        if not row:
            error_exit(f"Variable not found: {args.name}")

        print(json.dumps({
            "name": row["name"],
            "value": row["value"],
            "source_task": row["source_task"],
        }))
    else:
        rows = conn.execute(
            "SELECT name, value, source_task FROM dag_vars ORDER BY name"
        ).fetchall()
        conn.close()

        result = {}
        for row in rows:
            result[row["name"]] = row["value"]

        print(json.dumps(result))


def cmd_finding_add(args: argparse.Namespace) -> None:
    """Add a research finding tagged with section relevance."""
    import uuid
    conn = get_db(args.workdir)

    row = conn.execute("SELECT id FROM tasks WHERE id = ?", (args.task_id,)).fetchone()
    if not row:
        conn.close()
        error_exit(f"Task not found: {args.task_id}")

    finding_id = str(uuid.uuid4())
    tags = json.dumps(args.tags or [])
    conn.execute(
        """INSERT INTO research_findings (id, task_id, content, source, tags)
           VALUES (?, ?, ?, ?, ?)""",
        (finding_id, args.task_id, args.content, args.source, tags)
    )
    conn.commit()
    conn.close()
    print(json.dumps({"status": "ok", "id": finding_id}))


def cmd_finding_list(args: argparse.Namespace) -> None:
    """List research findings, optionally filtered by tags."""
    conn = get_db(args.workdir)

    rows = conn.execute(
        "SELECT id, task_id, content, source, tags, created_at FROM research_findings ORDER BY created_at"
    ).fetchall()

    result = []
    for row in rows:
        tags = json.loads(row["tags"])
        if args.tags:
            if not any(t in tags for t in args.tags):
                continue
        result.append({
            "id": row["id"],
            "task_id": row["task_id"],
            "content": row["content"],
            "source": row["source"],
            "tags": tags,
            "created_at": row["created_at"],
        })

    conn.close()
    print(json.dumps(result, indent=2))


def cmd_task_context(args: argparse.Namespace) -> None:
    """Resolve dependency artifacts for a task."""
    conn = get_db(args.workdir)

    # Verify task exists
    row = conn.execute(
        "SELECT id FROM tasks WHERE id = ?", (args.task_id,)
    ).fetchone()
    if not row:
        conn.close()
        error_exit(f"Task not found: {args.task_id}")

    # Get dependencies
    deps = conn.execute(
        "SELECT depends_on FROM task_deps WHERE task_id = ?", (args.task_id,)
    ).fetchall()
    dep_ids = [d["depends_on"] for d in deps]

    # Get artifacts from dependency tasks
    artifacts = []
    if dep_ids:
        placeholders = ",".join("?" * len(dep_ids))
        rows = conn.execute(
            f"""SELECT task_id, name, path, format, description, summary
                FROM artifacts WHERE task_id IN ({placeholders})
                ORDER BY task_id, name""",
            dep_ids
        ).fetchall()
        for r in rows:
            artifacts.append({
                "from_task": r["task_id"],
                "name": r["name"],
                "path": r["path"],
                "format": r["format"],
                "description": r["description"],
                "summary": r["summary"],
            })

    conn.close()
    print(json.dumps({
        "task_id": args.task_id,
        "depends_on": dep_ids,
        "artifacts": artifacts,
    }, indent=2))


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
        'workdir': getattr(args, 'workdir', None) or '/tmp/validate',
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
