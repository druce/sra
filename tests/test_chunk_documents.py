"""Tests for chunk_documents.py — chunking and embedding of text artifacts."""
import json
import subprocess
from pathlib import Path

import pytest

CWD = str(Path(__file__).parent.parent)
SCRIPT = ["uv", "run", "python", "skills/chunk_index/chunk_documents.py"]


def run_chunk(workdir, *extra_args):
    result = subprocess.run(
        SCRIPT + ["TEST", "--workdir", str(workdir)] + list(extra_args),
        capture_output=True, text=True, cwd=CWD,
    )
    try:
        manifest = json.loads(result.stdout)
    except json.JSONDecodeError:
        manifest = None
    return result.returncode, manifest, result.stderr


@pytest.fixture
def workdir_with_artifacts(tmp_path):
    """Create a minimal workdir with text artifacts and a manifest."""
    art = tmp_path / "artifacts"
    art.mkdir()
    (art / "wikipedia_full.txt").write_text(
        "Nvidia Corporation is an American multinational technology company.\n\n"
        "It designs graphics processing units for gaming and professional markets.\n\n"
        "Nvidia was founded in 1993 by Jensen Huang, Chris Malachowsky, and Curtis Priem.\n\n"
        "The company is headquartered in Santa Clara, California.\n\n"
        "Nvidia's primary products include the GeForce line of GPUs for gaming.\n\n"
        "Nvidia competes with AMD in discrete graphics cards for gaming.\n\n"
        "The data center segment has grown rapidly due to AI computing demand.\n\n"
        "Nvidia's CUDA platform is widely used in scientific computing.\n\n"
        "The company reported record revenue in fiscal year 2024.\n\n"
        "Supply chain dependencies include TSMC for chip manufacturing."
    )
    (art / "manifest.json").write_text(json.dumps([
        {"file": "artifacts/wikipedia_full.txt", "format": "txt",
         "description": "Wikipedia full article"}
    ]))
    return tmp_path


@pytest.mark.integration
def test_chunk_documents_produces_chunks_json(workdir_with_artifacts):
    rc, manifest, stderr = run_chunk(workdir_with_artifacts)
    assert rc == 0, f"Failed: {stderr}"
    assert manifest["status"] == "complete"
    chunks_path = workdir_with_artifacts / "lancedb" / "chunks.json"
    assert chunks_path.exists()
    chunks = json.loads(chunks_path.read_text())
    assert len(chunks) >= 1
    assert all("id" in c for c in chunks)
    assert all("text" in c for c in chunks)
    assert all("source" in c for c in chunks)
    assert all("embedding" in c for c in chunks)
    assert all(len(c["embedding"]) == 1536 for c in chunks)


@pytest.mark.integration
def test_chunk_documents_skips_binary(workdir_with_artifacts):
    """PNG and CSV files should not be chunked."""
    (workdir_with_artifacts / "artifacts" / "chart.png").write_bytes(b"\x89PNG fake")
    (workdir_with_artifacts / "artifacts" / "income.csv").write_text("year,revenue\n2024,60000\n")
    rc, manifest, _ = run_chunk(workdir_with_artifacts)
    assert rc == 0
    chunks = json.loads((workdir_with_artifacts / "lancedb" / "chunks.json").read_text())
    sources = [c["source"] for c in chunks]
    assert not any("chart.png" in s for s in sources)
    assert not any("income.csv" in s for s in sources)


@pytest.mark.integration
def test_chunk_documents_metadata(workdir_with_artifacts):
    rc, _, _ = run_chunk(workdir_with_artifacts)
    assert rc == 0
    chunks = json.loads((workdir_with_artifacts / "lancedb" / "chunks.json").read_text())
    for c in chunks:
        assert c["source"].startswith("artifacts/")
        assert "doc_type" in c
