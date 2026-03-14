#!/usr/bin/env python3
"""
Custom Research — Run user-provided investigation prompts via Claude.

Reads custom_prompts.json from the workdir, runs each prompt as a parallel
Claude subprocess with web search, auto-tags each response for section
relevance, and emits a JSON manifest on stdout.

Usage:
    ./skills/custom_research/custom_research.py SYMBOL --workdir DIR

Exit codes:
    0  All prompts succeeded (or no prompts to run)
    1  Partial success (at least one prompt succeeded)
    2  Complete failure
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

_SKILLS_DIR = Path(__file__).resolve().parent.parent
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

from utils import (  # noqa: E402
    setup_logging,
    validate_symbol,
    ensure_directory,
    load_environment,
    default_workdir,
    invoke_claude,
    resolve_company_name,
)

load_environment()
logger = setup_logging(__name__)


SECTION_TAGS = [
    "profile", "business_model", "competitive",
    "supply_chain", "financial", "valuation", "risk_news",
]

SYSTEM_PROMPT = (
    "You are a financial research analyst investigating a public company. "
    "Use web search to find current, factual data. Cite sources with specific "
    "numbers, dates, and data points. Write in Markdown. "
    "Save your output to the file path specified at the end of the prompt."
)

TAG_PROMPT_TEMPLATE = """Read the file at {response_path} and classify which of these report sections it is relevant to:

{tags_list}

A response may be relevant to multiple sections. Be generous — it's better to over-tag than under-tag.

Return ONLY a JSON array of matching tag strings, e.g.: ["financial", "risk_news", "valuation"]

