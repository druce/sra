#!/usr/bin/env python3
"""
Chunk and embed text artifacts for hybrid search index.

This is step 1 of a 3-step pipeline (chunk → tag → index):
  1. chunk_documents.py  — THIS FILE: split text into chunks, embed each chunk
  2. tag_chunks          — Claude task: assign section tags to each chunk
  3. build_index.py      — load chunks + tags into LanceDB for hybrid search

The pipeline processes only text artifacts (.md, .txt) produced by upstream
data-gathering tasks (perplexity research, SEC filings, wikipedia, etc.).
Binary files (charts, CSVs, JSON data) are skipped — they're accessed
directly by downstream tasks, not via search.

Chunking strategy: greedy paragraph accumulation. Paragraphs are added to the
current chunk until adding another would exceed CHUNK_MAX_TOKENS, then the
chunk is finalized and a new one starts. This preserves paragraph boundaries
(no mid-sentence splits) at the cost of zero overlap between chunks.

Embedding: all chunks are embedded in a single batched OpenAI API call using
text-embedding-3-small (1536 dimensions). The embeddings enable vector
similarity search in the downstream LanceDB index.

Usage:
    ./skills/chunk_index/chunk_documents.py SYMBOL --workdir DIR

Output (stdout):  JSON manifest {"status": "complete", "artifacts": [...]}
Output (files):   artifacts/chunks.json
"""
import argparse
import json
import sys
from pathlib import Path

import tiktoken
from openai import OpenAI

# Add skills/ to path so we can import shared utilities
_SKILLS_DIR = Path(__file__).resolve().parent.parent
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

from config import EMBED_MODEL, EMBED_DIM, CHUNK_MAX_TOKENS  # noqa: E402
from utils import setup_logging, load_environment  # noqa: E402

load_environment()
logger = setup_logging(__name__)

# Only chunk text files — skip binary, structured data, and database files
TEXT_EXTENSIONS = {".md", ".txt"}

# cl100k_base is the tokenizer used by GPT-4 and text-embedding-3-small,
# so token counts here match what the embedding model sees
enc = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(enc.encode(text))


