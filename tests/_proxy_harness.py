#!/usr/bin/env python3
"""
Test harness for mcp_proxy.py integration tests.
Starts the proxy as a subprocess, sends one tool call, prints result to stdout.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "skills"))

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--proxy-args", required=True)
    parser.add_argument("--tool", required=True)
    parser.add_argument("--arguments", required=True)
    args = parser.parse_args()

    proxy_args_list = json.loads(args.proxy_args)
    tool_name = args.tool
    tool_arguments = json.loads(args.arguments)

    proxy_params = StdioServerParameters(
        command="uv",
        args=["run", "python", "skills/mcp_proxy/mcp_proxy.py"] + proxy_args_list,
    )

    async with stdio_client(proxy_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, tool_arguments)
            print(json.dumps(result, default=str))


if __name__ == "__main__":
    asyncio.run(main())
