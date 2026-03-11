"""Tests for db.py — all subcommands."""

import json
import subprocess
from pathlib import Path

import pytest

CWD = str(Path(__file__).parent.parent)
DB_PY = ["uv", "run", "python", "skills/db.py"]
DAG = "dags/sra.yaml"
TICKER = "TEST"


def run_db(*args):
    """Run db.py with the given args; return (returncode, parsed_json)."""
    result = subprocess.run(
        DB_PY + list(args),
        capture_output=True,
        text=True,
        cwd=CWD,
    )
    try:
        return result.returncode, json.loads(result.stdout)
    except json.JSONDecodeError:
        return result.returncode, result.stdout


@pytest.fixture
def workdir(tmp_path):
    """Initialize a fresh database and return its workdir path."""
    wd = tmp_path / "test_run"
    rc, out = run_db("init", "--workdir", str(wd), "--dag", DAG, "--ticker", TICKER)
    assert rc == 0, f"init failed: {out}"
    return wd


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def test_init_creates_db(tmp_path):
    wd = tmp_path / "run"
    rc, out = run_db("init", "--workdir", str(wd), "--dag", DAG, "--ticker", TICKER)
    assert rc == 0
    assert out["status"] == "ok"
    assert out["tasks"] > 0
    assert (wd / "research.db").exists()


def test_init_idempotent_workdir(tmp_path):
    """init creates the workdir if it doesn't exist."""
    wd = tmp_path / "nested" / "run"
    rc, out = run_db("init", "--workdir", str(wd), "--dag", DAG, "--ticker", TICKER)
    assert rc == 0
    assert wd.exists()


def test_init_missing_dag(tmp_path):
    wd = tmp_path / "run"
    rc, out = run_db("init", "--workdir", str(wd), "--dag", "nonexistent.yaml", "--ticker", TICKER)
    assert rc == 1
    assert out["status"] == "error"


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_validate_valid_dag():
    rc, out = run_db("validate", "--dag", DAG, "--ticker", TICKER)
    assert rc == 0
    assert out["status"] == "ok"
    assert out["tasks"] > 0
    assert "python" in out["task_types"]


def test_validate_invalid_dag(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "dag:\n  version: 2\n  name: Bad\ntasks:\n"
        "  broken:\n    description: Missing type\n    config:\n      command: echo hi\n"
    )
    rc, out = run_db("validate", "--dag", str(bad))
    assert rc == 1
    assert out["status"] == "error"


def test_validate_missing_file(tmp_path):
    rc, out = run_db("validate", "--dag", str(tmp_path / "nope.yaml"))
    assert rc == 1
    assert out["status"] == "error"


# ---------------------------------------------------------------------------
# task-ready
# ---------------------------------------------------------------------------


def test_task_ready_after_init(workdir):
    """Tasks with no dependencies should be immediately ready."""
    rc, out = run_db("task-ready", "--workdir", str(workdir))
    assert rc == 0
    assert isinstance(out, list)
    assert len(out) > 0
    ids = {t["id"] for t in out}
    # 'profile' has no dependencies
    assert "profile" in ids


def test_task_ready_each_entry_has_required_fields(workdir):
    rc, out = run_db("task-ready", "--workdir", str(workdir))
    assert rc == 0
    for task in out:
        assert "id" in task
        assert "skill" in task
        assert "description" in task
        assert "params" in task


def test_task_ready_unlocks_after_dep_completes(workdir):
    """Completing profile + peers should unlock tasks that depend on them."""
    run_db("task-update", "--workdir", str(workdir), "--task-id", "profile", "--status", "complete")
    run_db("task-update", "--workdir", str(workdir), "--task-id", "peers", "--status", "complete")
    rc, out = run_db("task-ready", "--workdir", str(workdir))
    assert rc == 0
    ids = {t["id"] for t in out}
    # fundamental, fetch_edgar, wikipedia, detailed_profile all depend on [profile, peers]
    assert any(tid in ids for tid in ("fundamental", "fetch_edgar", "wikipedia", "detailed_profile"))


