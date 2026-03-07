"""
Integration tests for mcp_proxy.py — one test per service.

Each test:
1. Creates a temp workdir with MCP_CACHE_WORKDIR set
2. Starts proxy subprocess, makes one tool call
3. Verifies mcp-cache.db has 1 row, result is non-empty
4. Makes identical call again
5. Verifies mcp-cache.db still has 1 row (cache hit, no new insert)
6. Verifies result matches

Run with: uv run pytest tests/test_mcp_proxy.py -m integration -v
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

CWD = str(Path(__file__).parent.parent)
PROXY = ["uv", "run", "python", "skills/mcp_proxy/mcp_proxy.py"]


def call_via_proxy(proxy_args: list[str], tool_name: str, arguments: dict, workdir: str) -> dict:
    """Start proxy, send a single tool call, return result."""
    env = {**os.environ, "MCP_CACHE_WORKDIR": workdir}
    harness = Path(CWD) / "tests" / "_proxy_harness.py"
    result = subprocess.run(
        ["uv", "run", "python", str(harness),
         "--proxy-args", json.dumps(proxy_args),
         "--tool", tool_name,
         "--arguments", json.dumps(arguments)],
        capture_output=True, text=True, cwd=CWD, env=env, timeout=60
    )
    assert result.returncode == 0, f"Harness failed: {result.stderr}"
    return json.loads(result.stdout)


def cache_row_count(workdir: str) -> int:
    db_path = Path(workdir) / "mcp-cache.db"
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(str(db_path))
    count = conn.execute("SELECT COUNT(*) FROM mcp_cache").fetchone()[0]
    conn.close()
    return count


@pytest.mark.integration
def test_yfinance_cache(tmp_path):
    """yfinance stdio transport — get_stock_info for AAPL."""
    workdir = str(tmp_path)
    proxy_args = ["--transport", "stdio", "--command", "uvx", "--args", "yfinance-mcp"]

    result1 = call_via_proxy(proxy_args, "get_stock_info", {"symbol": "AAPL"}, workdir)
    assert result1
    assert cache_row_count(workdir) == 1

    result2 = call_via_proxy(proxy_args, "get_stock_info", {"symbol": "AAPL"}, workdir)
    assert cache_row_count(workdir) == 1
    assert result1 == result2


@pytest.mark.integration
def test_wikipedia_cache(tmp_path):
    """wikipedia stdio transport — get_article for Apple Inc."""
    workdir = str(tmp_path)
    proxy_args = ["--transport", "stdio", "--command", "uvx", "--args", "wikipedia-mcp"]

    result1 = call_via_proxy(proxy_args, "get_article", {"title": "Apple Inc."}, workdir)
    assert result1
    assert cache_row_count(workdir) == 1

    result2 = call_via_proxy(proxy_args, "get_article", {"title": "Apple Inc."}, workdir)
    assert cache_row_count(workdir) == 1
    assert result1 == result2


@pytest.mark.integration
def test_perplexity_cache(tmp_path):
    """perplexity-ask stdio/npx — simple factual query."""
    workdir = str(tmp_path)
    proxy_args = ["--transport", "stdio", "--command", "npx",
                  "--args", "-y,@anthropic-ai/mcp-server-perplexity"]

    result1 = call_via_proxy(proxy_args, "ask", {"query": "What year was Apple founded?"}, workdir)
    assert result1
    assert cache_row_count(workdir) == 1

    result2 = call_via_proxy(proxy_args, "ask", {"query": "What year was Apple founded?"}, workdir)
    assert cache_row_count(workdir) == 1
    assert result1 == result2


@pytest.mark.integration
def test_brave_search_cache(tmp_path):
    """brave-search stdio/npx — company news query."""
    workdir = str(tmp_path)
    proxy_args = ["--transport", "stdio", "--command", "npx",
                  "--args", "-y,@modelcontextprotocol/server-brave-search"]

    result1 = call_via_proxy(proxy_args, "brave_web_search",
                              {"query": "Apple Inc earnings 2024"}, workdir)
    assert result1
    assert cache_row_count(workdir) == 1

    result2 = call_via_proxy(proxy_args, "brave_web_search",
                              {"query": "Apple Inc earnings 2024"}, workdir)
    assert cache_row_count(workdir) == 1
    assert result1 == result2


@pytest.mark.integration
def test_alphavantage_cache(tmp_path):
    """alphavantage stdio — TIME_SERIES_DAILY for AAPL."""
    workdir = str(tmp_path)
    proxy_args = ["--transport", "stdio", "--command", "uvx",
                  "--args", "alphavantage-mcp"]

    result1 = call_via_proxy(proxy_args, "TIME_SERIES_DAILY",
                              {"symbol": "AAPL", "outputsize": "compact"}, workdir)
    assert result1
    assert cache_row_count(workdir) == 1

    result2 = call_via_proxy(proxy_args, "TIME_SERIES_DAILY",
                              {"symbol": "AAPL", "outputsize": "compact"}, workdir)
    assert cache_row_count(workdir) == 1
    assert result1 == result2


@pytest.mark.integration
def test_openbb_cache(tmp_path):
    """openbb-mcp stdio — equity quote for AAPL."""
    workdir = str(tmp_path)
    proxy_args = ["--transport", "stdio", "--command", "uvx", "--args", "openbb-mcp"]

    result1 = call_via_proxy(proxy_args, "equity_quote", {"symbol": "AAPL"}, workdir)
    assert result1
    assert cache_row_count(workdir) == 1

    result2 = call_via_proxy(proxy_args, "equity_quote", {"symbol": "AAPL"}, workdir)
    assert cache_row_count(workdir) == 1
    assert result1 == result2


@pytest.mark.integration
def test_fmp_cache(tmp_path):
    """FMP HTTP transport — quote for AAPL."""
    import dotenv
    dotenv.load_dotenv()
    api_key = os.environ.get("FMP_API_KEY", "")
    if not api_key:
        pytest.skip("FMP test requires FMP_API_KEY")

    workdir = str(tmp_path)
    proxy_args = ["--transport", "http", "--url", f"https://financialmodelingprep.com/mcp?apikey={api_key}"]

    result1 = call_via_proxy(proxy_args, "quote", {"symbol": "AAPL"}, workdir)
    assert result1
    assert cache_row_count(workdir) == 1

    result2 = call_via_proxy(proxy_args, "quote", {"symbol": "AAPL"}, workdir)
    assert cache_row_count(workdir) == 1
    assert result1 == result2
