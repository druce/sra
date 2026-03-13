"""Tests for db.py task commands: task-ready, task-get, task-update, task-context."""

from db_test_helpers import run_db, create_artifact_file


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
    # Also complete chunk wave, research wave, and post-research pipeline tasks
    for tid in ("chunk_documents", "tag_chunks", "build_index",
                "research_profile", "research_business", "research_competitive",
                "research_supply_chain", "research_financial", "research_valuation",
                "research_risk_news", "chunk_research", "tag_research", "append_index"):
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
        create_artifact_file(workdir, f"artifacts/{name}.{fmt}", content=f"{name} data")
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
    create_artifact_file(workdir, "artifacts/business_profile.md", content="Business profile data")
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
