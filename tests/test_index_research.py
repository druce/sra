"""
Tests for the post-research indexing pipeline:
  - chunk_research.py: reads knowledge/findings_*.md → chunks + embeds
  - append_index.py: merges tags, appends to LanceDB

Tests cover:
- Reading findings markdown files from knowledge/ directory
- Primary tag derivation from filename
- Chunk metadata (doc_type, source)
- Empty/missing knowledge/ handling
- Multiple findings files
"""
import json
import sys
from pathlib import Path

# Add skills/ to path for imports
_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

from chunk_index.chunk_research import read_research_findings


# --- read_research_findings ---

def test_reads_single_findings_file(tmp_path):
    """Reads a single findings file and produces chunks."""
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "findings_profile.md").write_text(
        "## Company History\n\n"
        "Founded in 1976 by Steve Jobs. (Source: Wikipedia)\n\n"
        "---\n\n"
        "## Recent News\n\n"
        "Q4 2025 revenue was $120B. (Source: 10-K)\n"
    )
    chunks = read_research_findings(tmp_path)
    assert len(chunks) > 0
    assert all(c["primary_tag"] == "profile" for c in chunks)
    assert all(c["doc_type"] == "research_finding" for c in chunks)
    assert all(c["source"] == "knowledge/findings_profile.md" for c in chunks)


def test_tag_derived_from_filename(tmp_path):
    """Primary tag is stripped from filename correctly."""
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "findings_risk_news.md").write_text(
        "## Key Risk\n\nRegulatory investigation announced. (Source: SEC filing)\n"
    )
    chunks = read_research_findings(tmp_path)
    assert len(chunks) > 0
    assert chunks[0]["primary_tag"] == "risk_news"


def test_multiple_findings_files(tmp_path):
    """All findings_*.md files are read and tagged separately."""
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "findings_financial.md").write_text(
        "## Revenue\n\nRevenue grew 25% YoY. (Source: income statement)\n"
    )
    (knowledge / "findings_competitive.md").write_text(
        "## Market Share\n\nCompany holds 35% market share. (Source: FMP)\n"
    )
    chunks = read_research_findings(tmp_path)
    tags = {c["primary_tag"] for c in chunks}
    assert "financial" in tags
    assert "competitive" in tags


def test_chunk_ids_include_tag(tmp_path):
    """Chunk IDs include the tag for uniqueness."""
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "findings_valuation.md").write_text(
        "## P/E Ratio\n\nCurrently trading at 25x forward P/E. (Source: FMP)\n"
    )
    chunks = read_research_findings(tmp_path)
    assert all(c["id"].startswith("findings_valuation_") for c in chunks)


def test_empty_file_skipped(tmp_path):
    """Empty findings files are skipped."""
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "findings_profile.md").write_text("")
    (knowledge / "findings_financial.md").write_text(
        "## Revenue\n\nStrong growth. (Source: 10-K)\n"
    )
    chunks = read_research_findings(tmp_path)
    assert all(c["primary_tag"] == "financial" for c in chunks)


def test_no_knowledge_dir(tmp_path):
    """Missing knowledge/ directory returns empty list."""
    chunks = read_research_findings(tmp_path)
    assert chunks == []


def test_no_findings_files(tmp_path):
    """knowledge/ dir exists but has no findings_*.md files."""
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "other_file.txt").write_text("not a findings file")
    chunks = read_research_findings(tmp_path)
    assert chunks == []


def test_non_findings_files_ignored(tmp_path):
    """Files not matching findings_*.md pattern are ignored."""
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "wikipedia_summary.txt").write_text("Wikipedia content")
    (knowledge / "findings_profile.md").write_text(
        "## Profile\n\nCompany info. (Source: Wikipedia)\n"
    )
    chunks = read_research_findings(tmp_path)
    assert all(c["primary_tag"] == "profile" for c in chunks)
