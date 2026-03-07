#!/usr/bin/env python3
"""
MCP caching proxy — wraps any MCP server with SQLite result cache.

Runs as a stdio MCP server. Internally connects to the real server.
Caches all tool call results in {MCP_CACHE_WORKDIR}/mcp-cache.db.

Usage (stdio transport):
    python mcp_proxy.py --transport stdio --command npx --args "-y,@pkg/server"

Usage (HTTP/SSE transport):
    python mcp_proxy.py --transport http --url https://api.example.com/mcp?key=KEY

Environment:
    MCP_CACHE_WORKDIR  Path to workdir — cache stored at {workdir}/mcp-cache.db
                       If unset, proxy passes through without caching.
"""
import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS mcp_cache (
  cache_key  TEXT PRIMARY KEY,
  server     TEXT NOT NULL,
  tool_name  TEXT NOT NULL,
  arguments  TEXT NOT NULL,
  result     TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


def make_cache_key(tool_name: str, arguments: dict) -> str:
    payload = tool_name + "|" + json.dumps(arguments, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def open_cache(workdir: str | None) -> sqlite3.Connection | None:
    if not workdir:
        return None
    path = Path(workdir) / "mcp-cache.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(CACHE_SCHEMA)
    conn.commit()
    return conn


async def run_proxy(args: argparse.Namespace) -> None:
    cache_conn = open_cache(os.environ.get("MCP_CACHE_WORKDIR"))
    server_label = args.command or args.url

    if args.transport == "stdio":
        cmd_args = args.args.split(",") if args.args else []
        server_params = StdioServerParameters(command=args.command, args=cmd_args)
        client_ctx = stdio_client(server_params)
    else:
        # HTTP/SSE transport — import here to avoid errors if not needed
        from mcp.client.sse import sse_client
        client_ctx = sse_client(args.url)

    async with client_ctx as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_response = await session.list_tools()
            available_tools = tools_response.tools

            proxy = Server("mcp-proxy")

            @proxy.list_tools()
            async def list_tools():
                return available_tools

            @proxy.call_tool()
            async def call_tool(name: str, arguments: dict | None = None):
                arguments = arguments or {}
                key = make_cache_key(name, arguments)

                if cache_conn:
                    row = cache_conn.execute(
                        "SELECT result FROM mcp_cache WHERE cache_key = ?", (key,)
                    ).fetchone()
                    if row:
                        return json.loads(row["result"])

                result = await session.call_tool(name, arguments)

                if cache_conn:
                    cache_conn.execute(
                        "INSERT OR REPLACE INTO mcp_cache VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            key, server_label, name,
                            json.dumps(arguments, sort_keys=True),
                            json.dumps(result, default=str),
                            datetime.now(timezone.utc).isoformat(),
                        )
                    )
                    cache_conn.commit()

                return result

            init_options = proxy.create_initialization_options()
            async with stdio_server() as (read_proxy, write_proxy):
                await proxy.run(read_proxy, write_proxy, init_options)


def main():
    parser = argparse.ArgumentParser(description="MCP caching proxy")
    parser.add_argument("--transport", choices=["stdio", "http"], required=True)
    parser.add_argument("--command", help="Command for stdio transport (e.g. npx)")
    parser.add_argument("--args", help="Comma-separated args for the command")
    parser.add_argument("--url", help="URL for HTTP/SSE transport")
    args = parser.parse_args()

    if args.transport == "stdio" and not args.command:
        parser.error("--command is required for stdio transport")
    if args.transport == "http" and not args.url:
        parser.error("--url is required for http transport")

    asyncio.run(run_proxy(args))


if __name__ == "__main__":
    main()
