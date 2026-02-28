#!/usr/bin/env python3
"""
research.py — Async DAG orchestrator for equity research pipeline.

Reads DAG YAML, initializes SQLite via db.py, runs waves of tasks as
async subprocesses. Python tasks via `uv run python`, Claude tasks via
`claude --dangerously-skip-permissions -p`.

Usage:
    ./research.py TICKER [--dag dags/sra.yaml] [--date YYYYMMDD]
"""

import argparse
import asyncio
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

DB_PY = Path(__file__).parent / "skills" / "db.py"


def log(msg: str) -> None:
    """Print timestamped message to stderr."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr)


async def run_db(*args: str) -> dict:
    """Call db.py with args, return parsed JSON stdout."""
    cmd = ["uv", "run", "python", str(DB_PY)] + list(args)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode().strip() or stdout.decode().strip()
        raise RuntimeError(
            f"db.py {args[0]} failed (rc={proc.returncode}): {err}")
    return json.loads(stdout.decode())


async def write_manifest(workdir: Path) -> None:
    """Query all artifacts from DB, write manifest.json for existing files only."""
    artifacts = await run_db("artifact-list", "--workdir", str(workdir))
    manifest = []
    for a in artifacts:
        file_path = workdir / a["path"]
        if file_path.exists():
            manifest.append({
                "description": a.get("description") or "",
                "format": a.get("format", ""),
                "summary": a.get("summary"),
                "file": a["path"],
            })
    manifest_path = workdir / "artifacts" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2))
    log(f"Manifest updated: {len(manifest)} artifacts")


async def run_python_task(task: dict, workdir: Path, ticker: str) -> dict:
    """Run a python task as subprocess. Return result dict."""
    params = task["params"]
    script = params["script"]
    args_dict = params.get("args", {})

    # Build command: uv run python {script} {ticker} --key value ...
    cmd = ["uv", "run", "python", script]

    # ticker is positional if present in args
    if "ticker" in args_dict:
        cmd.append(args_dict["ticker"])

    # Remaining args as --key value (underscore → hyphen)
    for key, val in args_dict.items():
        if key == "ticker":
            continue
        flag = f"--{key.replace('_', '-')}"
        cmd.append(flag)
        # Split space-separated values into multiple args (e.g. --file a=x b=y c=z)
        parts = str(val).split()
        cmd.extend(parts)

    stream_log = workdir / f"{task['id']}_stream.log"

    log(f"  [{task['id']}] Running: {' '.join(cmd)}")
    log(f"  [{task['id']}] Streaming to: {stream_log}")

    with open(stream_log, "w") as stream_f:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=stream_f,
        )
        stdout_bytes, _ = await proc.communicate()

    stdout = stdout_bytes.decode().strip()

    # Append stdout (JSON manifest) to stream log
    with open(stream_log, "a") as stream_f:
        stream_f.write(f"\n--- stdout (JSON manifest) ---\n{stdout}\n")

    # Parse JSON manifest from stdout
    try:
        manifest = json.loads(stdout)
    except json.JSONDecodeError:
        return {
            "task_id": task["id"],
            "status": "failed",
            "error": f"Invalid JSON stdout (rc={proc.returncode}): {stdout[:200]}",
            "artifacts": [],
            "manifest": None,
        }

    if proc.returncode >= 2 or manifest.get("status") == "failed":
        return {
            "task_id": task["id"],
            "status": "failed",
            "error": manifest.get("error") or f"Exit code {proc.returncode}",
            "artifacts": manifest.get("artifacts", []),
            "manifest": manifest,
        }

    return {
        "task_id": task["id"],
        "status": "complete",
        "error": None,
        "artifacts": manifest.get("artifacts", []),
        "manifest": manifest,
    }


async def _invoke_claude(
    prompt: str,
    workdir: Path,
    task_id: str,
    step_label: str,
    disallowed_tools: list[str] | None = None,
    system: str | None = None,
    model: str | None = None,
    max_budget_usd: float | None = None,
    expected_outputs: dict[str, dict] | None = None,
) -> dict:
    """Invoke claude CLI with a prompt. Return result dict with status, error, artifacts."""
    abs_workdir = str(workdir.resolve())
    outputs = expected_outputs or {}

    # Build prompt
    parts = []
    if system:
        parts.append(system)
        parts.append("")

    parts.append("All research data is in the artifacts/ subdirectory.")
    parts.append(
        "Read artifacts/manifest.json for a description of all available files.")
    parts.append("")
    parts.append("---")
    parts.append("")
    parts.append(prompt)

    # Add save instructions for each output
    for out_name, out_def in outputs.items():
        out_path = out_def["path"]
        if out_path not in prompt:
            parts.append("")
            parts.append(f'Save your output for "{out_name}" to {out_path}')

    full_prompt = "\n".join(parts)

    # Build claude command
    cmd = ["claude", "--dangerously-skip-permissions", "--verbose",
           "--output-format", "stream-json",
           "-d", abs_workdir, "-p"]

    if disallowed_tools:
        cmd.extend(["--disallowedTools", ",".join(disallowed_tools)])

    if model:
        cmd.extend(["--model", model])

    if max_budget_usd is not None:
        cmd.extend(["--max-budget-usd", str(max_budget_usd)])

    # Save prompt for debugging
    prompt_file = workdir / f"{task_id}_{step_label}_prompt.txt"
    prompt_file.write_text(full_prompt)

    # Clear CLAUDECODE env var to allow nested invocation
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    stderr_log = workdir / f"{task_id}_{step_label}_stderr.log"
    log(f"  [{task_id}] Running ({step_label}): {' '.join(cmd)}")
    log(f"  [{task_id}] Prompt file: {prompt_file}")

    stream_log_path = workdir / f"{task_id}_stream.log"
    tools_log_path = workdir / "tools.log"
    with (
        open(stderr_log, "w") as err_f,
        open(tools_log_path, "a") as tools_log,
        open(stream_log_path, "a") as stream_log,
    ):
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=err_f,
            env=env,
            cwd=abs_workdir,
            limit=10 * 1024 * 1024,  # 10MB buffer for large JSON lines
        )
        # Write prompt to stdin and close
        proc.stdin.write(full_prompt.encode())
        await proc.stdin.drain()
        proc.stdin.close()

        stream_log.write(f"\n{'='*60}\n[{task_id}] step: {step_label}\n{'='*60}\n")
        stream_log.flush()
        log(f"  [{task_id}] Streaming to: {stream_log_path}")

        # Stream and parse JSON output
        try:
            async for line_bytes in proc.stdout:
                try:
                    line = line_bytes.decode().strip()
                    if not line:
                        continue
                    msg = json.loads(line)
                    msg_type = msg.get("type")

                    # Extract content blocks from any message type
                    content = []
                    if msg_type == "assistant":
                        content = msg.get("message", {}).get("content", [])
                    elif msg_type == "user":
                        content = msg.get("message", {}).get("content", [])
                    if not isinstance(content, list):
                        content = []

                    for item in content:
                        item_type = item.get("type")
                        if item_type == "text":
                            stream_log.write(item["text"] + "\n")
                            stream_log.flush()
                        elif item_type == "thinking":
                            stream_log.write(f"[thinking] {item['thinking']}\n")
                            stream_log.flush()
                        elif item_type == "tool_use":
                            stream_log.write(f"[tool_use] {item.get('name')} {json.dumps(item.get('input', {}), indent=2)}\n")
                            stream_log.flush()
                            entry = {
                                "event": "PreToolUse",
                                "task": task_id,
                                "tool": item.get("name"),
                                "input": item.get("input"),
                            }
                            tools_log.write(json.dumps(entry) + "\n")
                            tools_log.flush()
                        elif item_type == "tool_result":
                            output = item.get("content", "")
                            if isinstance(output, str) and len(output) > 2000:
                                output = output[:2000] + "...(truncated)"
                            stream_log.write(f"[tool_result] {output}\n")
                            stream_log.flush()
                            entry = {
                                "event": "PostToolUse",
                                "task": task_id,
                                "tool_use_id": item.get("tool_use_id"),
                                "output": output,
                            }
                            tools_log.write(json.dumps(entry) + "\n")
                            tools_log.flush()
                except Exception:
                    pass
        except Exception:
            pass

        await proc.wait()

    # Check if expected output files were produced (exist and non-empty)
    missing = []
    empty = []
    for out_name, out_def in outputs.items():
        out_path = workdir / out_def["path"]
        if not out_path.exists():
            missing.append(out_def["path"])
        elif out_path.stat().st_size == 0:
            empty.append(out_def["path"])

    if missing:
        return {
            "status": "failed",
            "error": f"Missing output files: {', '.join(missing)}",
            "artifacts": [],
        }

    if empty:
        log(f"  [{task_id}] Warning: empty output files: {', '.join(empty)}")

    # Only include artifacts for files that exist and are non-empty
    artifacts = []
    for name, odef in outputs.items():
        out_path = workdir / odef["path"]
        if out_path.exists() and out_path.stat().st_size > 0:
            artifacts.append(
                {"name": name, "path": odef["path"], "format": odef["format"]})

    return {
        "status": "complete",
        "error": None,
        "artifacts": artifacts,
    }


async def run_claude_task(task: dict, workdir: Path) -> dict:
    """Run a claude task via claude CLI. Return result dict."""
    params = task["params"]
    outputs = params.get("outputs", {})

    # Step 1: Initial write
    result = await _invoke_claude(
        prompt=params["prompt"],
        workdir=workdir,
        task_id=task["id"],
        step_label="write",
        disallowed_tools=params.get("disallowed_tools") or None,
        system=params.get("system"),
        model=params.get("model"),
        max_budget_usd=params.get("max_budget_usd"),
        expected_outputs=outputs,
    )

    if result["status"] != "complete":
        return {
            "task_id": task["id"],
            "status": "failed",
            "error": result["error"],
            "artifacts": result["artifacts"],
            "manifest": None,
        }

    all_artifacts = list(result["artifacts"])

    # Step 2: Critic-optimizer loop
    n_iterations = params.get("n_iterations", 0)
    critic_prompt_template = params.get("critic_prompt")
    rewrite_prompt_template = params.get("rewrite_prompt")

    if n_iterations > 0 and critic_prompt_template and rewrite_prompt_template:
        # Determine primary output path (first output's path)
        primary_output = next(iter(outputs.values())) if outputs else None
        if primary_output:
            primary_path = Path(primary_output["path"])
            stem = primary_path.stem
            suffix = primary_path.suffix
            parent = str(primary_path.parent)
            draft_path = primary_output["path"]

            for i in range(1, n_iterations + 1):
                log(f"  [{task['id']}] Critic-optimizer iteration {i}/{n_iterations}")

                # --- Critic step ---
                critique_path = f"{parent}/{stem}_critic_{i}{suffix}"
                critic_prompt = (
                    critic_prompt_template
                    .replace("${draft_path}", draft_path)
                    .replace("${critique_path}", critique_path)
                )
                critic_outputs = {
                    f"critic_{i}": {"path": critique_path, "format": primary_output["format"]}
                }

                log(f"  [{task['id']}] Running critic {i}/{n_iterations}")
                critic_result = await _invoke_claude(
                    prompt=critic_prompt,
                    workdir=workdir,
                    task_id=task["id"],
                    step_label=f"critic_{i}",
                    disallowed_tools=params.get("critic_disallowed_tools") or None,
                    system=params.get("system"),
                    model=params.get("critic_model") or params.get("model"),
                    max_budget_usd=params.get("max_budget_usd"),
                    expected_outputs=critic_outputs,
                )

                if critic_result["status"] != "complete":
                    return {
                        "task_id": task["id"],
                        "status": "failed",
                        "error": f"Critic iteration {i} failed: {critic_result['error']}",
                        "artifacts": all_artifacts,
                        "manifest": None,
                    }
                all_artifacts.extend(critic_result["artifacts"])

                # --- Rewrite step ---
                rewrite_path = f"{parent}/{stem}_v{i + 1}{suffix}"
                rewrite_prompt = (
                    rewrite_prompt_template
                    .replace("${draft_path}", draft_path)
                    .replace("${critique_path}", critique_path)
                    .replace("${rewrite_path}", rewrite_path)
                )
                rewrite_outputs = {
                    f"rewrite_{i}": {"path": rewrite_path, "format": primary_output["format"]}
                }

                log(f"  [{task['id']}] Running rewrite {i}/{n_iterations}")
                rewrite_result = await _invoke_claude(
                    prompt=rewrite_prompt,
                    workdir=workdir,
                    task_id=task["id"],
                    step_label=f"rewrite_{i}",
                    disallowed_tools=params.get("rewrite_disallowed_tools") or None,
                    system=params.get("system"),
                    model=params.get("rewrite_model") or params.get("model"),
                    max_budget_usd=params.get("max_budget_usd"),
                    expected_outputs=rewrite_outputs,
                )

                if rewrite_result["status"] != "complete":
                    return {
                        "task_id": task["id"],
                        "status": "failed",
                        "error": f"Rewrite iteration {i} failed: {rewrite_result['error']}",
                        "artifacts": all_artifacts,
                        "manifest": None,
                    }
                all_artifacts.extend(rewrite_result["artifacts"])

                # Copy rewrite to original output path for downstream compatibility
                rewrite_file = workdir / rewrite_path
                original_file = workdir / primary_output["path"]
                if rewrite_file.exists():
                    shutil.copy2(str(rewrite_file), str(original_file))
                    log(f"  [{task['id']}] Copied {rewrite_path} -> {primary_output['path']}")

                # Update draft_path for next iteration
                draft_path = rewrite_path

    return {
        "task_id": task["id"],
        "status": "complete",
        "error": None,
        "artifacts": all_artifacts,
        "manifest": None,
    }


async def dispatch_task(task: dict, workdir: Path, ticker: str) -> dict:
    """Dispatch a task based on its type."""
    task_type = task["skill"]
    if task_type == "python":
        return await run_python_task(task, workdir, ticker)
    elif task_type == "claude":
        return await run_claude_task(task, workdir)
    else:
        return {
            "task_id": task["id"],
            "status": "failed",
            "error": f"Unknown task type: {task_type}",
            "artifacts": [],
            "manifest": None,
        }


async def process_results(results: list[dict], workdir: Path, tasks: list[dict]) -> tuple[int, int]:
    """Process wave results: register artifacts, extract vars, update DB. Return (completed, failed) counts."""
    completed = 0
    failed = 0

    # Build task lookup for sets_vars
    task_lookup = {t["id"]: t for t in tasks}

    for result in results:
        task_id = result["task_id"]
        task_def = task_lookup.get(task_id, {})
        params = task_def.get("params", {})

        if result["status"] == "complete":
            completed += 1

            # Register artifacts (skip missing or empty files)
            for artifact in result["artifacts"]:
                raw_path = Path(artifact["path"])
                artifact_file = workdir / raw_path
                # If workdir-relative path doesn't exist, check if the path
                # is already relative to project root (e.g. render_template.py
                # returns the full --output path like "work/SYM_DATE/artifacts/file.md")
                if not artifact_file.exists() and raw_path.exists():
                    artifact_file = raw_path
                    # Normalize to workdir-relative for DB storage
                    try:
                        artifact["path"] = str(raw_path.relative_to(workdir))
                    except ValueError:
                        artifact["path"] = str(raw_path)
                if not artifact_file.exists():
                    log(f"  [{task_id}] Skipping artifact '{artifact.get('name')}': file not found at {artifact['path']}")
                    continue
                if artifact_file.stat().st_size == 0:
                    log(f"  [{task_id}] Skipping artifact '{artifact.get('name')}': file is empty at {artifact['path']}")
                    continue
                try:
                    add_args = [
                        "artifact-add", "--workdir", str(workdir),
                        "--task-id", task_id,
                        "--name", artifact.get("name", "output"),
                        "--path", artifact["path"],
                        "--format", artifact.get("format", "unknown"),
                    ]
                    if artifact.get("source"):
                        add_args.extend(["--source", artifact["source"]])
                    if artifact.get("summary"):
                        add_args.extend(["--summary", artifact["summary"]])
                    await run_db(*add_args)
                except RuntimeError as e:
                    log(
                        f"  Warning: artifact-add failed for {task_id}/{artifact.get('name')}: {e}")

            # Extract sets_vars
            sets_vars = params.get("sets_vars", {})
            for var_name, var_def in sets_vars.items():
                try:
                    artifact_path = workdir / var_def["artifact"]
                    data = json.loads(artifact_path.read_text())
                    value = str(data[var_def["key"]])
                    await run_db(
                        "var-set", "--workdir", str(workdir),
                        "--name", var_name, "--value", value,
                        "--source-task", task_id,
                    )
                    log(f"  [{task_id}] Set var {var_name}={value}")
                except Exception as e:
                    log(f"  Warning: var-set failed for {var_name}: {e}")

            # Mark complete
            await run_db(
                "task-update", "--workdir", str(workdir),
                "--task-id", task_id, "--status", "complete",
            )

        else:
            failed += 1
            error = result.get("error", "Unknown error")
            log(f"  [{task_id}] FAILED: {error}")
            await run_db(
                "task-update", "--workdir", str(workdir),
                "--task-id", task_id, "--status", "failed",
                "--error", error,
            )

    return completed, failed


async def init_pipeline(ticker: str, dag: str, date: str) -> Path:
    """Validate DAG, create workdir, initialize DB. Return workdir Path."""
    # Validate
    result = await run_db("validate", "--dag", dag, "--ticker", ticker)
    log(f"DAG validated: {result['tasks']} tasks")

    # Init
    workdir = Path("work") / f"{ticker}_{date}"
    result = await run_db(
        "init", "--workdir", str(workdir), "--dag", dag,
        "--ticker", ticker, "--date", date,
    )
    log(f"DB initialized: {result['workdir']}")

    # Mark running
    await run_db("research-update", "--workdir", str(workdir), "--status", "running")

    return workdir


async def resume_pipeline(ticker: str, date: str) -> Path:
    """Resume an existing pipeline run. Reset interrupted tasks and continue."""
    workdir = Path("work") / f"{ticker}_{date}"
    db_path = workdir / "research.db"
    if not db_path.exists():
        raise RuntimeError(
            f"No existing run found at {workdir} — cannot resume")

    # Reset any tasks stuck in 'running' (interrupted) back to 'pending'
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    stuck = conn.execute(
        "SELECT id FROM tasks WHERE status = 'running'").fetchall()
    if stuck:
        stuck_ids = [r["id"] for r in stuck]
        log(f"Resetting {len(stuck_ids)} interrupted tasks to pending: {', '.join(stuck_ids)}")
        conn.execute(
            "UPDATE tasks SET status = 'pending' WHERE status = 'running'")
        conn.commit()
    conn.close()

    # Mark research as running
    await run_db("research-update", "--workdir", str(workdir), "--status", "running")

    # Show current status
    status = await run_db("status", "--workdir", str(workdir))
    task_counts = status.get("tasks", {})
    log(f"Resuming: {task_counts.get('complete', 0)} complete, "
        f"{task_counts.get('pending', 0)} pending, "
        f"{task_counts.get('failed', 0)} failed")

    return workdir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run equity research DAG pipeline")
    parser.add_argument("ticker", help="Stock ticker symbol (e.g. AAPL)")
    parser.add_argument("--dag", default="dags/sra.yaml", help="DAG YAML file")
    parser.add_argument(
        "--date", default=datetime.now().strftime("%Y%m%d"),
        help="Date string YYYYMMDD (default: today)",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume an existing run (skip init, reset interrupted tasks)",
    )
    parser.add_argument(
        "--retry-failed", action="store_true",
        help="When resuming, also retry previously failed tasks",
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    ticker = args.ticker.upper()

    if args.resume:
        log(f"Resuming research pipeline for {ticker}")
        try:
            workdir = await resume_pipeline(ticker, args.date)
        except RuntimeError as e:
            log(f"Resume failed: {e}")
            return 1

        if args.retry_failed:
            import sqlite3
            db_path = workdir / "research.db"
            conn = sqlite3.connect(str(db_path))
            failed = conn.execute(
                "SELECT id FROM tasks WHERE status = 'failed'").fetchall()
            if failed:
                failed_ids = [r["id"] for r in failed]
                log(f"Retrying {len(failed_ids)} failed tasks: {', '.join(failed_ids)}")
                conn.execute(
                    "UPDATE tasks SET status = 'pending', error = NULL WHERE status = 'failed'")
                conn.commit()
            conn.close()
    else:
        log(f"Starting research pipeline for {ticker}")
        try:
            workdir = await init_pipeline(ticker, args.dag, args.date)
        except RuntimeError as e:
            log(f"Initialization failed: {e}")
            return 1

    log(f"Workdir: {workdir}")

    wave = 0
    total_completed = 0
    total_failed = 0

    while True:
        # Get ready tasks
        ready = await run_db("task-ready", "--workdir", str(workdir))
        if not ready:
            break

        wave += 1
        task_ids = [t["id"] for t in ready]
        log(f"\n{'='*60}")
        log(f"Wave {wave}: dispatching {len(ready)} tasks: {', '.join(task_ids)}")
        log(f"{'='*60}")

        # Mark all as running
        for t in ready:
            await run_db(
                "task-update", "--workdir", str(workdir),
                "--task-id", t["id"], "--status", "running",
            )

        # Update manifest before launching (Claude tasks read it)
        await write_manifest(workdir)

        # Dispatch all tasks in parallel
        coros = [dispatch_task(t, workdir, ticker) for t in ready]
        results = await asyncio.gather(*coros)

        # Process results (centralized DB writes)
        completed, failed = await process_results(results, workdir, ready)
        total_completed += completed
        total_failed += failed

        log(f"Wave {wave} done: {completed} completed, {failed} failed")

        # Safety: if nothing happened, abort
        if completed == 0 and failed == 0:
            log("ERROR: No progress in this wave — aborting")
            break

    # Finalize
    await run_db("research-update", "--workdir", str(workdir), "--status", "complete")

    # Print final status
    status = await run_db("status", "--workdir", str(workdir))
    log(f"\nPipeline finished: {total_completed} completed, {total_failed} failed")
    print(json.dumps(status, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
