#!/usr/bin/env python3
"""
research.py — Async DAG orchestrator for equity research pipeline.

Reads DAG YAML, initializes SQLite via db.py, runs waves of tasks as
async subprocesses. Python tasks via `uv run python`, Claude tasks via
`claude --dangerously-skip-permissions -p`.

Usage:
    ./research.py TICKER [--dag dags/sra.yaml] [--date YYYYMMDD] [--clean]
"""

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

# Add skills to path for imports
_SKILLS_DIR = Path(__file__).parent / "skills"
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

from utils import invoke_claude as _invoke_claude  # noqa: E402
from utils import load_environment as _load_environment  # noqa: E402
from utils import substitute_vars as _substitute_vars  # noqa: E402

DB_PY = Path(__file__).parent / "skills" / "db.py"
_PROJECT_ROOT = Path(__file__).parent

# Default timeout (seconds) for Claude subprocess tasks.
# Tasks with MCP tools can hang indefinitely on unresponsive servers.
CLAUDE_TASK_TIMEOUT = 1200  # 20 minutes


_PROXY_SCRIPT = str(_PROJECT_ROOT / "skills" / "mcp_proxy" / "mcp_proxy.py")


def _wrap_with_proxy(server_def: dict) -> dict:
    """Wrap an MCP server definition with the caching proxy."""
    if "url" in server_def:
        return {
            "command": "uv",
            "args": [
                "run", "python", _PROXY_SCRIPT,
                "--transport", "http",
                "--url", server_def["url"],
            ],
        }
    real_cmd = server_def.get("command", "")
    real_args = server_def.get("args", [])
    args_str = ",".join(str(a) for a in real_args)
    proxy_args = [
        "run", "python", _PROXY_SCRIPT,
        "--transport", "stdio",
        "--command", real_cmd,
    ]
    if args_str:
        proxy_args += ["--args", args_str]
    result = {"command": "uv", "args": proxy_args}
    if "env" in server_def:
        result["env"] = server_def["env"]
    return result


def hydrate_mcp_configs(workdir: Path) -> None:
    """Hydrate MCP config templates from templates/*.j2 into workdir, wrapping servers with caching proxy."""
    template_dir = _PROJECT_ROOT / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)))
    for template_path in template_dir.glob("mcp-*.json.j2"):
        output_name = template_path.stem  # e.g. "mcp-research.json"
        output_path = workdir / output_name
        if output_path.exists():
            continue
        template = env.get_template(template_path.name)
        hydrated = template.render(os.environ)
        # Wrap each server with the caching proxy
        config = json.loads(hydrated)
        for name, server_def in config.get("mcpServers", {}).items():
            config["mcpServers"][name] = _wrap_with_proxy(server_def)
        output_path.write_text(json.dumps(config, indent=2))
        log(f"Hydrated {template_path.name} -> {output_path} (proxy-wrapped)")


def collect_custom_prompts(workdir: Path) -> None:
    """Interactively collect custom investigation prompts from the user."""
    prompts_file = workdir / "custom_prompts.json"
    if prompts_file.exists():
        log(f"Custom prompts already exist at {prompts_file} — skipping collection")
        return

    print("\nCustom investigation prompts (enter empty line to finish):", file=sys.stderr)
    prompts = []
    idx = 1
    while True:
        try:
            line = input(f"[{idx}]> ").strip()
        except EOFError:
            break
        if not line:
            break
        prompts.append({"id": f"custom_{idx}", "prompt": line})
        idx += 1

    workdir.mkdir(parents=True, exist_ok=True)
    prompts_file.write_text(json.dumps(prompts, indent=2))
    if prompts:
        log(f"Saved {len(prompts)} custom prompts to {prompts_file}")
    else:
        log("No custom prompts entered — task will be a no-op")


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

    # Exit code convention: 0=success, 1=partial/warnings, >=2=hard failure
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


