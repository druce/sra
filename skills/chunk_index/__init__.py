"""Chunk, tag, and index pipeline for hybrid search over research artifacts."""

import sys
from pathlib import Path

_SKILLS_DIR = Path(__file__).resolve().parent.parent
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

# Lazy-loaded to avoid requiring pyarrow at import time (tests that import
# submodule helpers like extract_text_from_result shouldn't need pyarrow).
_CHUNKS_SCHEMA = None


def get_chunks_schema():
    """Return the shared Arrow schema for the LanceDB chunks table, creating it on first call."""
    global _CHUNKS_SCHEMA
    if _CHUNKS_SCHEMA is None:
        import pyarrow as pa
        from config import EMBED_DIM

        _CHUNKS_SCHEMA = pa.schema([
            pa.field("id", pa.string()),
            pa.field("text", pa.string()),
            pa.field("source", pa.string()),
            pa.field("doc_type", pa.string()),
            pa.field("tags", pa.string()),          # JSON-encoded list of section tags
            pa.field("vector", pa.list_(pa.float32(), EMBED_DIM)),
        ])
    return _CHUNKS_SCHEMA


# Backward-compatible attribute access: `from chunk_index import CHUNKS_SCHEMA`
def __getattr__(name):
    if name == "CHUNKS_SCHEMA":
        return get_chunks_schema()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
