"""
Shared utility functions for stock research skills.

This module provides common functionality used across all research skills including:
- Logging setup
- Path handling
- Date formatting
- Input validation
- File operations
"""

import asyncio
import json
import os
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from dotenv import load_dotenv

from config import (
    WORK_DIR,
    ARTIFACTS_DIR,
    DATE_FORMAT_FILE,
    DATE_FORMAT_DISPLAY,
    DATE_FORMAT_ISO,
    LOG_FORMAT,
    LOG_DATE_FORMAT,
)


def load_environment() -> None:
    """Load .env from project root (skills/../.env), regardless of cwd."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(dotenv_path=env_path)


def setup_logging(
    name: str,
    level: str = 'INFO',
    log_file: Optional[Path] = None
) -> logging.Logger:
    """
    Set up logging for a skill.

    Args:
        name: Logger name (typically __name__)
        level: Log level ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')
        log_file: Optional path to log file

    Returns:
        Configured logger instance

    Example:
        >>> logger = setup_logging(__name__, 'INFO')
        >>> logger.info("Starting analysis")
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper()))

    # Remove existing handlers
    logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # File handler (if specified)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

    return logger


# Module-level logger (used by invoke_claude)
logger = setup_logging(__name__)


def default_workdir(symbol: str) -> str:
    """
    Return the default workdir path for a symbol: work/{SYMBOL}_{YYYYMMDD}.

    Does NOT create the directory — scripts already call ensure_directory.

    Args:
        symbol: Stock ticker symbol (uppercase).

    Returns:
        String path like 'work/TSLA_20260224'.
    """
    date_str = datetime.now().strftime(DATE_FORMAT_FILE)
    return f"{WORK_DIR}/{symbol}_{date_str}"


def create_work_directory(
    symbol: str,
    base_dir: Union[str, Path] = WORK_DIR,
    date: Optional[datetime] = None
) -> Path:
    """
    Create standardized work directory for a symbol.

    Args:
        symbol: Stock ticker symbol
        base_dir: Base directory for work directories
        date: Optional date for directory name (default: current date)

    Returns:
        Path to created work directory

    Example:
        >>> work_dir = create_work_directory('TSLA')
        >>> print(work_dir)
        work/TSLA_20260116
    """
    date_str = (date or datetime.now()).strftime(DATE_FORMAT_FILE)
    work_dir = Path(base_dir) / f"{symbol}_{date_str}"
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def validate_symbol(symbol: str) -> str:
    """
    Validate and normalize stock ticker symbol.

    Args:
        symbol: Raw ticker symbol input

    Returns:
        Normalized ticker symbol (uppercase, stripped)

    Raises:
        ValueError: If symbol is invalid

    Example:
        >>> validate_symbol("  tsla  ")
        'TSLA'
    """
    if not symbol or not isinstance(symbol, str):
        raise ValueError("Symbol must be a non-empty string")

    normalized = symbol.strip().upper()

    if not normalized:
        raise ValueError("Symbol cannot be empty")

    # Basic validation - allow alphanumeric and dots (for international tickers)
    if not all(c.isalnum() or c == '.' for c in normalized):
        raise ValueError(f"Invalid symbol format: {symbol}")

    return normalized


def format_currency(value: float, precision: int = 2) -> str:
    """
    Format value as currency string with appropriate suffix.

    Args:
        value: Numeric value to format
        precision: Decimal places to show (default: 2)

    Returns:
        Formatted currency string

    Example:
        >>> format_currency(1234567890)
        '$1.23B'
        >>> format_currency(5678901.23, precision=1)
        '$5.7M'
    """
    try:
        value = float(value)
    except (ValueError, TypeError):
        return 'N/A'

    if value >= 1e12:
        return f"${value/1e12:.{precision}f}T"
    elif value >= 1e9:
        return f"${value/1e9:.{precision}f}B"
    elif value >= 1e6:
        return f"${value/1e6:.{precision}f}M"
    elif value >= 1e3:
        return f"${value/1e3:.{precision}f}K"
    else:
        return f"${value:.{precision}f}"


def format_number(value: Union[int, float], precision: int = 0) -> str:
    """
    Format number with commas for thousands.

    Args:
        value: Numeric value to format
        precision: Decimal places to show (default: 0)

    Returns:
        Formatted number string

    Example:
        >>> format_number(1234567)
        '1,234,567'
        >>> format_number(1234.5678, precision=2)
        '1,234.57'
    """
    try:
        value = float(value)
    except (ValueError, TypeError):
        return 'N/A'

    if precision == 0:
        return f"{int(value):,}"
    else:
        return f"{value:,.{precision}f}"


