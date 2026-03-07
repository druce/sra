#!/usr/bin/env python3
"""
Generate MCP config files from Claude Desktop config.

Reads ~/Library/Application Support/Claude/claude_desktop_config.json and produces:
  .mcp.json          — coding profile: context7, playwright, filesystem (direct)
  mcp-research.json  — research profile: all finance/research servers via mcp_proxy.py

Usage:
    python scripts/gen_mcp_configs.py [--dry-run]
"""
import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DESKTOP_CONFIG = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
PROXY_SCRIPT = str(PROJECT_ROOT / "skills" / "mcp_proxy" / "mcp_proxy.py")

# Servers to use directly in coding sessions (no proxy, no cache)
CODING_SERVERS = {"context7", "playwright", "filesystem"}


def wrap_with_proxy(name: str, server_def: dict) -> dict:
    """Wrap a server definition with mcp_proxy.py."""
    if "url" in server_def:
        # HTTP/SSE transport
        return {
            "command": "uv",
            "args": [
                "run", "python", PROXY_SCRIPT,
                "--transport", "http",
                "--url", server_def["url"],
            ]
        }
    else:
        # stdio transport
        real_cmd = server_def.get("command", "")
        real_args = server_def.get("args", [])
        args_str = ",".join(str(a) for a in real_args)
        proxy_args = [
            "run", "python", PROXY_SCRIPT,
            "--transport", "stdio",
            "--command", real_cmd,
        ]
        if args_str:
            proxy_args += ["--args", args_str]
        result = {"command": "uv", "args": proxy_args}
        if "env" in server_def:
            result["env"] = server_def["env"]
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not DESKTOP_CONFIG.exists():
        print(f"ERROR: Claude Desktop config not found at {DESKTOP_CONFIG}", file=sys.stderr)
        sys.exit(1)

    config = json.loads(DESKTOP_CONFIG.read_text())
    all_servers = config.get("mcpServers", {})

    coding_mcp = {"mcpServers": {}}
    research_mcp = {"mcpServers": {}}

    for name, definition in all_servers.items():
        if name in CODING_SERVERS:
            coding_mcp["mcpServers"][name] = definition
        else:
            research_mcp["mcpServers"][name] = wrap_with_proxy(name, definition)

    mcp_json_path = PROJECT_ROOT / ".mcp.json"
    research_json_path = PROJECT_ROOT / "mcp-research.json"

    if args.dry_run:
        print("=== .mcp.json ===")
        print(json.dumps(coding_mcp, indent=2))
        print("\n=== mcp-research.json ===")
        print(json.dumps(research_mcp, indent=2))
    else:
        mcp_json_path.write_text(json.dumps(coding_mcp, indent=2))
        research_json_path.write_text(json.dumps(research_mcp, indent=2))
        print(f"Written: {mcp_json_path}")
        print(f"Written: {research_json_path}")


if __name__ == "__main__":
    main()
