"""Tests for skills/chunk_index/__init__.py — chunks_to_records and schema helpers.

Also tests skills/chunk_index/chunk_documents.py — chunk_text and infer_doc_type.
"""
import sys
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parent.parent / "skills"
if str(SKILLS) not in sys.path:
    sys.path.insert(0, str(SKILLS))

from chunk_index import chunks_to_records
from chunk_index.chunk_documents import chunk_text, infer_doc_type, count_tokens


# ---------------------------------------------------------------------------
# infer_doc_type
# ---------------------------------------------------------------------------

class TestInferDocType:
    def test_10k(self):
        assert infer_doc_type("knowledge/sec_10k_item1.md") == "10-K"

    def test_10q(self):
        assert infer_doc_type("knowledge/sec_10q_item2.md") == "10-Q"

    def test_8k(self):
        assert infer_doc_type("knowledge/sec_8k_2025-01-15.md") == "8-K"

    def test_wikipedia(self):
        assert infer_doc_type("knowledge/wikipedia_summary.txt") == "wikipedia"

    def test_news(self):
        assert infer_doc_type("knowledge/news_article.md") == "news"

    def test_analysis(self):
        assert infer_doc_type("knowledge/perplexity_analysis.md") == "analysis"

    def test_profile(self):
        assert infer_doc_type("knowledge/business_profile.md") == "profile"

    def test_unknown(self):
        assert infer_doc_type("knowledge/random_file.md") == "other"


# ---------------------------------------------------------------------------
# chunk_text
# ---------------------------------------------------------------------------

class TestChunkText:
    def test_short_text_single_chunk(self):
        text = "This is a short paragraph.\n\nAnother paragraph."
        chunks = chunk_text(text, "test.md")
        assert len(chunks) == 1
        assert chunks[0]["id"] == "test_0000"
        assert chunks[0]["source"] == "test.md"
        assert "short paragraph" in chunks[0]["text"]

    def test_long_text_multiple_chunks(self):
        # Create text that exceeds CHUNK_MAX_TOKENS (800)
        paragraphs = [("Paragraph number %d. " % i) * 20 for i in range(50)]
        text = "\n\n".join(paragraphs)
        chunks = chunk_text(text, "long.md")
        assert len(chunks) > 1
        # Each chunk should have sequential IDs
        for i, chunk in enumerate(chunks):
            assert chunk["id"] == f"long_{i:04d}"

    def test_empty_paragraphs_skipped(self):
        text = "Content here.\n\n\n\n\nMore content."
        chunks = chunk_text(text, "test.md")
        assert len(chunks) == 1
        assert "Content here" in chunks[0]["text"]
        assert "More content" in chunks[0]["text"]

    def test_preserves_paragraph_boundaries(self):
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        chunks = chunk_text(text, "test.md")
        assert len(chunks) == 1
        assert "\n\n" in chunks[0]["text"]

    def test_doc_type_assigned(self):
        chunks = chunk_text("Some SEC content.", "knowledge/sec_10k_item1.md")
        assert chunks[0]["doc_type"] == "10-K"

    def test_empty_text_no_chunks(self):
        chunks = chunk_text("", "test.md")
        assert len(chunks) == 0

    def test_whitespace_only_no_chunks(self):
        chunks = chunk_text("   \n\n   ", "test.md")
        assert len(chunks) == 0


# ---------------------------------------------------------------------------
# count_tokens
# ---------------------------------------------------------------------------

class TestCountTokens:
    def test_basic_count(self):
        # "hello world" should be roughly 2 tokens
        count = count_tokens("hello world")
        assert count >= 2

    def test_empty_string(self):
        assert count_tokens("") == 0


# ---------------------------------------------------------------------------
# chunks_to_records
# ---------------------------------------------------------------------------

class TestChunksToRecords:
    def test_basic_conversion(self):
        chunks = [
            {
                "id": "test_0000",
                "text": "Some text content",
                "source": "knowledge/test.md",
                "doc_type": "other",
                "tags": '["profile"]',
                "embedding": [0.1, 0.2, 0.3],
            }
        ]
        records = chunks_to_records(chunks)
        assert len(records) == 1
        r = records[0]
        assert r["id"] == "test_0000"
        assert r["vector"] == [0.1, 0.2, 0.3]
        assert "embedding" not in r  # renamed to "vector"

    def test_default_doc_type(self):
        chunks = [
            {
                "id": "c1",
                "text": "text",
                "source": "s",
                "tags": "[]",
                "embedding": [0.0],
            }
        ]
        records = chunks_to_records(chunks, default_doc_type="research_finding")
        assert records[0]["doc_type"] == "research_finding"

    def test_explicit_doc_type_preserved(self):
        chunks = [
            {
                "id": "c1",
                "text": "text",
                "source": "s",
                "doc_type": "10-K",
                "tags": "[]",
                "embedding": [0.0],
            }
        ]
        records = chunks_to_records(chunks, default_doc_type="other")
        assert records[0]["doc_type"] == "10-K"