def test_task_ready_failed_deps_dont_block(workdir):
    """A failed dependency should not block downstream tasks."""
    # Complete all data-gathering deps except fetch_edgar (which fails)
    for tid in ("profile", "peers", "technical", "fundamental", "detailed_profile", "wikipedia", "custom_research"):
        run_db("task-update", "--workdir", str(workdir), "--task-id", tid, "--status", "complete")
    run_db("task-update", "--workdir", str(workdir), "--task-id", "fetch_edgar", "--status", "failed",
           "--error", "timeout")
    # Also complete chunk wave, research wave, and index_research tasks
    for tid in ("chunk_documents", "tag_chunks", "build_index",
                "research_profile", "research_business", "research_competitive",
                "research_supply_chain", "research_financial", "research_valuation",
                "research_risk_news", "index_research"):
        run_db("task-update", "--workdir", str(workdir), "--task-id", tid, "--status", "complete")

    rc, out = run_db("task-ready", "--workdir", str(workdir))
    assert rc == 0
    ids = {t["id"] for t in out}
    assert "write_profile" in ids


def test_task_ready_does_not_include_running(workdir):
    """A running task is not re-listed as ready."""
    run_db("task-update", "--workdir", str(workdir), "--task-id", "profile", "--status", "running")
    rc, out = run_db("task-ready", "--workdir", str(workdir))
    assert rc == 0
    ids = {t["id"] for t in out}
    assert "profile" not in ids


# ---------------------------------------------------------------------------
# task-get
# ---------------------------------------------------------------------------


def test_task_get_valid(workdir):
    rc, out = run_db("task-get", "--workdir", str(workdir), "--task-id", "profile")
    assert rc == 0
    assert out["id"] == "profile"
    assert out["skill"] == "python"
    assert out["status"] == "pending"
    assert isinstance(out["depends_on"], list)
    assert isinstance(out["params"], dict)
    assert "artifact_count" in out


def test_task_get_has_depends_on(workdir):
    """A task with dependencies should list them."""
    rc, out = run_db("task-get", "--workdir", str(workdir), "--task-id", "fundamental")
    assert rc == 0
    assert "profile" in out["depends_on"]


def test_task_get_missing(workdir):
    rc, out = run_db("task-get", "--workdir", str(workdir), "--task-id", "nonexistent")
    assert rc == 1
    assert out["status"] == "error"


# ---------------------------------------------------------------------------
# task-update
# ---------------------------------------------------------------------------


def test_task_update_status(workdir):
    rc, out = run_db("task-update", "--workdir", str(workdir), "--task-id", "profile", "--status", "running")
    assert rc == 0
    assert out["status"] == "ok"
    assert out["new_status"] == "running"
    _, task = run_db("task-get", "--workdir", str(workdir), "--task-id", "profile")
    assert task["status"] == "running"


def test_task_update_summary(workdir):
    run_db("task-update", "--workdir", str(workdir), "--task-id", "profile",
           "--status", "complete", "--summary", "Fetched TEST profile")
    _, task = run_db("task-get", "--workdir", str(workdir), "--task-id", "profile")
    assert task["summary"] == "Fetched TEST profile"


def test_task_update_error(workdir):
    run_db("task-update", "--workdir", str(workdir), "--task-id", "profile",
           "--status", "failed", "--error", "Connection timeout")
    _, task = run_db("task-get", "--workdir", str(workdir), "--task-id", "profile")
    assert task["status"] == "failed"
    assert task["error"] == "Connection timeout"


def test_task_update_all_statuses(workdir):
    for status in ("pending", "running", "complete", "failed", "skipped"):
        rc, out = run_db("task-update", "--workdir", str(workdir), "--task-id", "profile", "--status", status)
        assert rc == 0, f"failed for status={status}: {out}"