def run_hard_checks(file_path: Path, checks: list[str]) -> list[dict]:
    """Run programmatic hard checks against a file's content.

    Each check is a string with format 'rule: value'. Returns a list of
    dicts with keys: check, passed, message.
    """
    content = file_path.read_text()
    results = []

    for check in checks:
        sep = ": "
        idx = check.find(sep)
        if idx == -1:
            results.append({"check": check, "passed": False, "message": f"Malformed check (no ': ' separator): {check}"})
            continue
        rule = check[:idx].strip()
        value = check[idx + len(sep):]

        if rule == "min_length":
            threshold = int(value)
            passed = len(content) >= threshold
            results.append({
                "check": check,
                "passed": passed,
                "message": f"Content length {len(content)} >= {threshold}" if passed
                    else f"Content length {len(content)} is below minimum {threshold}",
            })
        elif rule == "max_length":
            threshold = int(value)
            passed = len(content) <= threshold
            results.append({
                "check": check,
                "passed": passed,
                "message": f"Content length {len(content)} <= {threshold}" if passed
                    else f"Content length {len(content)} exceeds maximum {threshold}",
            })
        elif rule == "startswith":
            first_line = ""
            for line in content.splitlines():
                if line.strip():
                    first_line = line
                    break
            passed = first_line.startswith(value)
            results.append({
                "check": check,
                "passed": passed,
                "message": f"First line starts with '{value}'" if passed
                    else f"First non-blank line is: '{first_line[:80]}' — expected to start with '{value}'",
            })
        elif rule == "contains":
            passed = value in content
            results.append({
                "check": check,
                "passed": passed,
                "message": f"Content contains '{value}'" if passed
                    else f"Content does not contain '{value}'",
            })
        elif rule == "regex":
            passed = bool(re.search(value, content, re.MULTILINE))
            results.append({
                "check": check,
                "passed": passed,
                "message": f"Regex '{value}' matched" if passed
                    else f"Regex '{value}' did not match anywhere in content",
            })
        else:
            results.append({"check": check, "passed": False, "message": f"Unknown check rule: {rule}"})

    return results


def write_hard_critique(workdir: Path, stem: str, iteration: int, failures: list[dict]) -> str:
    """Write a hard check failure critique file. Returns workdir-relative path."""
    drafts_dir = workdir / "drafts"
    drafts_dir.mkdir(exist_ok=True)

    critique_path = f"drafts/{stem}_hard_critic_{iteration}.md"
    lines = ["HARD CHECK FAILURES — fix these issues in the rewrite:\n"]
    for i, f in enumerate(failures, 1):
        lines.append(f"{i}. FAIL: {f['check']}")
        lines.append(f"   {f['message']}")
        # Add actionable guidance
        check = f["check"]
        sep_idx = check.find(": ")
        if sep_idx != -1:
            rule = check[:sep_idx].strip()
            value = check[sep_idx + 2:]
            if rule == "startswith":
                lines.append(f"   Action: Ensure the very first line of the document is: {value}")
            elif rule == "min_length":
                lines.append(f"   Action: Expand the analysis to meet the minimum length of {value} characters.")
            elif rule == "max_length":
                lines.append(f"   Action: Trim the content to stay under {value} characters.")
            elif rule == "contains":
                lines.append(f"   Action: Include the text '{value}' somewhere in the document.")
            elif rule == "regex":
                lines.append(f"   Action: Ensure the content matches the pattern: {value}")
        lines.append("")

    (workdir / critique_path).write_text("\n".join(lines))
    log(f"  Hard critique written to {critique_path}")
    return critique_path


