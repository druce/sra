#!/usr/bin/env python3
"""
Chunk and embed research findings markdown files for the knowledge base.

Part of the post-research pipeline: chunk_research → tag_research → append_index.

Reads knowledge/findings_*.md files written by research agents, splits them into
paragraph-boundary chunks, assigns a primary tag derived from the filename
(e.g. findings_profile.md → "profile"), and embeds via OpenAI.

Usage:
    ./skills/chunk_index/chunk_research.py SYMBOL --workdir DIR

Output (stdout):  JSON manifest {"status": "complete", "artifacts": [...]}
Output (files):   lancedb/research_chunks.json
"""
import argparse
import json
import sys
from pathlib import Path

_SKILLS_DIR = Path(__file__).resolve().parent.parent
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

from chunk_index.chunk_documents import chunk_text, embed_chunks  # noqa: E402
from utils import setup_logging, load_environment  # noqa: E402

load_environment()
logger = setup_logging(__name__)


def read_research_findings(workdir: Path) -> list[dict]:
    """Read knowledge/findings_*.md files and convert to chunks.

    Each file's primary tag is derived from its filename:
      findings_profile.md   → tag "profile"
      findings_risk_news.md → tag "risk_news"

    Returns list of chunk dicts with id, text, source, doc_type, and primary_tag.
    """
    knowledge_dir = workdir / "knowledge"
    if not knowledge_dir.exists():
        logger.info("No knowledge directory found, skipping")
        return []

    all_chunks: list[dict] = []
    for md_file in sorted(knowledge_dir.glob("findings_*.md")):
        text = md_file.read_text(encoding="utf-8", errors="ignore")
        if not text.strip():
            continue

        # Derive primary tag from filename: findings_profile.md → profile
        tag = md_file.stem.removeprefix("findings_")
        source = f"knowledge/{md_file.name}"

        sub_chunks = chunk_text(text, source)
        for idx, chunk in enumerate(sub_chunks):
            chunk["id"] = f"findings_{tag}_{idx:04d}"
            chunk["primary_tag"] = tag
            chunk["doc_type"] = "research_finding"
        all_chunks.extend(sub_chunks)

    logger.info(
        f"Read {len(all_chunks)} chunks from "
        f"{len(list(knowledge_dir.glob('findings_*.md')))} findings files"
    )
    return all_chunks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker")
    parser.add_argument("--workdir", required=True)
    args = parser.parse_args()

    workdir = Path(args.workdir)
    chunks = read_research_findings(workdir)

    if not chunks:
        logger.info("No research findings to chunk")
        print(json.dumps({
            "status": "complete",
            "artifacts": [],
            "error": None,
        }))
        return 0

    # Embed all chunks
    from openai import OpenAI
    client = OpenAI()
    chunks = embed_chunks(chunks, client)

    # Write chunks JSON for tag_research to read
    lancedb_dir = workdir / "lancedb"
    lancedb_dir.mkdir(parents=True, exist_ok=True)
    out_path = lancedb_dir / "research_chunks.json"
    out_path.write_text(json.dumps(chunks, indent=2))
    logger.info(f"Wrote {len(chunks)} research chunks to {out_path}")

    print(json.dumps({
        "status": "complete",
        "artifacts": [{"name": "chunks", "path": "lancedb/research_chunks.json", "format": "json"}],
        "error": None,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