def chunk_text(text: str, source: str) -> list[dict]:
    """Split text into chunks at paragraph boundaries.

    Algorithm: greedy accumulation. Walk paragraphs in order, adding each to
    the current chunk. When the next paragraph would push the chunk past
    CHUNK_MAX_TOKENS, finalize the current chunk and start a new one.

    If a single paragraph exceeds CHUNK_MAX_TOKENS, it becomes its own chunk
    (we never split within a paragraph — this preserves sentence coherence
    for financial documents where mid-paragraph splits lose context).

    No overlap between chunks. Downstream hybrid search (vector + BM25) with
    reciprocal rank fusion compensates by retrieving multiple adjacent chunks.

    Each chunk gets:
      - id: "{source_stem}_{sequential_index}" for stable references
      - text: the concatenated paragraphs
      - source: original file path (relative to workdir)
      - doc_type: classified document type for filtering (SEC filing, news, etc.)
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks = []
    current_parts = []
    current_tokens = 0
    chunk_idx = 0

    for para in paragraphs:
        para_tokens = count_tokens(para)
        # Finalize current chunk if adding this paragraph would exceed the limit.
        # The "and current_parts" guard ensures we don't emit an empty chunk
        # when a single paragraph exceeds CHUNK_MAX_TOKENS — instead, that
        # paragraph becomes the start of a new (oversized) chunk.
        if current_tokens + para_tokens > CHUNK_MAX_TOKENS and current_parts:
            chunks.append({
                "id": f"{Path(source).stem}_{chunk_idx:04d}",
                "text": "\n\n".join(current_parts),
                "source": source,
                "doc_type": infer_doc_type(source),
            })
            chunk_idx += 1
            current_parts = []
            current_tokens = 0
        current_parts.append(para)
        current_tokens += para_tokens

    # Don't forget the last chunk (the loop only finalizes when it *exceeds*
    # the limit, so the final accumulation is always left over)
    if current_parts:
        chunks.append({
            "id": f"{Path(source).stem}_{chunk_idx:04d}",
            "text": "\n\n".join(current_parts),
            "source": source,
            "doc_type": infer_doc_type(source),
        })

    return chunks


def infer_doc_type(source: str) -> str:
    """Classify a source file into a document type based on filename patterns.

    Used for metadata tagging so downstream search can filter by document type
    (e.g., only search SEC filings, or only search news articles). The doc_type
    is stored in the LanceDB index alongside the chunk text and embedding.
    """
    name = Path(source).name.lower()
    if "10k" in name or "10-k" in name:
        return "10-K"
    if "10q" in name or "10-q" in name:
        return "10-Q"
    if "8k" in name or "8-k" in name:
        return "8-K"
    if "wikipedia" in name:
        return "wikipedia"
    if "news" in name:
        return "news"
    if "perplexity" in name or "analysis" in name:
        return "analysis"
    if "business_profile" in name:
        return "profile"
    return "other"


def embed_chunks(chunks: list[dict], client: OpenAI) -> list[dict]:
    """Embed all chunks in a single batched API call.

    OpenAI's embeddings endpoint accepts a list of strings and returns all
    embeddings in one response. This is much faster than embedding one at a
    time (one HTTP round-trip vs hundreds). For a typical research run with
    ~200 chunks, this completes in a few seconds.

    Each chunk gets an "embedding" field: a 1536-dim float list used for
    vector similarity search in LanceDB.
    """
    texts = [c["text"] for c in chunks]
    logger.info(f"Embedding {len(texts)} chunks...")
    response = client.embeddings.create(model=EMBED_MODEL, input=texts)
    for i, data in enumerate(response.data):
        chunks[i]["embedding"] = data.embedding
    return chunks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker")
    parser.add_argument("--workdir", required=True)
    args = parser.parse_args()

    workdir = Path(args.workdir)
    artifacts_dir = workdir / "artifacts"

    # manifest.json is written by research.py before each wave, listing all
    # artifacts produced by completed tasks. We iterate it to find text files.
    manifest_path = artifacts_dir / "manifest.json"

    if not manifest_path.exists():
        print(json.dumps({"status": "failed", "error": "manifest.json not found", "artifacts": []}))
        return 1

    manifest = json.loads(manifest_path.read_text())
    client = OpenAI()
    all_chunks = []

    # Walk all artifacts from the manifest. Only text files (.md, .txt) get
    # chunked — these are perplexity research, SEC filing extracts, wikipedia
    # summaries, analysis outputs, etc. JSON data files and charts are skipped.
    for entry in manifest:
        file_path = workdir / entry["file"]
        ext = file_path.suffix.lower()
        if ext not in TEXT_EXTENSIONS:
            continue
        if not file_path.exists():
            logger.warning(f"Skipping missing file: {file_path}")
            continue
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        if not text.strip():
            continue
        source = entry["file"]
        chunks = chunk_text(text, source)
        logger.info(f"  {source}: {len(chunks)} chunks")
        all_chunks.extend(chunks)

    if not all_chunks:
        print(json.dumps({"status": "failed", "error": "No text artifacts to chunk", "artifacts": []}))
        return 1

    # Embed all chunks from all documents in one batched API call
    all_chunks = embed_chunks(all_chunks, client)

    # Write chunks.json — consumed by tag_chunks (Claude) and build_index.py.
    # Each entry: {id, text, source, doc_type, embedding}
    lancedb_dir = workdir / "lancedb"
    lancedb_dir.mkdir(parents=True, exist_ok=True)
    out_path = lancedb_dir / "chunks.json"
    out_path.write_text(json.dumps(all_chunks, indent=2))
    logger.info(f"Wrote {len(all_chunks)} chunks to {out_path}")

    # Print JSON manifest to stdout — research.py parses this to register
    # the artifact in the database and determine task success/failure
    print(json.dumps({
        "status": "complete",
        "artifacts": [{"name": "chunks", "path": "lancedb/chunks.json", "format": "json"}],
        "error": None,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
