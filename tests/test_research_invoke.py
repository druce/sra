"""Test that _invoke_claude passes mcp_config flags correctly."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_invoke_claude_mcp_config_in_cmd(tmp_path):
    """mcp_config paths should appear as --mcp-config flags in claude command."""
    from research import _invoke_claude

    captured_cmd = []

    async def fake_exec(*cmd, **kwargs):
        captured_cmd.extend(cmd)
        proc = MagicMock()
        proc.returncode = 0
        proc.stdin = AsyncMock()
        proc.stdout = AsyncMock()
        proc.stdout.__aiter__ = AsyncMock(return_value=iter([]))
        proc.wait = AsyncMock()
        return proc

    # Create a dummy output file
    out_path = tmp_path / "artifacts" / "test.md"
    out_path.parent.mkdir(parents=True)
    out_path.write_text("content")

    async def run():
        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            await _invoke_claude(
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
    from research import _invoke_claude

    captured_env = {}

    async def fake_exec(*cmd, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        proc = MagicMock()
        proc.returncode = 0
        proc.stdin = AsyncMock()
        proc.stdout = AsyncMock()
        proc.stdout.__aiter__ = AsyncMock(return_value=iter([]))
        proc.wait = AsyncMock()
        return proc

    out_path = tmp_path / "artifacts" / "test.md"
    out_path.parent.mkdir(parents=True)
    out_path.write_text("content")

    async def run():
        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            await _invoke_claude(
                prompt="test prompt",
                workdir=tmp_path,
                task_id="test_task",
                step_label="write",
                extra_env={"MCP_CACHE_WORKDIR": "/tmp/test"},
                expected_outputs={"out": {"path": "artifacts/test.md", "format": "md"}},
            )

    asyncio.run(run())
    assert captured_env.get("MCP_CACHE_WORKDIR") == "/tmp/test"
