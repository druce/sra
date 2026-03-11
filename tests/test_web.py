import pytest
from datetime import datetime
from pathlib import Path

from httpx import AsyncClient, ASGITransport


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_list_reports_empty(tmp_path, monkeypatch):
    """Returns empty list when work/ has no completed reports."""
    import web
    monkeypatch.setattr(web, "WORK_DIR", tmp_path)
    import asyncio
    result = asyncio.run(web.list_reports())
    assert result == []


def test_list_reports_finds_completed(tmp_path, monkeypatch):
    """Returns entry for each work dir that has artifacts/final_report.md."""
    import web
    monkeypatch.setattr(web, "WORK_DIR", tmp_path)
    for name in ["ADSK_20260301", "MNDY_20260228"]:
        report = tmp_path / name / "artifacts" / "final_report.md"
        report.parent.mkdir(parents=True)
        report.write_text("# Report")
    import asyncio
    result = asyncio.run(web.list_reports())
    assert len(result) == 2
    syms = {r["ticker"] for r in result}
    assert syms == {"ADSK", "MNDY"}


def test_list_reports_sorted_descending(tmp_path, monkeypatch):
    """Reports sorted newest first."""
    import web
    monkeypatch.setattr(web, "WORK_DIR", tmp_path)
    for name in ["ADSK_20260101", "ADSK_20260301", "ADSK_20260201"]:
        report = tmp_path / name / "artifacts" / "final_report.md"
        report.parent.mkdir(parents=True)
        report.write_text("# Report")
    import asyncio
    result = asyncio.run(web.list_reports())
    dates = [r["date"] for r in result]
    assert dates == sorted(dates, reverse=True)


def test_list_reports_skips_incomplete(tmp_path, monkeypatch):
    """Dirs without final_report.md are ignored."""
    import web
    monkeypatch.setattr(web, "WORK_DIR", tmp_path)
    incomplete = tmp_path / "COIN_20260301"
    incomplete.mkdir()
    import asyncio
    assert asyncio.run(web.list_reports()) == []


@pytest.mark.anyio
async def test_reports_endpoint_empty(tmp_path, monkeypatch):
    """GET /reports returns empty list when no completed runs."""
    import web
    monkeypatch.setattr(web, "WORK_DIR", tmp_path)
    from web import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/reports")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_reports_endpoint_finds_reports(tmp_path, monkeypatch):
    """GET /reports returns completed runs."""
    import web
    monkeypatch.setattr(web, "WORK_DIR", tmp_path)
    report = tmp_path / "ADSK_20260301" / "artifacts" / "final_report.md"
    report.parent.mkdir(parents=True)
    report.write_text("# Report")
    from web import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/reports")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["ticker"] == "ADSK"


@pytest.mark.anyio
async def test_run_endpoint_rejects_empty_ticker(tmp_path, monkeypatch):
    """POST /run with empty ticker returns 400."""
    import web
    monkeypatch.setattr(web, "WORK_DIR", tmp_path)
    from web import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/run", json={"ticker": ""})
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_status_endpoint_not_found(tmp_path, monkeypatch):
    """GET /status/{run_id} returns 404 when workdir does not exist."""
    import web
    monkeypatch.setattr(web, "WORK_DIR", tmp_path)
    from web import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/status/FAKE_20260101")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_run_endpoint_rejects_invalid_ticker(tmp_path, monkeypatch):
    """POST /run with invalid ticker format returns 400."""
    import web
    monkeypatch.setattr(web, "WORK_DIR", tmp_path)
    from web import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/run", json={"ticker": "../evil"})
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_run_endpoint_rejects_duplicate(tmp_path, monkeypatch):
    """POST /run returns 409 when same run_id is already in progress."""
    import web
    monkeypatch.setattr(web, "WORK_DIR", tmp_path)

    # Inject a fake running process with returncode=None (still running)
    class FakeProc:
        returncode = None

    # Use today's date dynamically so the test doesn't go stale
    date_str = datetime.now().strftime("%Y%m%d")
    run_id = f"ADSK_{date_str}"
    monkeypatch.setitem(web.running, run_id, FakeProc())

    from web import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/run", json={"ticker": "ADSK"})
    assert resp.status_code == 409
