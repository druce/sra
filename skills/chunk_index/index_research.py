#!/usr/bin/env python3
"""
Index MCP cache responses and research findings into the existing LanceDB index.

Runs after the research wave completes. Reads two sources:
  1. MCP cache (mcp-cache.db) — tool call results from research agents
  2. Research findings (research.db) — structured findings recorded by agents

Chunks, embeds, tags, and appends everything to the existing LanceDB index
at artifacts/index/ so writers can query a single unified source.

Usage:
    ./skills/chunk_index/index_research.py SYMBOL --workdir DIR

Output (stdout):  JSON manifest {"status": "complete", "artifacts": [...]}
"""
import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

_SKILLS_DIR = Path(__file__).resolve().parent.parent
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

from chunk_index.chunk_documents import chunk_text, embed_chunks  # noqa: E402
from utils import setup_logging, load_environment  # noqa: E402

load_environment()
logger = setup_logging(__name__)

# Map research task IDs to section tags for MCP chunk tagging
TASK_TO_SECTION = {
    "research_profile": ["profile"],
    "research_business": ["business_model"],
    "research_competitive": ["competitive"],
    "research_supply_chain": ["supply_chain"],
    "research_financial": ["financial"],
    "research_valuation": ["valuation"],
    "research_risk_news": ["risk_news"],
}

# Minimum prose length to index an MCP response (skip short numeric results)
MIN_TEXT_LENGTH = 50


def extract_text_from_result(result_json: str) -> str:
    """Extract readable text from an MCP tool result JSON string."""
    try:
        result = json.loads(result_json)
    except (json.JSONDecodeError, TypeError):
        return ""

    # MCP results can be a list of content blocks or a dict with content
    texts = []
    if isinstance(result, dict):
        content = result.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(block.get("text", ""))
        elif isinstance(content, str):
            texts.append(content)
        # Also handle direct text field
        if not texts and "text" in result:
            texts.append(result["text"])
    elif isinstance(result, list):
        for block in result:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))

    return "\n\n".join(t for t in texts if t)


def tags_from_requestors(requestors: list[str]) -> list[str]:
    """Derive section tags from the union of all requestor task IDs."""
    tags = set()
    for task_id in requestors:
        if task_id in TASK_TO_SECTION:
            tags.update(TASK_TO_SECTION[task_id])
    return sorted(tags) if tags else ["research"]


def read_mcp_cache(workdir: Path) -> list[dict]:
    """Read MCP cache entries and convert to chunks."""
    cache_path = workdir / "mcp-cache.db"
    if not cache_path.exists():
        logger.info("No MCP cache found, skipping")
        return []

    conn = sqlite3.connect(str(cache_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT cache_key, tool_name, result, requestors FROM mcp_cache"
    ).fetchall()
    conn.close()

    all_chunks = []
    seen_keys = set()

    for row in rows:
        cache_key = row["cache_key"]
        if cache_key in seen_keys:
            continue
        seen_keys.add(cache_key)

        text = extract_text_from_result(row["result"])
        if len(text) < MIN_TEXT_LENGTH:
            continue

        try:
            requestors = json.loads(row["requestors"])
        except (json.JSONDecodeError, TypeError):
            requestors = []
        tags = tags_from_requestors(requestors)
        source = f"mcp:{row['tool_name']}"
        key_hash = hashlib.sha256(cache_key.encode()).hexdigest()[:6]

        # Chunk longer responses, use short ones directly
        sub_chunks = chunk_text(text, source)
        for idx, chunk in enumerate(sub_chunks):
            chunk["id"] = f"mcp_{key_hash}_{idx:04d}"
            chunk["tags"] = json.dumps(tags)
            chunk["doc_type"] = "mcp_research"
        all_chunks.extend(sub_chunks)

    logger.info(f"Read {len(all_chunks)} chunks from MCP cache ({len(rows)} entries)")
    return all_chunks


def read_findings(workdir: Path) -> list[dict]:
    """Read research findings and convert to chunks."""
    db_path = workdir / "research.db"
    if not db_path.exists():
        logger.info("No research.db found, skipping findings")
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, task_id, content, source, tags FROM research_findings"
    ).fetchall()
    conn.close()

    chunks = []
    for row in rows:
        content = row["content"]
        if not content or len(content) < MIN_TEXT_LENGTH:
            continue

        finding_id = str(row["id"])[:8]
        source = row["source"] or f"finding:{row['task_id']}"
        tags = row["tags"]  # Already JSON-encoded from db.py

        chunks.append({
            "id": f"finding_{finding_id}",
            "text": content,
            "source": source,
            "doc_type": "research_finding",
            "tags": tags,
        })

    logger.info(f"Read {len(chunks)} chunks from research findings ({len(rows)} rows)")
    return chunks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker")
    parser.add_argument("--workdir", required=True)
    args = parser.parse_args()

    workdir = Path(args.workdir)
    index_dir = workdir / "lancedb" / "index"

    if not index_dir.exists():
        print(json.dumps({
            "status": "failed",
            "error": "LanceDB index not found at lancedb/index/",
            "artifacts": [],
        }))
        return 1

    # Collect chunks from both sources
    mcp_chunks = read_mcp_cache(workdir)
    finding_chunks = read_findings(workdir)
    all_chunks = mcp_chunks + finding_chunks

    if not all_chunks:
        logger.info("No research content to index, completing with empty result")
        print(json.dumps({
            "status": "complete",
            "artifacts": [{"name": "research_index", "path": "lancedb/index/", "format": "lancedb"}],
            "error": None,
        }))
        return 0

    # Embed all new chunks
    from openai import OpenAI
    client = OpenAI()
    all_chunks = embed_chunks(all_chunks, client)

    # Convert to records matching the existing LanceDB schema
    records = []
    for c in all_chunks:
        records.append({
            "id": c["id"],
            "text": c["text"],
            "source": c["source"],
            "doc_type": c.get("doc_type", "other"),
            "tags": c["tags"],
            "vector": [float(x) for x in c["embedding"]],
        })

    # Append to existing LanceDB table
    import lancedb
    from chunk_index import CHUNKS_SCHEMA

    db = lancedb.connect(str(index_dir))
    if "chunks" not in db.table_names():
        # Create table if it doesn't exist (shouldn't happen in normal flow)
        table = db.create_table("chunks", data=records, schema=CHUNKS_SCHEMA)
    else:
        table = db.open_table("chunks")
        table.add(records)

    # Rebuild FTS index to include new chunks
    table.create_fts_index("text", replace=True)
    logger.info(f"Appended {len(records)} research chunks to index")

    print(json.dumps({
        "status": "complete",
        "artifacts": [{"name": "research_index", "path": "lancedb/index/", "format": "lancedb"}],
        "error": None,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
