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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--sections", nargs="*", default=None)
    parser.add_argument("--top-k", type=int, default=10, dest="top_k")
    args = parser.parse_args()

    workdir = Path(args.workdir)
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

    output = []
    for doc_id in merged_ids[: args.top_k]:
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

    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
