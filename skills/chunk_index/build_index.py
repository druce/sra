#!/usr/bin/env python3
"""
Build LanceDB hybrid index from chunks.json + chunk_tags.json.

This is step 3 of a 3-step pipeline (chunk → tag → index):
  1. chunk_documents.py  — split text artifacts into chunks, embed each chunk
  2. tag_chunks          — Claude task: assign section tags (e.g. "competitive",
                           "risk", "financial") to each chunk
  3. build_index.py      — THIS FILE: merge tags into chunks, load into LanceDB

The result is a LanceDB table at artifacts/index/ with two search indexes:
  - Vector index: 1536-dim embeddings for semantic similarity search
  - FTS index: BM25 full-text search on chunk text

Downstream, search_index.py queries this table using hybrid search (vector +
BM25 with reciprocal rank fusion) to retrieve relevant context for research
agents and writers. Tags enable section-level filtering (e.g., "only chunks
tagged 'financial' or 'valuation'").

This script does a full rebuild each run (drop table if exists, create fresh).
This is safe and fast since chunk counts per research run are small (~200).

Usage:
    ./skills/chunk_index/build_index.py SYMBOL --workdir DIR

Output (stdout):  JSON manifest {"status": "complete", "artifacts": [...]}
Output (files):   artifacts/index/ (LanceDB database directory)
"""
import argparse
import json
import sys
from pathlib import Path

import lancedb
import pyarrow as pa

# Add skills/ to path so we can import shared utilities
_SKILLS_DIR = Path(__file__).resolve().parent.parent
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

from utils import setup_logging  # noqa: E402

logger = setup_logging(__name__)

# Must match the embedding dimension from chunk_documents.py (text-embedding-3-small)
EMBED_DIM = 1536


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker")
    parser.add_argument("--workdir", required=True)
    args = parser.parse_args()

    workdir = Path(args.workdir)
    lancedb_dir = workdir / "lancedb"

    # chunks.json: produced by chunk_documents.py — array of
    # {id, text, source, doc_type, embedding} for each chunk
    chunks_path = lancedb_dir / "chunks.json"

    # chunk_tags.json: produced by tag_chunks Claude task — array of
    # {id, tags} where tags is a list of section labels like
    # ["competitive", "risk"] that classify each chunk's content
    tags_path = lancedb_dir / "chunk_tags.json"

    if not chunks_path.exists():
        print(json.dumps({"status": "failed", "error": "chunks.json not found", "artifacts": []}))
        return 1
    if not tags_path.exists():
        print(json.dumps({"status": "failed", "error": "chunk_tags.json not found", "artifacts": []}))
        return 1

    chunks = json.loads(chunks_path.read_text())
    tags_list = json.loads(tags_path.read_text())

    # Build a lookup: chunk_id → list of tag strings
    tags_map = {t["id"]: t["tags"] for t in tags_list}

    # Merge tags into each chunk. Tags are stored as a JSON-encoded string
    # (not a native list) because LanceDB/Arrow doesn't support variable-length
    # string lists natively. search_index.py deserializes at query time.
    # Chunks with no matching tags get an empty list [].
    for c in chunks:
        c["tags"] = json.dumps(tags_map.get(c["id"], []))

    # Create LanceDB database directory. LanceDB stores data as Lance files
    # (columnar format based on Arrow) — efficient for both vector search
    # and metadata filtering.
    index_dir = lancedb_dir / "index"
    index_dir.mkdir(exist_ok=True)
    db = lancedb.connect(str(index_dir))

    # PyArrow schema defines the table structure. The "vector" field is a
    # fixed-size list of 1536 floats — LanceDB uses this for ANN (approximate
    # nearest neighbor) search automatically.
    schema = pa.schema([
        pa.field("id", pa.string()),          # Unique chunk ID: "{source_stem}_{idx}"
        pa.field("text", pa.string()),         # Chunk text content
        pa.field("source", pa.string()),       # Source file path relative to workdir
        pa.field("doc_type", pa.string()),     # Document type: "10-K", "news", "analysis", etc.
        pa.field("tags", pa.string()),         # JSON-encoded list of section tags
        pa.field("vector", pa.list_(pa.float32(), EMBED_DIM)),  # 1536-dim embedding
    ])

    # Convert chunks to records, casting embeddings to float32.
    # The embedding values come from OpenAI as Python floats (float64);
    # we convert to float32 to match the Arrow schema and halve storage.
    records = []
    for c in chunks:
        records.append({
            "id": c["id"],
            "text": c["text"],
            "source": c["source"],
            "doc_type": c.get("doc_type", "other"),
            "tags": c["tags"],
            "vector": [float(x) for x in c["embedding"]],
        })

    # Full rebuild: drop existing table if present, then create fresh.
    # This is idempotent — safe to rerun on retries or pipeline restarts.
    if "chunks" in db.table_names():
        db.drop_table("chunks")
    table = db.create_table("chunks", data=records, schema=schema)

    # Create BM25 full-text search index on the text column.
    # This enables keyword search alongside vector search — the hybrid
    # approach (vector + BM25 with RRF) in search_index.py catches both
    # semantic matches and exact term matches.
    table.create_fts_index("text", replace=True)
    logger.info(f"Built index: {len(records)} chunks in {index_dir}")

    # Print JSON manifest to stdout — research.py parses this to register
    # the artifact in the database and determine task success/failure
    print(json.dumps({
        "status": "complete",
        "artifacts": [{"name": "index", "path": "lancedb/index/", "format": "lancedb"}],
        "error": None,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