def test_task_update_missing_task(workdir):
    rc, out = run_db("task-update", "--workdir", str(workdir), "--task-id", "nonexistent", "--status", "complete")
    assert rc == 1
    assert out["status"] == "error"


def test_task_update_no_fields_errors(workdir):
    """Calling task-update with no fields to update should error."""
    rc, out = run_db("task-update", "--workdir", str(workdir), "--task-id", "profile")
    assert rc == 1
    assert out["status"] == "error"


# ---------------------------------------------------------------------------
# artifact-add
# ---------------------------------------------------------------------------


def _create_artifact_file(workdir, rel_path, content="{}"):
    """Create a file at workdir/rel_path so artifact-add validation passes."""
    p = Path(workdir) / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def test_artifact_add_valid(workdir):
    _create_artifact_file(workdir, "artifacts/profile.json")
    rc, out = run_db(
        "artifact-add", "--workdir", str(workdir),
        "--task-id", "profile", "--name", "profile",
        "--path", "artifacts/profile.json", "--format", "json",
        "--summary", "Company profile data",
    )
    assert rc == 0
    assert out["status"] == "ok"
    assert out["name"] == "profile"
    assert out["task"] == "profile"
    assert "artifact_id" in out


def test_artifact_add_upserts(workdir):
    """Adding the same (task, name) twice updates, not duplicates."""
    _create_artifact_file(workdir, "artifacts/profile.json")
    for _ in range(2):
        run_db(
            "artifact-add", "--workdir", str(workdir),
            "--task-id", "profile", "--name", "profile",
            "--path", "artifacts/profile.json", "--format", "json",
        )
    _, artifacts = run_db("artifact-list", "--workdir", str(workdir), "--task", "profile")
    assert len([a for a in artifacts if a["name"] == "profile"]) == 1


def test_artifact_add_missing_task(workdir):
    rc, out = run_db(
        "artifact-add", "--workdir", str(workdir),
        "--task-id", "nonexistent", "--name", "out",
        "--path", "artifacts/out.json", "--format", "json",
    )
    assert rc == 1
    assert out["status"] == "error"


# ---------------------------------------------------------------------------
# artifact-list
# ---------------------------------------------------------------------------


def test_artifact_list_empty(workdir):
    rc, out = run_db("artifact-list", "--workdir", str(workdir))
    assert rc == 0
    assert out == []


def test_artifact_list_all(workdir):
    _create_artifact_file(workdir, "artifacts/profile.json")
    _create_artifact_file(workdir, "artifacts/chart.png", content="fake png")
    run_db("artifact-add", "--workdir", str(workdir), "--task-id", "profile",
           "--name", "profile", "--path", "artifacts/profile.json", "--format", "json")
    run_db("artifact-add", "--workdir", str(workdir), "--task-id", "technical",
           "--name", "chart", "--path", "artifacts/chart.png", "--format", "png")

    rc, out = run_db("artifact-list", "--workdir", str(workdir))
    assert rc == 0
    assert len(out) == 2
    names = {a["name"] for a in out}
    assert names == {"profile", "chart"}


def test_artifact_list_filter_by_task(workdir):
    _create_artifact_file(workdir, "artifacts/profile.json")
    _create_artifact_file(workdir, "artifacts/chart.png", content="fake png")
    run_db("artifact-add", "--workdir", str(workdir), "--task-id", "profile",
           "--name", "profile", "--path", "artifacts/profile.json", "--format", "json")
    run_db("artifact-add", "--workdir", str(workdir), "--task-id", "technical",
           "--name", "chart", "--path", "artifacts/chart.png", "--format", "png")

    rc, out = run_db("artifact-list", "--workdir", str(workdir), "--task", "profile")
    assert rc == 0
    assert len(out) == 1
    assert out[0]["name"] == "profile"
    assert out[0]["task_id"] == "profile"