async def _run_hard_check_rewrite(
    workdir: Path, task: dict, params: dict,
    stem: str, suffix: str, draft_path: str,
    hard_checks_list: list[str], hard_check_retries: int,
    primary_output: dict, rewrite_prompt_template: str,
    task_mcp_config, task_extra_env, task_timeout: int,
    label_prefix: str = "hard",
) -> tuple[str, list[dict], int]:
    """Run hard check → rewrite loop. Returns (draft_path, final_failures, retries_used)."""
    failures = run_hard_checks(workdir / primary_output["path"], hard_checks_list)
    hard_retry_count = 0

    while any(not f["passed"] for f in failures) and hard_retry_count < hard_check_retries:
        hard_retry_count += 1
        failed = [f for f in failures if not f["passed"]]
        log(f"  [{task['id']}] Hard check failures ({len(failed)}), retry {hard_retry_count}/{hard_check_retries}")

        # Write critique
        critique_path = write_hard_critique(workdir, stem, hard_retry_count, failed)

        # Rewrite
        rewrite_path = f"drafts/{stem}_{label_prefix}_v{hard_retry_count}{suffix}"
        rewrite_prompt = (
            rewrite_prompt_template
            .replace("${draft_path}", draft_path)
            .replace("${critique_path}", critique_path)
            .replace("${rewrite_path}", rewrite_path)
        )
        rewrite_outputs = {
            f"{label_prefix}_rewrite_{hard_retry_count}": {
                "path": rewrite_path,
                "format": primary_output["format"],
            }
        }

        rewrite_result = await _invoke_claude(
            prompt=rewrite_prompt,
            workdir=workdir,
            task_id=task["id"],
            step_label=f"{label_prefix}_rewrite_{hard_retry_count}",
            disallowed_tools=params.get("rewrite_disallowed_tools") or None,
            system=params.get("system"),
            model=params.get("rewrite_model") or params.get("model"),
            max_budget_usd=params.get("max_budget_usd"),
            expected_outputs=rewrite_outputs,
            artifacts_inline=None,
            mcp_config=task_mcp_config,
            extra_env=task_extra_env,
            timeout=task_timeout,
        )

        if rewrite_result["status"] != "complete":
            log(f"  [{task['id']}] Hard check rewrite failed: {rewrite_result['error']}")
            break

        # Publish rewrite to artifacts
        rewrite_file = workdir / rewrite_path
        artifact_file = workdir / primary_output["path"]
        if rewrite_file.exists():
            shutil.copy2(str(rewrite_file), str(artifact_file))
            log(f"  [{task['id']}] Published {rewrite_path} -> {primary_output['path']}")

        draft_path = rewrite_path
        failures = run_hard_checks(workdir / primary_output["path"], hard_checks_list)

    return draft_path, failures, hard_retry_count


async def _run_critic_loop(
    workdir: Path, task: dict, params: dict,
    stem: str, suffix: str, draft_path: str,
    n_iterations: int,
    critic_prompt_template: str, rewrite_prompt_template: str,
    primary_output: dict, primary_artifacts: list[dict],
    task_mcp_config, task_extra_env, task_timeout: int,
) -> dict:
    """Run n_iterations of critic → rewrite cycles, publishing each rewrite.

    Returns dict with keys: status, draft_path, and on failure: task_id, error,
    artifacts, manifest.
    """
    for i in range(1, n_iterations + 1):
        log(f"  [{task['id']}] Critic-optimizer iteration {i}/{n_iterations}")

        # --- Critic step ---
        critique_path = f"drafts/{stem}_critic_{i}{suffix}"
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
            artifacts_inline=None,
            mcp_config=task_mcp_config,
            extra_env=task_extra_env,
            timeout=task_timeout,
        )

        if critic_result["status"] != "complete":
            return {
                "status": "failed",
                "task_id": task["id"],
                "error": f"Critic iteration {i} failed: {critic_result['error']}",
                "artifacts": primary_artifacts,
                "manifest": None,
            }

        # --- Rewrite step ---
        rewrite_path = f"drafts/{stem}_v{i}{suffix}"
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
            artifacts_inline=None,
            mcp_config=task_mcp_config,
            extra_env=task_extra_env,
            timeout=task_timeout,
        )

        if rewrite_result["status"] != "complete":
            return {
                "status": "failed",
                "task_id": task["id"],
                "error": f"Rewrite iteration {i} failed: {rewrite_result['error']}",
                "artifacts": primary_artifacts,
                "manifest": None,
            }

        # Publish: copy rewrite to artifacts (overwrite primary output)
        rewrite_file = workdir / rewrite_path
        original_file = workdir / primary_output["path"]
        if rewrite_file.exists():
            shutil.copy2(str(rewrite_file), str(original_file))
            log(f"  [{task['id']}] Published {rewrite_path} -> {primary_output['path']}")

        draft_path = rewrite_path

    return {"status": "complete", "draft_path": draft_path}


