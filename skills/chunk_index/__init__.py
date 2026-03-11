"""Chunk, tag, and index pipeline for hybrid search over research artifacts."""

import sys
from pathlib import Path

_SKILLS_DIR = Path(__file__).resolve().parent.parent
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

import pyarrow as pa

from config import EMBED_DIM

# Shared Arrow schema for the LanceDB chunks table. Used by build_index.py
# (initial build) and index_research.py (appending research chunks).
CHUNKS_SCHEMA = pa.schema([
    pa.field("id", pa.string()),
    pa.field("text", pa.string()),
    pa.field("source", pa.string()),
    pa.field("doc_type", pa.string()),
    pa.field("tags", pa.string()),          # JSON-encoded list of section tags
    pa.field("vector", pa.list_(pa.float32(), EMBED_DIM)),
])