def test_artifact_list_fields(workdir):
    _create_artifact_file(workdir, "artifacts/profile.json")
    run_db("artifact-add", "--workdir", str(workdir), "--task-id", "profile",
           "--name", "profile", "--path", "artifacts/profile.json", "--format", "json",
           "--description", "Company identity and valuation snapshot",
           "--source", "yfinance", "--summary", "Profile data")
    _, out = run_db("artifact-list", "--workdir", str(workdir))
    a = out[0]
    assert a["task_id"] == "profile"
    assert a["name"] == "profile"
    assert a["path"] == "artifacts/profile.json"
    assert a["format"] == "json"
    assert a["description"] == "Company identity and valuation snapshot"
    assert a["source"] == "yfinance"
    assert a["summary"] == "Profile data"


def test_artifact_description_in_list(workdir):
    """artifact-list includes description field for all artifacts."""
    _create_artifact_file(workdir, "artifacts/profile.json")
    _create_artifact_file(workdir, "artifacts/chart.png", content="fake png")
    run_db("artifact-add", "--workdir", str(workdir), "--task-id", "profile",
           "--name", "profile", "--path", "artifacts/profile.json", "--format", "json",
           "--description", "Company profile data")
    run_db("artifact-add", "--workdir", str(workdir), "--task-id", "technical",
           "--name", "chart", "--path", "artifacts/chart.png", "--format", "png")

    rc, out = run_db("artifact-list", "--workdir", str(workdir))
    assert rc == 0
    for a in out:
        assert "description" in a
    # Explicit description is preserved; YAML fallback populates the other
    descs = {a["name"]: a["description"] for a in out}
    assert descs["profile"] == "Company profile data"
    # chart gets its description from the YAML output definition
    assert descs["chart"] is not None
    assert len(descs["chart"]) > 0


def test_output_descriptions_in_task_params(workdir):
    """Output descriptions from YAML are stored in task params."""
    rc, out = run_db("task-get", "--workdir", str(workdir), "--task-id", "profile")
    assert rc == 0
    outputs = out["params"]["outputs"]
    assert "description" in outputs["profile"]
    assert len(outputs["profile"]["description"]) > 0
    # peers_list is defined in the 'peers' task, not 'profile'
    rc2, out2 = run_db("task-get", "--workdir", str(workdir), "--task-id", "peers")
    assert rc2 == 0
    peers_outputs = out2["params"]["outputs"]
    assert "description" in peers_outputs["peers_list"]
    assert len(peers_outputs["peers_list"]["description"]) > 0


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_after_init(workdir):
    rc, out = run_db("status", "--workdir", str(workdir))
    assert rc == 0
    assert out["research"]["ticker"] == TICKER
    assert out["research"]["status"] == "not started"
    assert out["tasks"]["total"] > 0
    assert out["tasks"]["pending"] > 0
    assert out["tasks"]["complete"] == 0
    assert out["artifacts"]["total"] == 0


def test_status_task_details_present(workdir):
    rc, out = run_db("status", "--workdir", str(workdir))
    assert rc == 0
    assert isinstance(out["task_details"], list)
    assert len(out["task_details"]) == out["tasks"]["total"]
    ids = {t["id"] for t in out["task_details"]}
    assert "profile" in ids


def test_status_reflects_updates(workdir):
    _create_artifact_file(workdir, "artifacts/profile.json")
    run_db("task-update", "--workdir", str(workdir), "--task-id", "profile", "--status", "complete")
    run_db("artifact-add", "--workdir", str(workdir), "--task-id", "profile",
           "--name", "profile", "--path", "artifacts/profile.json", "--format", "json")

    rc, out = run_db("status", "--workdir", str(workdir))
    assert rc == 0
    assert out["tasks"]["complete"] == 1
    assert out["tasks"]["pending"] == out["tasks"]["total"] - 1
    assert out["artifacts"]["total"] == 1


# ---------------------------------------------------------------------------
# research-update
# ---------------------------------------------------------------------------


