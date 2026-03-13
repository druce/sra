#!/usr/bin/env python3
"""
Append tagged research findings to the existing LanceDB index.

Part of the post-research pipeline: chunk_research → tag_research → append_index.

Reads research_chunks.json (chunks + embeddings) and research_chunk_tags.json
(cross-tags from Claude), merges them, and appends to the existing LanceDB
chunks table. Rebuilds the FTS index to include the new chunks.

Usage:
    ./skills/chunk_index/append_index.py SYMBOL --workdir DIR

Output (stdout):  JSON manifest {"status": "complete", "artifacts": [...]}
"""
import argparse
import json
import sys
from pathlib import Path

_SKILLS_DIR = Path(__file__).resolve().parent.parent
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

from utils import setup_logging  # noqa: E402

logger = setup_logging(__name__)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker")
    parser.add_argument("--workdir", required=True)
    args = parser.parse_args()

    workdir = Path(args.workdir)
    lancedb_dir = workdir / "lancedb"
    index_dir = lancedb_dir / "index"

    chunks_path = lancedb_dir / "research_chunks.json"
    tags_path = lancedb_dir / "research_chunk_tags.json"

    if not index_dir.exists():
        print(json.dumps({
            "status": "failed",
            "error": "LanceDB index not found at lancedb/index/",
            "artifacts": [],
        }))
        return 1

    if not chunks_path.exists():
        # No research chunks — nothing to append (not an error)
        logger.info("No research_chunks.json found, completing with empty result")
        print(json.dumps({
            "status": "complete",
            "artifacts": [{"name": "research_index", "path": "lancedb/index/", "format": "lancedb"}],
            "error": None,
        }))
        return 0

    chunks = json.loads(chunks_path.read_text())

    if not chunks:
        logger.info("No chunks to append")
        print(json.dumps({
            "status": "complete",
            "artifacts": [{"name": "research_index", "path": "lancedb/index/", "format": "lancedb"}],
            "error": None,
        }))
        return 0

    # Merge cross-tags from tag_research
    if tags_path.exists():
        tags_list = json.loads(tags_path.read_text())
        tags_map = {t["id"]: t["tags"] for t in tags_list}
    else:
        tags_map = {}

    for c in chunks:
        if c["id"] in tags_map:
            c["tags"] = json.dumps(tags_map[c["id"]])
        else:
            # Fall back to primary tag from chunk_research
            c["tags"] = json.dumps([c.get("primary_tag", "research")])

    # Convert to records matching LanceDB schema
    records = []
    for c in chunks:
        records.append({
            "id": c["id"],
            "text": c["text"],
            "source": c["source"],
            "doc_type": c.get("doc_type", "research_finding"),
            "tags": c["tags"],
            "vector": [float(x) for x in c["embedding"]],
        })

    # Append to existing LanceDB table
    import lancedb
    from chunk_index import CHUNKS_SCHEMA

    db = lancedb.connect(str(index_dir))
    if "chunks" not in db.table_names():
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
