"""
Claude CLI subprocess runner.

Provides the canonical invoke_claude() async function that all callers use
to run Claude via the CLI.  The function builds a prompt (with optional
inline artifacts and system preamble), spawns ``claude --dangerously-skip-permissions``,
streams structured JSON output, logs tool-use events, and verifies that
expected output files were produced.

Callers:
    - research.py (orchestrator)
    - custom_research.py (parallel investigation prompts)
    - fetch_detailed_profile_info.py (7 parallel web-search tasks)
"""

import asyncio
import json
import os
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)


async def invoke_claude(
    prompt: str,
    workdir: Path,
    step_label: str,
    *,
    task_id: Optional[str] = None,
    output_file: Optional[str] = None,
    expected_outputs: Optional[dict] = None,
    disallowed_tools: Optional[list] = None,
    system: Optional[str] = None,
    model: Optional[str] = None,
    max_budget_usd: Optional[float] = None,
    artifacts_inline: Optional[list] = None,
    mcp_config: Optional[list] = None,
    extra_env: Optional[dict] = None,
    debug: bool = False,
    tools_log_path: Optional[Path] = None,
    stream_to_stdout: bool = False,
    stream_prefix: Optional[str] = None,
    timeout: Optional[int] = None,
) -> dict:
    """
    Invoke claude -p with a prompt via CLI subprocess.

    This is the canonical way to invoke the Claude CLI from Python. All
    callers (orchestrator, individual skills) should use this function.

    Args:
        prompt: The prompt text to send to Claude.
        workdir: Working directory for the Claude process and log files.
        step_label: Label for log/prompt files (e.g. 'write', 'critic_1').
        task_id: Optional task identifier for log prefixes. Defaults to step_label.
        output_file: Simple mode — single output file to verify. Mutually
                     exclusive with expected_outputs.
        expected_outputs: Dict of {name: {"path": str, "format": str}} for
                          multi-output verification. Mutually exclusive with output_file.
        disallowed_tools: List of tool names to pass via --disallowedTools.
        system: System prompt prepended to the full prompt.
        model: Model override via --model flag.
        max_budget_usd: Budget cap via --max-budget-usd flag.
        artifacts_inline: List of artifact paths (relative to workdir) to inline
                          in the prompt. Files >50KB are skipped.
        mcp_config: List of MCP config file paths to pass via --mcp-config.
        extra_env: Additional environment variables for the subprocess.
        debug: If True, enable --debug and write debug log.
        tools_log_path: Optional path to append tool_use/tool_result entries (JSONL).
        stream_to_stdout: If True, echo stream log content to stdout in real time.
        stream_prefix: Optional prefix for stdout lines (e.g. "[news]") for interleaved output.
        timeout: Optional timeout in seconds for the entire subprocess. If exceeded,
                 the process is killed and a failed result is returned.

    Returns:
        Dict with keys: status ("complete"|"failed"), error (str|None),
        artifacts (list of {name, path, format}).
    """
    abs_workdir = str(workdir.resolve())
    label = task_id or step_label

    full_prompt = _build_prompt(prompt, workdir, label, system, artifacts_inline, expected_outputs, output_file)

    cmd = _build_command(abs_workdir, disallowed_tools, model, max_budget_usd, mcp_config, debug, workdir, step_label)

    # Save prompt for debugging
    prompt_file = workdir / f"{label}_{step_label}_prompt.txt"
    prompt_file.write_text(full_prompt)

    # Build environment
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    if extra_env:
        env.update(extra_env)

    stderr_log = workdir / f"{label}_{step_label}_stderr.log"
    logger.info(f"  [{label}] Running ({step_label}): {' '.join(cmd)}")
    logger.info(f"  [{label}] Prompt file: {prompt_file}")

    stream_log_path_file = workdir / f"{label}_stream.log"
    tools_log = tools_log_path or (workdir / "tools.log")

    with (
        open(stderr_log, "w") as err_f,
        open(tools_log, "a") as tools_f,
        open(stream_log_path_file, "a") as stream_f,
    ):
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=err_f,
            env=env,
            cwd=abs_workdir,
            limit=10 * 1024 * 1024,
        )
        assert proc.stdin is not None
        proc.stdin.write(full_prompt.encode())
        await proc.stdin.drain()
        proc.stdin.close()

        stream_f.write(f"\n{'='*60}\n[{label}] step: {step_label}\n{'='*60}\n")
        stream_f.flush()

        async def _run_and_wait() -> None:
            await _consume_stream(proc, stream_f, tools_f, label, stream_to_stdout, stream_prefix)
            await proc.wait()

        try:
            await asyncio.wait_for(_run_and_wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"  [{label}] Timeout after {timeout}s — killing subprocess")
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
            return {
                "status": "failed",
                "error": f"Timed out after {timeout}s",
                "artifacts": [],
            }

    # Normalize outputs dict
    outputs: dict = {}
    if expected_outputs:
        outputs = expected_outputs
    elif output_file:
        outputs = {"output": {"path": output_file, "format": "json"}}

    return _check_outputs(outputs, workdir, label, proc.returncode)