def test_research_update_valid(workdir):
    rc, out = run_db("research-update", "--workdir", str(workdir), "--status", "running")
    assert rc == 0
    assert out["new_status"] == "running"
    _, status = run_db("status", "--workdir", str(workdir))
    assert status["research"]["status"] == "running"


def test_research_update_all_statuses(workdir):
    for status in ("not started", "running", "complete", "failed"):
        rc, out = run_db("research-update", "--workdir", str(workdir), "--status", status)
        assert rc == 0, f"failed for status={status}: {out}"
        assert out["new_status"] == status


# ---------------------------------------------------------------------------
# task-context
# ---------------------------------------------------------------------------


def test_task_context_with_deps(workdir):
    """task-context resolves dependency artifacts for a task."""
    # chunk_documents depends on: technical, fundamental, detailed_profile, fetch_edgar, wikipedia, custom_research
    for task, name, fmt in [
        ("detailed_profile", "business_profile", "md"),
        ("wikipedia", "wikipedia_summary", "txt"),
        ("fetch_edgar", "filings_index", "json"),
    ]:
        _create_artifact_file(workdir, f"artifacts/{name}.{fmt}", content=f"{name} data")
        run_db("artifact-add", "--workdir", str(workdir),
               "--task-id", task, "--name", name,
               "--path", f"artifacts/{name}.{fmt}", "--format", fmt,
               "--summary", f"{name} data")

    rc, out = run_db("task-context", "--workdir", str(workdir), "--task-id", "chunk_documents")
    assert rc == 0
    assert out["task_id"] == "chunk_documents"
    assert len(out["artifacts"]) == 3
    names = {a["name"] for a in out["artifacts"]}
    assert names == {"business_profile", "wikipedia_summary", "filings_index"}


def test_task_context_artifact_fields(workdir):
    """Each artifact in task-context has required fields."""
    _create_artifact_file(workdir, "artifacts/business_profile.md", content="Business profile data")
    run_db("artifact-add", "--workdir", str(workdir),
           "--task-id", "detailed_profile", "--name", "business_profile",
           "--path", "artifacts/business_profile.md", "--format", "md",
           "--summary", "Business profile")

    rc, out = run_db("task-context", "--workdir", str(workdir), "--task-id", "chunk_documents")
    assert rc == 0
    for a in out["artifacts"]:
        assert "from_task" in a
        assert "name" in a
        assert "path" in a
        assert "format" in a
        assert "summary" in a


def test_task_context_no_deps(workdir):
    """task-context for a task without dependencies returns empty list."""
    rc, out = run_db("task-context", "--workdir", str(workdir), "--task-id", "profile")
    assert rc == 0
    assert out["task_id"] == "profile"
    assert out["artifacts"] == []


def test_task_context_missing_task(workdir):
    rc, out = run_db("task-context", "--workdir", str(workdir), "--task-id", "nonexistent")
    assert rc == 1
    assert out["status"] == "error"
    assert "nonexistent" in out["error"]


# ---------------------------------------------------------------------------
# var-set / var-get
# ---------------------------------------------------------------------------


def test_var_set_and_get(workdir):
    rc, out = run_db("var-set", "--workdir", str(workdir),
                     "--name", "symbol", "--value", "AAPL", "--source-task", "profile")
    assert rc == 0
    assert out["status"] == "ok"
    assert out["name"] == "symbol"
    assert out["value"] == "AAPL"

    rc, out = run_db("var-get", "--workdir", str(workdir), "--name", "symbol")
    assert rc == 0
    assert out["name"] == "symbol"
    assert out["value"] == "AAPL"
    assert out["source_task"] == "profile"


def test_var_get_all(workdir):
    run_db("var-set", "--workdir", str(workdir),
           "--name", "symbol", "--value", "AAPL", "--source-task", "profile")
    run_db("var-set", "--workdir", str(workdir),
           "--name", "company_name", "--value", "Apple Inc.", "--source-task", "profile")

    rc, out = run_db("var-get", "--workdir", str(workdir))
    assert rc == 0
    assert out == {"company_name": "Apple Inc.", "symbol": "AAPL"}


