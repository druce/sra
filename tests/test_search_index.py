"""Tests for build_index.py + search_index.py."""
import json
import subprocess
from pathlib import Path

import pytest

CWD = str(Path(__file__).parent.parent)
BUILD_SCRIPT = ["uv", "run", "python", "skills/chunk_index/build_index.py"]
SEARCH_SCRIPT = ["uv", "run", "python", "skills/search_index/search_index.py"]


def run_build(workdir):
    r = subprocess.run(BUILD_SCRIPT + ["TEST", "--workdir", str(workdir)],
                       capture_output=True, text=True, cwd=CWD)
    try:
        return r.returncode, json.loads(r.stdout), r.stderr
    except json.JSONDecodeError:
        return r.returncode, None, r.stderr


def run_search(workdir, query, sections=None, top_k=5):
    cmd = SEARCH_SCRIPT + [query, "--workdir", str(workdir), "--top-k", str(top_k)]
    if sections:
        cmd += ["--sections"] + sections
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=CWD)
    try:
        return r.returncode, json.loads(r.stdout)
    except json.JSONDecodeError:
        return r.returncode, None


@pytest.fixture
def workdir_with_chunks(tmp_path):
    """Minimal workdir with chunks.json and chunk_tags.json."""
    lancedb_dir = tmp_path / "lancedb"
    lancedb_dir.mkdir()

    # Fake embeddings (1536 zeros — good enough for index structure tests)
    fake_vec = [0.0] * 1536
    chunks = [
        {"id": "wiki_0000", "text": "Nvidia competes with AMD and Intel in GPUs.",
         "source": "artifacts/wikipedia_full.txt", "doc_type": "wikipedia",
         "embedding": fake_vec},
        {"id": "wiki_0001", "text": "Nvidia revenue grew 120% in FY2024.",
         "source": "artifacts/wikipedia_full.txt", "doc_type": "wikipedia",
         "embedding": fake_vec},
        {"id": "10k_0000", "text": "Supply chain depends on TSMC for chip manufacturing.",
         "source": "artifacts/sec_10k_item1.md", "doc_type": "10-K",
         "embedding": fake_vec},
    ]
    (lancedb_dir / "chunks.json").write_text(json.dumps(chunks))
    tags = [
        {"id": "wiki_0000", "tags": ["competitive"]},
        {"id": "wiki_0001", "tags": ["financial"]},
        {"id": "10k_0000", "tags": ["supply_chain", "risk_news"]},
    ]
    (lancedb_dir / "chunk_tags.json").write_text(json.dumps(tags))
    return tmp_path


def test_build_index_creates_lance_db(workdir_with_chunks):
    rc, manifest, stderr = run_build(workdir_with_chunks)
    assert rc == 0, f"build_index failed: {stderr}"
    assert manifest["status"] == "complete"
    index_dir = workdir_with_chunks / "lancedb" / "index"
    assert index_dir.exists()
    assert any(index_dir.iterdir())  # non-empty


@pytest.mark.integration
def test_build_index_merges_tags(workdir_with_chunks):
    rc, _, _ = run_build(workdir_with_chunks)
    assert rc == 0
    # Verify the index file exists and contains expected data
    import lancedb
    db = lancedb.connect(str(workdir_with_chunks / "lancedb" / "index"))
    table = db.open_table("chunks")
    df = table.to_pandas()
    assert "tags" in df.columns
    assert len(df) == 3


@pytest.mark.integration
def test_search_returns_results(workdir_with_chunks):
    run_build(workdir_with_chunks)
    rc, results = run_search(workdir_with_chunks, "GPU competition")
    assert rc == 0
    assert isinstance(results, list)
    assert len(results) >= 1
    assert all("text" in r for r in results)
    assert all("source" in r for r in results)
    assert all("tags" in r for r in results)


@pytest.mark.integration
def test_search_filter_by_section(workdir_with_chunks):
    run_build(workdir_with_chunks)
    rc, results = run_search(workdir_with_chunks, "supply chain", sections=["supply_chain"])
    assert rc == 0
    # All returned results should have supply_chain tag
    for r in results:
        assert "supply_chain" in r["tags"]