async def run_claude_task(task: dict, workdir: Path) -> dict:
    """Run a claude task via claude CLI. Return result dict."""
    params = task["params"]
    outputs = params.get("outputs", {})

    # Check for critic-optimizer loop
    n_iterations = params.get("n_iterations", 0)
    critic_prompt_template = params.get("critic_prompt")
    rewrite_prompt_template = params.get("rewrite_prompt")
    hard_checks_list = params.get("hard_checks", [])
    hard_check_retries = params.get("hard_check_retries", 2)
    has_critic_loop = (
        n_iterations > 0
        and critic_prompt_template
        and rewrite_prompt_template
        and outputs
    )
    has_drafts_flow = has_critic_loop or (hard_checks_list and rewrite_prompt_template and outputs)

    # For tasks with critic loop or hard checks, initial write goes to drafts/
    # The prompt instructs Claude to save there; expected_outputs must match
    if has_drafts_flow:
        primary_name = next(iter(outputs.keys()))
        primary_output = outputs[primary_name]
        primary_path = Path(primary_output["path"])
        stem = primary_path.stem
        suffix = primary_path.suffix

        drafts_dir = workdir / "drafts"
        drafts_dir.mkdir(exist_ok=True)

        draft_write_path = f"drafts/{stem}{suffix}"
        write_outputs = dict(outputs)
        write_outputs[primary_name] = {**primary_output, "path": draft_write_path}
    else:
        write_outputs = outputs

    # Pre-copy: copy files before Claude starts (avoids reading large files into context)
    for copy_spec in params.get("pre_copy", []):
        src = workdir / copy_spec["from"]
        dst = workdir / copy_spec["to"]
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))
            log(f"  [{task['id']}] Pre-copied {copy_spec['from']} -> {copy_spec['to']}")
        else:
            log(f"  [{task['id']}] WARNING: pre_copy source not found: {copy_spec['from']}")

    # Resolve mcp_config and extra_env
    task_mcp_config = params.get("mcp_config") or None
    task_extra_env = {"MCP_CACHE_WORKDIR": str(workdir), "MCP_TASK_ID": task["id"]} if task_mcp_config else None

    # Step 1: Initial write
    task_timeout = params.get("timeout") or CLAUDE_TASK_TIMEOUT
    result = await _invoke_claude(
        prompt=params["prompt"],
        workdir=workdir,
        task_id=task["id"],
        step_label="write",
        disallowed_tools=params.get("disallowed_tools") or None,
        system=params.get("system"),
        model=params.get("model"),
        max_budget_usd=params.get("max_budget_usd"),
        expected_outputs=write_outputs,
        artifacts_inline=params.get("artifacts_inline") or None,
        mcp_config=task_mcp_config,
        extra_env=task_extra_env,
        timeout=task_timeout,
    )

    if result["status"] != "complete":
        return {
            "task_id": task["id"],
            "status": "failed",
            "error": result["error"],
            "artifacts": result["artifacts"],
            "manifest": None,
        }

    # Step 2: Drafts flow (critic loop and/or hard checks)
    if has_drafts_flow:
        # Publish initial write: drafts/ -> artifacts/
        src = workdir / draft_write_path
        dst = workdir / primary_output["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
        log(f"  [{task['id']}] Published initial write to {primary_output['path']}")

        # Primary artifacts reference artifact paths (not drafts)
        primary_artifacts = [
            {"name": name, "path": odef["path"], "format": odef["format"]}
            for name, odef in outputs.items()
        ]

        # Preserve initial draft as v0
        v0_path = f"drafts/{stem}_v0{suffix}"
        shutil.copy2(str(src), str(workdir / v0_path))
        log(f"  [{task['id']}] Copied initial write to {v0_path}")
        draft_path = v0_path

        # Step 2a: Hard checks on initial write (before critic loop)
        hard_retries_used = 0
        if hard_checks_list:
            log(f"  [{task['id']}] Running hard checks on initial write")
            draft_path, pre_failures, hard_retries_used = await _run_hard_check_rewrite(
                workdir=workdir, task=task, params=params,
                stem=stem, suffix=suffix, draft_path=draft_path,
                hard_checks_list=hard_checks_list, hard_check_retries=hard_check_retries,
                primary_output=primary_output, rewrite_prompt_template=rewrite_prompt_template,
                task_mcp_config=task_mcp_config, task_extra_env=task_extra_env,
                task_timeout=task_timeout, label_prefix="hard_pre",
            )
            pre_passed = all(f["passed"] for f in pre_failures)
            if pre_passed:
                log(f"  [{task['id']}] All hard checks passed")
            else:
                failed_names = [f["check"] for f in pre_failures if not f["passed"]]
                log(f"  [{task['id']}] Hard checks still failing after {hard_retries_used} retries: {failed_names}")

        # Step 2b: Critic-optimizer loop (if configured)
        if has_critic_loop:
            critic_result = await _run_critic_loop(
                workdir=workdir, task=task, params=params,
                stem=stem, suffix=suffix, draft_path=draft_path,
                n_iterations=n_iterations,
                critic_prompt_template=critic_prompt_template,
                rewrite_prompt_template=rewrite_prompt_template,
                primary_output=primary_output, primary_artifacts=primary_artifacts,
                task_mcp_config=task_mcp_config, task_extra_env=task_extra_env,
                task_timeout=task_timeout,
            )
            if critic_result["status"] == "failed":
                return critic_result
            draft_path = critic_result["draft_path"]

        # Step 2c: Hard checks on final output (after critic loop)
        if hard_checks_list:
            remaining_retries = hard_check_retries - hard_retries_used
            if remaining_retries > 0:
                log(f"  [{task['id']}] Running final hard checks ({remaining_retries} retries remaining)")
                draft_path, post_failures, post_retries = await _run_hard_check_rewrite(
                    workdir=workdir, task=task, params=params,
                    stem=stem, suffix=suffix, draft_path=draft_path,
                    hard_checks_list=hard_checks_list, hard_check_retries=remaining_retries,
                    primary_output=primary_output, rewrite_prompt_template=rewrite_prompt_template,
                    task_mcp_config=task_mcp_config, task_extra_env=task_extra_env,
                    task_timeout=task_timeout, label_prefix="hard_post",
                )
                post_passed = all(f["passed"] for f in post_failures)
                if post_passed:
                    log(f"  [{task['id']}] All final hard checks passed")
                else:
                    failed_names = [f["check"] for f in post_failures if not f["passed"]]
                    log(f"  [{task['id']}] WARNING: Hard checks still failing after all retries: {failed_names}")
            else:
                # No retries left, just report final status
                final_failures = run_hard_checks(workdir / primary_output["path"], hard_checks_list)
                if not all(f["passed"] for f in final_failures):
                    failed_names = [f["check"] for f in final_failures if not f["passed"]]
                    log(f"  [{task['id']}] WARNING: Hard checks still failing (no retries left): {failed_names}")
    else:
        # No critic loop or hard checks — artifacts come directly from the write result
        primary_artifacts = list(result["artifacts"])

    return {
        "task_id": task["id"],
        "status": "complete",
        "error": None,
        "artifacts": primary_artifacts,
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
                    desc = artifact.get("description") or artifact.get("summary") or ""
                    if desc:
                        add_args.extend(["--description", desc])
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

            # Extract variables from manifest (for dynamic var emission)
            manifest_vars = (result.get("manifest") or {}).get("variables", {})
            for var_name, var_value in manifest_vars.items():
                try:
                    await run_db(
                        "var-set", "--workdir", str(workdir),
                        "--name", var_name, "--value", str(var_value),
                        "--source-task", task_id,
                    )
                    log(f"  [{task_id}] Set var {var_name}={var_value}")
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


async def init_pipeline(ticker: str, dag: str, date: str, length: str = "standard") -> Path:
    """Validate DAG, create workdir, initialize DB. Return workdir Path."""
    # Validate
    result = await run_db("validate", "--dag", dag, "--ticker", ticker)
    log(f"DAG validated: {result['tasks']} tasks")

    # Init
    workdir = Path("work") / f"{ticker}_{date}"
    init_args = [
        "init", "--workdir", str(workdir), "--dag", dag,
        "--ticker", ticker, "--date", date,
    ]
    if length != "standard":
        init_args += ["--length", length]
    result = await run_db(*init_args)
    log(f"DB initialized: {result['workdir']}")

    # Create knowledge/ directory for research findings
    (workdir / "knowledge").mkdir(exist_ok=True)

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
        "--clean", action="store_true",
        help="Remove existing workdir before starting (for re-runs)",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume an existing run (skip init, reset interrupted tasks)",
    )
    parser.add_argument(
        "--retry-failed", action="store_true",
        help="When resuming, also retry previously failed tasks",
    )
    parser.add_argument(
        "--task", metavar="TASK_ID",
        help="Run a single task by ID (workdir must already be initialized)",
    )
    parser.add_argument(
        "--length", choices=["short", "standard", "long"], default="standard",
        help="Report section length preset (default: standard)",
    )
    parser.add_argument(
        "--reload-yaml", action="store_true",
        help="Re-read DAG YAML and update task params in DB before running (use with --task)",
    )
    return parser.parse_args()


def _reload_task_from_yaml(
    dag_file: str, task_id: str, ticker: str, workdir: Path, conn: "sqlite3.Connection",
) -> None:
    """Re-parse DAG YAML and update a single task's params in the DB."""
    import yaml
    from jinja2 import Environment as JEnv, BaseLoader, Undefined

    dag_path = Path(dag_file)
    if not dag_path.exists():
        raise RuntimeError(f"DAG file not found: {dag_path}")

    # Determine length preset from stored dag_var (set during init)
    row = conn.execute(
        "SELECT value FROM dag_vars WHERE name = 'length_preset'"
    ).fetchone()
    length = row["value"] if row else "standard"

    raw_text = dag_path.read_text()
    jinja_vars = {"SHORT": length == "short", "LONG": length == "long", "LENGTH": length}
    rendered = JEnv(loader=BaseLoader(), undefined=Undefined,
                    keep_trailing_newline=True).from_string(raw_text).render(jinja_vars)
    raw = yaml.safe_load(rendered)

    variables = {"ticker": ticker, "date": workdir.name.split("_", 1)[1], "workdir": str(workdir)}

    from schema import load_dag
    dag = load_dag(raw, variables)

    if task_id not in dag.tasks:
        raise RuntimeError(f"Task '{task_id}' not found in DAG YAML")

    task = dag.tasks[task_id]
    params = task.config.model_dump()
    params["outputs"] = {k: v.model_dump() for k, v in task.outputs.items()}
    if task.sets_vars:
        params["sets_vars"] = {k: v.model_dump() for k, v in task.sets_vars.items()}

    # Resolve DAG-level vars
    dag_level_vars = dag.dag.vars or {}
    if dag_level_vars:
        all_vars = {**dag_level_vars, **variables}
        params = _substitute_vars(params, all_vars)

    conn.execute("UPDATE tasks SET params = ? WHERE id = ?", (json.dumps(params), task_id))
    conn.commit()
    log(f"Reloaded task '{task_id}' params from {dag_file}")


async def run_single_task(
    ticker: str, task_id: str, workdir: Path, *, reload_yaml: bool = False, dag_file: str = "dags/sra.yaml",
) -> int:
    """Run a single task by ID. Check deps, dispatch, process, return exit code."""
    import sqlite3

    db_path = workdir / "research.db"
    if not db_path.exists():
        log(f"ERROR: No initialized workdir at {workdir}")
        log(f"Run:  ./skills/db.py init --workdir {workdir} --dag dags/sra.yaml --ticker {ticker}")
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Verify task exists
    task_row = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task_row:
        available = [r["id"] for r in conn.execute("SELECT id FROM tasks ORDER BY sort_order").fetchall()]
        conn.close()
        log(f"ERROR: Task '{task_id}' not found in DAG")
        log(f"Available tasks: {', '.join(available)}")
        return 1

    # Reload task params from YAML if requested
    if reload_yaml:
        try:
            _reload_task_from_yaml(dag_file, task_id, ticker, workdir, conn)
        except RuntimeError as e:
            conn.close()
            log(f"ERROR: {e}")
            return 1

    # Check dependencies
    deps = conn.execute(
        "SELECT depends_on FROM task_deps WHERE task_id = ?", (task_id,)
    ).fetchall()
    unmet = []
    for dep in deps:
        dep_status = conn.execute(
            "SELECT status FROM tasks WHERE id = ?", (dep["depends_on"],)
        ).fetchone()
        if not dep_status or dep_status["status"] != "complete":
            unmet.append(dep["depends_on"])

    if unmet:
        conn.close()
        log(f"ERROR: Cannot run '{task_id}': unmet dependencies: {', '.join(unmet)}")
        return 1

    # Reset task to pending (allows re-running completed/failed tasks)
    conn.execute(
        "UPDATE tasks SET status = 'pending', error = NULL WHERE id = ?", (task_id,)
    )
    conn.commit()
    conn.close()

    # Hydrate MCP configs
    hydrate_mcp_configs(workdir)

    # Ensure knowledge/ dir exists for research agents
    (workdir / "knowledge").mkdir(exist_ok=True)

    # Get full task config from DB
    task = await run_db("task-get", "--workdir", str(workdir), "--task-id", task_id)

    # Resolve runtime vars (e.g. company_name, symbol) in task params
    runtime_vars = await run_db("var-get", "--workdir", str(workdir))
    if runtime_vars:
        task["params"] = _substitute_vars(task["params"], runtime_vars)

    # Mark running
    await run_db(
        "task-update", "--workdir", str(workdir),
        "--task-id", task_id, "--status", "running",
    )

    # Update manifest before dispatch
    await write_manifest(workdir)

    log(f"Dispatching task: {task_id}")
    result = await dispatch_task(task, workdir, ticker)

    # Process result
    completed, failed = await process_results([result], workdir, [task])

    if failed:
        log(f"Task '{task_id}' FAILED")
        return 1

    log(f"Task '{task_id}' completed successfully")
    return 0


async def main() -> int:
    _load_environment()
    args = parse_args()
    ticker = args.ticker.upper()

    if args.task:
        workdir = Path("work") / f"{ticker}_{args.date}"
        return await run_single_task(
            ticker, args.task, workdir,
            reload_yaml=args.reload_yaml, dag_file=args.dag,
        )

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
            conn.row_factory = sqlite3.Row
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

        # --clean: remove existing workdir for a fresh re-run
        if args.clean:
            candidate = Path("work") / f"{ticker}_{args.date}"
            if candidate.exists():
                log(f"Removing existing workdir: {candidate}")
                shutil.rmtree(candidate)

        try:
            workdir = await init_pipeline(ticker, args.dag, args.date, args.length)
        except RuntimeError as e:
            log(f"Initialization failed: {e}")
            return 1

    log(f"Workdir: {workdir}")

    # Hydrate MCP config templates into workdir (once, before DAG execution)
    hydrate_mcp_configs(workdir)

    # Collect custom investigation prompts (interactive, skip on resume)
    if not args.resume:
        collect_custom_prompts(workdir)

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

        # Fetch runtime vars (e.g. company_name, symbol set by profile task)
        # and resolve them in task params before dispatch
        runtime_vars = await run_db("var-get", "--workdir", str(workdir))
        if runtime_vars:
            for t in ready:
                t["params"] = _substitute_vars(t["params"], runtime_vars)

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
    final_status = "complete" if total_failed == 0 else "failed"
    await run_db("research-update", "--workdir", str(workdir), "--status", final_status)

    # Print final status
    status = await run_db("status", "--workdir", str(workdir))
    log(f"\nPipeline finished: {total_completed} completed, {total_failed} failed")
    print(json.dumps(status, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
