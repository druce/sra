#!/usr/bin/env python3
"""
Hybrid vector + BM25 search over the LanceDB chunk index.

Usage:
    ./skills/search_index/search_index.py QUERY --workdir DIR [--sections S1 S2] [--top-k N]

Output: JSON array of matching chunks, ranked by reciprocal rank fusion.
"""
import argparse
import json
import sys
from pathlib import Path

import lancedb
from openai import OpenAI

_SKILLS_DIR = Path(__file__).resolve().parent.parent
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

from config import EMBED_MODEL  # noqa: E402
from utils import setup_logging, load_environment  # noqa: E402

load_environment()
logger = setup_logging(__name__)


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = 60) -> list[str]:
    """Merge multiple ranked lists via RRF. Returns IDs sorted by fused score."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores, key=lambda x: scores[x], reverse=True)


def show_stats(workdir: Path, source_filter: str | None = None, tag_filter: str | None = None) -> int:
    """Print index statistics, optionally filtered by source pattern and/or tag."""
    index_dir = workdir / "lancedb" / "index"
    if not index_dir.exists():
        print(json.dumps({"error": "Index not found — run build_index first"}))
        return 1

    db = lancedb.connect(str(index_dir))
    table = db.open_table("chunks")
    df = table.to_pandas()

    if source_filter:
        df = df[df["source"].str.contains(source_filter, case=False)]

    if tag_filter:
        df = df[df["tags"].apply(lambda t: tag_filter in json.loads(t))]

    if df.empty:
        filters = []
        if source_filter:
            filters.append(f"source='{source_filter}'")
        if tag_filter:
            filters.append(f"tag='{tag_filter}'")
        print(json.dumps({"error": f"No chunks matching {', '.join(filters)}", "total_chunks": 0}))
        return 0

    total = len(df)
    doc_types = df["doc_type"].value_counts().to_dict()
    sources = df["source"].value_counts().to_dict()

    # Tag distribution (tags are JSON-encoded lists)
    tag_counts: dict[str, int] = {}
    for tags_json in df["tags"]:
        for tag in json.loads(tags_json):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    result: dict = {
        "total_chunks": total,
        "doc_types": doc_types,
        "tags": dict(sorted(tag_counts.items(), key=lambda x: -x[1])),
        "sources": dict(sorted(sources.items(), key=lambda x: -x[1])),
    }

    # When filtering to a small set, also show individual chunks
    if source_filter and total <= 50:
        chunks = []
        for _, row in df.iterrows():
            chunks.append({
                "id": row["id"],
                "source": row["source"],
                "doc_type": row["doc_type"],
                "tags": json.loads(row["tags"]),
                "text": row["text"][:200] + ("..." if len(row["text"]) > 200 else ""),
            })
        result["chunks"] = chunks

    print(json.dumps(result, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="?", default=None)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--sections", nargs="*", default=None)
    parser.add_argument("--top-k", type=int, default=10, dest="top_k")
    parser.add_argument("--stats", action="store_true", help="Show index statistics instead of searching")
    parser.add_argument("--source", default=None, help="Filter --stats to chunks matching this source pattern")
    parser.add_argument("--tag", default=None, help="Filter --stats to chunks containing this tag")
    parser.add_argument("--all", action="store_true",
                        help="Return all chunks matching --sections filter (no query needed)")
    parser.add_argument("--max-chars", type=int, default=500_000, dest="max_chars",
                        help="Max total characters in output (default: 500000, ~125k tokens)")
    args = parser.parse_args()

    workdir = Path(args.workdir)

    if args.stats:
        return show_stats(workdir, args.source, args.tag)

    if args.all:
        if not args.sections:
            print(json.dumps({"error": "--all requires --sections"}))
            return 1
        index_dir = workdir / "lancedb" / "index"
        if not index_dir.exists():
            print(json.dumps({"error": "Index not found — run build_index first"}))
            return 1
        db = lancedb.connect(str(index_dir))
        table = db.open_table("chunks")
        df = table.to_pandas()
        output = []
        total_chars = 0
        for _, row in df.iterrows():
            tags = json.loads(row["tags"])
            if any(s in tags for s in args.sections):
                text = row["text"]
                if total_chars + len(text) > args.max_chars:
                    logger.info(f"Truncating at {len(output)} chunks ({total_chars:,} chars), "
                                f"max_chars={args.max_chars:,}")
                    break
                output.append({
                    "id": row["id"],
                    "text": text,
                    "source": row["source"],
                    "doc_type": row["doc_type"],
                    "tags": tags,
                })
                total_chars += len(text)
        print(json.dumps(output, indent=2))
        return 0

    if not args.query:
        print(json.dumps({"error": "query is required (or use --stats/--all)"}))
        return 1

    index_dir = workdir / "lancedb" / "index"

    if not index_dir.exists():
        print(json.dumps({"error": "Index not found — run build_index first"}))
        return 1

    db = lancedb.connect(str(index_dir))
    table = db.open_table("chunks")

    # Vector search
    client = OpenAI()
    resp = client.embeddings.create(model=EMBED_MODEL, input=[args.query])
    query_vec = resp.data[0].embedding

    vec_results = (
        table.search(query_vec, query_type="vector")
        .limit(args.top_k * 2)
        .to_pandas()
    )

    # FTS search
    try:
        fts_results = (
            table.search(args.query, query_type="fts")
            .limit(args.top_k * 2)
            .to_pandas()
        )
    except Exception:
        fts_results = vec_results.head(0)  # empty fallback if FTS unavailable

    # RRF fusion
    vec_ids = vec_results["id"].tolist()
    fts_ids = fts_results["id"].tolist() if len(fts_results) > 0 else []
    merged_ids = reciprocal_rank_fusion([vec_ids, fts_ids])

    # Build id->row lookup
    all_rows = {row["id"]: row for _, row in vec_results.iterrows()}
    for _, row in fts_results.iterrows():
        all_rows[row["id"]] = row

    # Apply section filter first, then take top_k results
    output = []
    for doc_id in merged_ids:
        if doc_id not in all_rows:
            continue
        row = all_rows[doc_id]
        tags = json.loads(row["tags"])
        if args.sections and not any(s in tags for s in args.sections):
            continue
        output.append({
            "id": doc_id,
            "text": row["text"],
            "source": row["source"],
            "doc_type": row["doc_type"],
            "tags": tags,
        })
        if len(output) >= args.top_k:
            break

    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