Write the result to {tag_output_path}
"""


def _get_mcp_config(workdir: Path) -> list[str] | None:
    """Return MCP config filenames if mcp-research.json exists in workdir.

    Returns relative filenames (not absolute paths) because invoke_claude
    runs with cwd=workdir and passes these via --mcp-config.
    """
    mcp_path = workdir / "mcp-research.json"
    if mcp_path.exists():
        return ["mcp-research.json"]
    return None


def get_company_name(symbol: str, workdir: Path) -> str:
    """Resolve company name from profile.json, yfinance, or symbol."""
    return resolve_company_name(symbol, workdir, yfinance_fallback=True)


async def run_prompt(
    idx: int,
    prompt_text: str,
    company: str,
    symbol: str,
    workdir: Path,
) -> dict:
    """Run a single custom prompt via invoke_claude. Return result dict."""
    prompt_id = f"custom_{idx}"
    output_path = f"knowledge/custom_research_{idx}.md"

    full_prompt = (
        f"Research the following question about {company} ({symbol}):\n\n"
        f"{prompt_text}\n\n"
        "Be thorough. Include specific data, dates, and sources."
    )

    result = await invoke_claude(
        prompt=full_prompt,
        workdir=workdir,
        task_id="custom_research",
        step_label=f"prompt_{idx}",
        system=SYSTEM_PROMPT,
        expected_outputs={
            prompt_id: {"path": output_path, "format": "md"},
        },
        mcp_config=_get_mcp_config(workdir),
    )

    return {
        "idx": idx,
        "prompt_id": prompt_id,
        "output_path": output_path,
        "status": result["status"],
        "error": result.get("error"),
        "artifacts": result.get("artifacts", []),
    }


async def tag_response(idx: int, workdir: Path) -> list[str]:
    """Auto-tag a custom research response for section relevance. Return list of tags."""
    response_path = f"knowledge/custom_research_{idx}.md"
    tag_output_path = f"artifacts/custom_research_{idx}_tags.json"
    tags_list = "\n".join(f"- {t}" for t in SECTION_TAGS)

    prompt = TAG_PROMPT_TEMPLATE.format(
        response_path=response_path,
        tags_list=tags_list,
        tag_output_path=tag_output_path,
    )

    result = await invoke_claude(
        prompt=prompt,
        workdir=workdir,
        task_id="custom_research",
        step_label=f"tag_{idx}",
        disallowed_tools=["WebSearch", "WebFetch"],
        expected_outputs={
            f"tags_{idx}": {"path": tag_output_path, "format": "json"},
        },
    )

    if result["status"] == "complete":
        tag_file = workdir / tag_output_path
        if tag_file.exists():
            try:
                tags = json.loads(tag_file.read_text())
                if isinstance(tags, list):
                    return [t for t in tags if t in SECTION_TAGS]
            except (json.JSONDecodeError, OSError):
                pass

    return []


async def run_all(symbol: str, workdir: Path) -> int:
    """Main async entry point. Returns exit code."""
    workdir = Path(workdir)
    ensure_directory(workdir / "artifacts")
    ensure_directory(workdir / "knowledge")

    # Read custom prompts
    prompts_file = workdir / "custom_prompts.json"
    if not prompts_file.exists():
        logger.info("No custom_prompts.json found — skipping")
        print(json.dumps({"status": "complete", "artifacts": [], "error": None}))
        return 0

    prompts = json.loads(prompts_file.read_text())
    if not prompts:
        logger.info("custom_prompts.json is empty — skipping")
        print(json.dumps({"status": "complete", "artifacts": [], "error": None}))
        return 0

    company = get_company_name(symbol, workdir)
    logger.info("Running %d custom prompts for %s (%s)", len(prompts), company, symbol)

    # Phase 1: Run all prompts in parallel
    coros = [
        run_prompt(i + 1, p["prompt"], company, symbol, workdir)
        for i, p in enumerate(prompts)
    ]
    results = await asyncio.gather(*coros)

    succeeded = [r for r in results if r["status"] == "complete"]
    failed = [r for r in results if r["status"] != "complete"]

    for f in failed:
        logger.error("Prompt %d failed: %s", f["idx"], f["error"])

    if not succeeded:
        logger.error("All %d custom prompts failed", len(prompts))
        print(json.dumps({
            "status": "failed",
            "artifacts": [],
            "error": f"All {len(prompts)} custom prompts failed",
        }))
        return 2

    # Phase 2: Auto-tag all successful responses in parallel
    logger.info("Auto-tagging %d responses", len(succeeded))
    tag_coros = [tag_response(r["idx"], workdir) for r in succeeded]
    tag_results = await asyncio.gather(*tag_coros)

    # Build combined tags metadata
    tags_metadata = []
    all_artifacts = []
    variables = {}

    for result, tags in zip(succeeded, tag_results):
        tags_metadata.append({
            "id": result["prompt_id"],
            "file": result["output_path"],
            "tags": tags,
        })
        # Extract title from the first heading in the .md file for description
        md_path = workdir / result["output_path"]
        title = None
        if md_path.exists():
            for line in md_path.read_text().splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    title = stripped.lstrip("#").strip()
                    break
        for art in result["artifacts"]:
            if title:
                art["description"] = title
        if title:
            variables[f"custom_research_{result['idx']}_desc"] = title
        all_artifacts.extend(result["artifacts"])

    # Write combined tags file
    tags_file = workdir / "artifacts" / "custom_research_tags.json"
    tags_file.write_text(json.dumps(tags_metadata, indent=2))
    all_artifacts.append({
        "name": "custom_research_tags",
        "path": "artifacts/custom_research_tags.json",
        "format": "json",
        "description": "Section relevance tags for each custom research response",
    })

    # Clean up individual tag files
    for r in succeeded:
        tag_file = workdir / f"artifacts/custom_research_{r['idx']}_tags.json"
        if tag_file.exists():
            tag_file.unlink()

    logger.info(
        "Custom research complete: %d/%d succeeded",
        len(succeeded), len(prompts),
    )

    print(json.dumps({
        "status": "complete",
        "artifacts": all_artifacts,
        "variables": variables,
        "error": None,
    }))

    if failed:
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run custom investigation prompts")
    parser.add_argument("ticker", help="Stock ticker symbol")
    parser.add_argument("--workdir", default=None, help="Working directory")
    args = parser.parse_args()

    symbol = validate_symbol(args.ticker)
    workdir = args.workdir or default_workdir(symbol)

    return asyncio.run(run_all(symbol, Path(workdir)))


if __name__ == "__main__":
    sys.exit(main())
