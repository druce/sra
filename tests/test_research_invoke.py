"""Test that invoke_claude passes mcp_config flags and extra_env correctly."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "skills"))
sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_fake_proc():
    """Build a mock subprocess that matches asyncio.subprocess.Process interface."""
    proc = MagicMock()
    proc.returncode = 0

    # stdin: write() and close() are synchronous in asyncio.subprocess
    stdin = MagicMock()
    stdin.drain = AsyncMock()
    proc.stdin = stdin

    # stdout: async iterator that yields no lines
    async def _empty_aiter():
        return
        yield  # noqa: unreachable — makes this an async generator

    proc.stdout = _empty_aiter()
    proc.wait = AsyncMock()
    return proc


def test_invoke_claude_mcp_config_in_cmd(tmp_path):
    """mcp_config paths should appear as --mcp-config flags in claude command."""
    from utils import invoke_claude

    captured_cmd = []

    async def fake_exec(*cmd, **kwargs):
        captured_cmd.extend(cmd)
        return _make_fake_proc()

    # Create a dummy output file
    out_path = tmp_path / "artifacts" / "test.md"
    out_path.parent.mkdir(parents=True)
    out_path.write_text("content")

    async def run():
        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            await invoke_claude(
                prompt="test prompt",
                workdir=tmp_path,
                task_id="test_task",
                step_label="write",
                mcp_config=["mcp-research.json"],
                expected_outputs={"out": {"path": "artifacts/test.md", "format": "md"}},
            )

    asyncio.run(run())
    assert "--mcp-config" in captured_cmd
    idx = captured_cmd.index("--mcp-config")
    assert captured_cmd[idx + 1] == "mcp-research.json"


def test_invoke_claude_extra_env_passed(tmp_path):
    """extra_env should be merged into subprocess environment."""
    from utils import invoke_claude

    captured_env = {}

    async def fake_exec(*cmd, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return _make_fake_proc()

    out_path = tmp_path / "artifacts" / "test.md"
    out_path.parent.mkdir(parents=True)
    out_path.write_text("content")

    async def run():
        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            await invoke_claude(
                prompt="test prompt",
                workdir=tmp_path,
                task_id="test_task",
                step_label="write",
                extra_env={"MCP_CACHE_WORKDIR": "/tmp/test"},
                expected_outputs={"out": {"path": "artifacts/test.md", "format": "md"}},
            )

    asyncio.run(run())
    assert captured_env.get("MCP_CACHE_WORKDIR") == "/tmp/test"