def test_var_get_empty(workdir):
    rc, out = run_db("var-get", "--workdir", str(workdir))
    assert rc == 0
    assert out == {}


def test_var_get_missing_name(workdir):
    rc, out = run_db("var-get", "--workdir", str(workdir), "--name", "nonexistent")
    assert rc == 1
    assert out["status"] == "error"


def test_var_set_upserts(workdir):
    """Setting the same variable twice updates the value."""
    run_db("var-set", "--workdir", str(workdir),
           "--name", "symbol", "--value", "OLD", "--source-task", "profile")
    run_db("var-set", "--workdir", str(workdir),
           "--name", "symbol", "--value", "NEW", "--source-task", "profile")

    rc, out = run_db("var-get", "--workdir", str(workdir), "--name", "symbol")
    assert rc == 0
    assert out["value"] == "NEW"


def test_var_set_stored_in_task_params(workdir):
    """Profile task params should include sets_vars from the YAML."""
    rc, out = run_db("task-get", "--workdir", str(workdir), "--task-id", "profile")
    assert rc == 0
    params = out["params"]
    assert "sets_vars" in params
    assert "symbol" in params["sets_vars"]
    assert params["sets_vars"]["symbol"]["key"] == "symbol"
    assert "company_name" in params["sets_vars"]
    assert params["sets_vars"]["company_name"]["key"] == "company_name"


# ---------------------------------------------------------------------------
# init — drafts_dir
# ---------------------------------------------------------------------------


def test_init_stores_drafts_dir(workdir):
    """init stores drafts_dir from DAG header in research table."""
    import sqlite3
    conn = sqlite3.connect(str(workdir / "research.db"))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT drafts_dir FROM research LIMIT 1").fetchone()
    conn.close()
    assert row["drafts_dir"] == "drafts"


# ---------------------------------------------------------------------------
# finding-add / finding-list
# ---------------------------------------------------------------------------


def test_finding_add(workdir):
    rc, out = run_db(
        "finding-add", "--workdir", str(workdir),
        "--task-id", "profile",
        "--content", "NVDA competes with AMD and Intel in discrete GPUs.",
        "--source", "artifacts/sec_10k_item1.md",
        "--tags", "competitive", "supply_chain",
    )
    assert rc == 0
    assert out["status"] == "ok"
    assert "id" in out


def test_finding_list_all(workdir):
    run_db("finding-add", "--workdir", str(workdir),
           "--task-id", "profile",
           "--content", "NVDA dominates GPU market.",
           "--source", "10-K", "--tags", "competitive")
    run_db("finding-add", "--workdir", str(workdir),
           "--task-id", "fundamental",
           "--content", "NVDA revenue grew 120% YoY.",
           "--source", "income_statement.csv", "--tags", "financial")
    rc, out = run_db("finding-list", "--workdir", str(workdir))
    assert rc == 0
    assert len(out) == 2


def test_finding_list_filter_by_tags(workdir):
    run_db("finding-add", "--workdir", str(workdir),
           "--task-id", "profile",
           "--content", "AMD is gaining share in data center.",
           "--source", "10-K", "--tags", "competitive", "financial")
    run_db("finding-add", "--workdir", str(workdir),
           "--task-id", "fundamental",
           "--content", "Gross margin expanded to 73%.",
           "--source", "income_statement.csv", "--tags", "financial")
    rc, out = run_db("finding-list", "--workdir", str(workdir), "--tags", "competitive")
    assert rc == 0
    assert len(out) == 1
    assert "AMD" in out[0]["content"]


def test_finding_add_requires_task_id(workdir):
    rc, out = run_db(
        "finding-add", "--workdir", str(workdir),
        "--content", "some content",
        "--tags", "competitive",
    )
    assert rc != 0