def _build_prompt(
    prompt: str,
    workdir: Path,
    label: str,
    system: Optional[str],
    artifacts_inline: Optional[list],
    expected_outputs: Optional[dict],
    output_file: Optional[str],
) -> str:
    """Assemble the full prompt from system preamble, task prompt, and inline artifacts."""
    parts = []
    if system:
        parts.append(system)
        parts.append("")

    inline_artifacts = artifacts_inline or []

    parts.append("")
    parts.append("---")
    parts.append("")
    parts.append(prompt)

    # Inline artifacts (after the task prompt so instructions come first)
    if inline_artifacts:
        parts.append("")
        parts.append("--- INLINE ARTIFACTS ---")
        for art_path in inline_artifacts:
            full_path = Path(workdir) / art_path
            if full_path.exists() and full_path.stat().st_size < 50_000:
                parts.append(f"\n## {art_path}\n")
                parts.append(full_path.read_text())
            else:
                logger.info(f"  [{label}] Skipping inline: {art_path} (missing or >50KB)")
        parts.append("--- END INLINE ARTIFACTS ---")

    # Normalize outputs for save instructions
    outputs: dict = {}
    if expected_outputs:
        outputs = expected_outputs
    elif output_file:
        outputs = {"output": {"path": output_file, "format": "json"}}

    # Add save instructions for each output
    for out_name, out_def in outputs.items():
        out_path = out_def["path"]
        if out_path not in prompt:
            parts.append("")
            parts.append(f'Save your output for "{out_name}" to {out_path}')

    return "\n".join(parts)


def _build_command(
    abs_workdir: str,
    disallowed_tools: Optional[list],
    model: Optional[str],
    max_budget_usd: Optional[float],
    mcp_config: Optional[list],
    debug: bool,
    workdir: Path,
    step_label: str,
) -> list[str]:
    """Build the claude CLI command line."""
    cmd = [
        "claude",
        "--dangerously-skip-permissions",
        "--verbose",
        "--output-format", "stream-json",
        "-d", abs_workdir,
        "-p",
    ]

    if disallowed_tools:
        cmd.extend(["--disallowedTools", ",".join(disallowed_tools)])

    if model:
        cmd.extend(["--model", model])

    if max_budget_usd is not None:
        cmd.extend(["--max-budget-usd", str(max_budget_usd)])

    for config_path in (mcp_config or []):
        cmd.extend(["--mcp-config", config_path])

    if debug:
        debug_log = (workdir / f"{step_label}_debug.log").resolve()
        cmd.extend(["--debug-file", str(debug_log)])

    return cmd


async def _consume_stream(
    proc: asyncio.subprocess.Process,
    stream_f,
    tools_f,
    label: str,
    stream_to_stdout: bool,
    stream_prefix: Optional[str],
) -> None:
    """Read and parse the stream-json output from the Claude process."""
    def _emit(text: str) -> None:
        """Write to stream log and optionally to stdout."""
        stream_f.write(text)
        stream_f.flush()
        if stream_to_stdout:
            if stream_prefix:
                for line in text.splitlines(keepends=True):
                    sys.stdout.write(f"{stream_prefix} {line}")
            else:
                sys.stdout.write(text)
            sys.stdout.flush()

    try:
        assert proc.stdout is not None
        async for line_bytes in proc.stdout:
            try:
                line = line_bytes.decode().strip()
                if not line:
                    continue
                msg = json.loads(line)
                msg_type = msg.get("type")

                content = []
                if msg_type in ("assistant", "user"):
                    content = msg.get("message", {}).get("content", [])
                if not isinstance(content, list):
                    content = []

                for item in content:
                    item_type = item.get("type")
                    if item_type == "text":
                        _emit(item["text"] + "\n")
                    elif item_type == "thinking":
                        _emit(f"[thinking] {item['thinking']}\n")
                    elif item_type == "tool_use":
                        _emit(
                            f"[tool_use] {item.get('name')} "
                            f"{json.dumps(item.get('input', {}), indent=2)}\n"
                        )
                        entry = {
                            "ts": datetime.now().isoformat(),
                            "event": "PreToolUse",
                            "task": label,
                            "tool": item.get("name"),
                            "input": item.get("input"),
                        }
                        tools_f.write(json.dumps(entry) + "\n")
                        tools_f.flush()
                    elif item_type == "tool_result":
                        output = item.get("content", "")
                        if isinstance(output, str) and len(output) > 2000:
                            output = output[:2000] + "...(truncated)"
                        _emit(f"[tool_result] {output}\n")
                        entry = {
                            "ts": datetime.now().isoformat(),
                            "event": "PostToolUse",
                            "task": label,
                            "tool_use_id": item.get("tool_use_id"),
                            "output": output,
                        }
                        tools_f.write(json.dumps(entry) + "\n")
                        tools_f.flush()
            except Exception:
                pass  # Skip unparseable stream-json lines
    except Exception as e:
        logger.warning(f"  [{label}] Stream read error: {e}")


def _check_outputs(outputs: dict, workdir: Path, label: str, returncode: int | None) -> dict:
    """Verify expected output files exist and build the result dict.

    Pass/fail is determined solely by whether expected files exist, not by
    returncode — the Claude CLI may exit non-zero for transient reasons even
    when all outputs were successfully written.
    """
    missing = []
    empty = []
    for out_name, out_def in outputs.items():
        out_path_check = workdir / out_def["path"]
        if not out_path_check.exists():
            missing.append(out_def["path"])
        elif out_path_check.stat().st_size == 0:
            empty.append(out_def["path"])

    if missing:
        return {
            "status": "failed",
            "error": f"Missing output files: {', '.join(missing)}",
            "artifacts": [],
        }

    if empty:
        logger.info(f"  [{label}] Warning: empty output files: {', '.join(empty)}")

    # Build artifacts list for files that exist and are non-empty
    artifacts = []
    for name, odef in outputs.items():
        out_path_check = workdir / odef["path"]
        if out_path_check.exists() and out_path_check.stat().st_size > 0:
            artifacts.append(
                {"name": name, "path": odef["path"], "format": odef["format"]})

    logger.info(f"  [{label}] Complete (rc={returncode})")
    return {
        "status": "complete",
        "error": None,
        "artifacts": artifacts,
    }