def format_percentage(value: float, precision: int = 2) -> str:
    """
    Format decimal as percentage string.

    Args:
        value: Decimal value (e.g., 0.15 for 15%)
        precision: Decimal places to show (default: 2)

    Returns:
        Formatted percentage string

    Example:
        >>> format_percentage(0.1567)
        '15.67%'
        >>> format_percentage(0.05, precision=1)
        '5.0%'
    """
    try:
        value = float(value)
        return f"{value * 100:.{precision}f}%"
    except (ValueError, TypeError):
        return 'N/A'


def format_date(
    date: Union[str, datetime],
    format_type: str = 'display'
) -> str:
    """
    Format date according to specified format type.

    Args:
        date: Date string or datetime object
        format_type: Format type ('display', 'file', 'iso')

    Returns:
        Formatted date string

    Example:
        >>> from datetime import datetime
        >>> dt = datetime(2026, 1, 16)
        >>> format_date(dt, 'display')
        '2026-01-16 00:00:00'
        >>> format_date(dt, 'file')
        '20260116'
    """
    if isinstance(date, str):
        # Try to parse common date formats
        for fmt in ['%Y-%m-%d', '%Y%m%d', '%Y-%m-%d %H:%M:%S']:
            try:
                date = datetime.strptime(date, fmt)
                break
            except ValueError:
                continue

    if not isinstance(date, datetime):
        return str(date)

    formats = {
        'display': DATE_FORMAT_DISPLAY,
        'file': DATE_FORMAT_FILE,
        'iso': DATE_FORMAT_ISO,
    }

    return date.strftime(formats.get(format_type, DATE_FORMAT_DISPLAY))


def safe_get(
    data: dict,
    key: str,
    default: str = 'N/A',
    formatter: Optional[callable] = None
) -> str:
    """
    Safely get value from dictionary with optional formatting.

    Args:
        data: Dictionary to get value from
        key: Key to look up
        default: Default value if key not found or value is None
        formatter: Optional function to format the value

    Returns:
        Formatted value or default

    Example:
        >>> data = {'price': 123.45, 'name': 'Apple'}
        >>> safe_get(data, 'price', formatter=lambda x: f"${x:.2f}")
        '$123.45'
        >>> safe_get(data, 'missing')
        'N/A'
    """
    value = data.get(key)

    if value is None or value == 'N/A':
        return default

    if formatter:
        try:
            return formatter(value)
        except Exception:
            return default

    return str(value)


def ensure_directory(path: Union[str, Path]) -> Path:
    """
    Ensure directory exists, creating if necessary.

    Args:
        path: Directory path

    Returns:
        Path object for the directory

    Example:
        >>> output_dir = ensure_directory('work/TSLA_20260116/output')
        >>> output_dir.exists()
        True
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def print_section_header(title: str, width: int = 60) -> None:
    """
    Print a formatted section header.

    Args:
        title: Section title
        width: Width of the header line

    Example:
        >>> print_section_header("Data Analysis")
        ============================================================
        Data Analysis
        ============================================================
    """
    print("=" * width)
    print(title)
    print("=" * width)


def print_success(message: str) -> None:
    """Print success message with checkmark."""
    print(f"✓ {message}")


def print_error(message: str) -> None:
    """Print error message with X mark."""
    print(f"❌ {message}")


def print_warning(message: str) -> None:
    """Print warning message with warning symbol."""
    print(f"⚠ {message}")


def print_info(message: str) -> None:
    """Print info message with circle symbol."""
    print(f"⊘ {message}")


# ============================================================================
# Claude CLI invocation
# ============================================================================

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

    Returns:
        Dict with keys: status ("complete"|"failed"), error (str|None),
        artifacts (list of {name, path, format}).
    """
    abs_workdir = str(workdir.resolve())
    label = task_id or step_label

    # Build prompt
    parts = []
    if system:
        parts.append(system)
        parts.append("")

    inline_artifacts = artifacts_inline or []
    if inline_artifacts:
        parts.append(
            "Key artifacts are included inline below.")
        parts.append(
            "Additional files are in artifacts/ — use Read tool for larger files not included inline.")
    else:
        parts.append("All research data is in the artifacts/ subdirectory.")
        parts.append(
            "Read artifacts/manifest.json for a description of all available files.")

    # Inline artifacts
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

    parts.append("")
    parts.append("---")
    parts.append("")
    parts.append(prompt)

    # Normalize outputs: support both simple output_file and expected_outputs
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

    full_prompt = "\n".join(parts)

    # Build claude command
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

        # Stream and parse JSON output
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
                    pass
        except Exception:
            pass

        await proc.wait()

    # Check if expected output files were produced
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

    logger.info(f"  [{label}] Complete (rc={proc.returncode})")
    return {
        "status": "complete",
        "error": None,
        "artifacts": artifacts,
    }
