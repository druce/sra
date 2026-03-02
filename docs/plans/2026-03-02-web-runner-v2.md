# Web Runner v2 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Local FastAPI web app to run the equity research pipeline with dark neon UI, WebSocket log streaming, DAG-ordered task grid, and past report history.

**Architecture:** FastAPI backend serves `static/index.html`. `POST /run` spawns `research.py` as subprocess. WebSocket tails `*_stream.log` files and streams to browser as JSON. Status endpoint queries `db.py status` and returns tasks ordered by `sort_order` from `dags/sra.yaml`. On completion, opens Typora.

**Tech Stack:** FastAPI, uvicorn, websockets, Alpine.js (CDN), vanilla HTML/CSS (all deps already in pyproject.toml). Tests use pytest + httpx (AsyncClient). Reference `test.html` for the exact visual design — `static/index.html` is a real-API adaptation of that prototype.

---

### Task 1: Backend scaffolding — report discovery + task ordering

**Files:**
- Create: `web.py`
- Create: `tests/test_web.py`

**Step 1: Install httpx for async test client**

```bash
uv add --dev httpx
```

Expected: httpx added to pyproject.toml dev deps.

**Step 2: Write failing tests for `list_reports` and `load_sort_order`**

Create `tests/test_web.py`:

```python
import pytest
from pathlib import Path


def test_list_reports_empty(tmp_path):
    """Returns empty list when work/ has no completed reports."""
    from web import list_reports
    assert list_reports(tmp_path) == []


def test_list_reports_finds_completed(tmp_path):
    """Returns entry for each work dir that has artifacts/final_report.md."""
    from web import list_reports
    # Create two completed runs
    for name in ["ADSK_20260301", "MNDY_20260228"]:
        report = tmp_path / name / "artifacts" / "final_report.md"
        report.parent.mkdir(parents=True)
        report.write_text("# Report")
    result = list_reports(tmp_path)
    assert len(result) == 2
    syms = {r["ticker"] for r in result}
    assert syms == {"ADSK", "MNDY"}


def test_list_reports_sorted_descending(tmp_path):
    """Reports sorted newest first."""
    from web import list_reports
    for name in ["ADSK_20260101", "ADSK_20260301", "ADSK_20260201"]:
        report = tmp_path / name / "artifacts" / "final_report.md"
        report.parent.mkdir(parents=True)
        report.write_text("# Report")
    result = list_reports(tmp_path)
    dates = [r["date"] for r in result]
    assert dates == sorted(dates, reverse=True)


def test_list_reports_skips_incomplete(tmp_path):
    """Dirs without final_report.md are ignored."""
    from web import list_reports
    incomplete = tmp_path / "COIN_20260301"
    incomplete.mkdir()
    assert list_reports(tmp_path) == []


def test_load_sort_order(tmp_path):
    """Parses sort_order from DAG YAML into {task_id: int} dict."""
    from web import load_sort_order
    dag = tmp_path / "test.yaml"
    dag.write_text("""
dag:
  version: 2
  name: Test
tasks:
  profile:
    sort_order: 1
    type: python
    config:
      script: x.py
  technical:
    sort_order: 2
    type: python
    config:
      script: x.py
""")
    order = load_sort_order(dag)
    assert order == {"profile": 1, "technical": 2}
```

**Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/test_web.py -v
```

Expected: `ModuleNotFoundError: No module named 'web'` or similar — confirms tests are wired.

**Step 4: Create `web.py` with `list_reports` and `load_sort_order`**

```python
#!/usr/bin/env python3
"""
web.py — FastAPI app for running equity research pipeline.

Usage: uv run uvicorn web:app --reload --port 8000
"""

import asyncio
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).parent
WORK_DIR = ROOT / "work"
DB_PY = ROOT / "skills" / "db.py"
DAG_PATH = ROOT / "dags" / "sra.yaml"

app = FastAPI()

# Loaded once at startup
_sort_order: dict[str, int] = {}

# Track running pipelines: {run_id: asyncio.subprocess.Process}
running: dict[str, asyncio.subprocess.Process] = {}


def load_sort_order(dag_path: Path) -> dict[str, int]:
    """Return {task_id: sort_order} from DAG YAML."""
    with open(dag_path) as f:
        dag = yaml.safe_load(f)
    tasks = dag.get("tasks", {})
    return {task_id: cfg.get("sort_order", 999) for task_id, cfg in tasks.items()}


def list_reports(work_dir: Path) -> list[dict]:
    """
    Scan work_dir for completed runs. Returns list of dicts sorted newest first:
      {run_id, ticker, date, path}
    where date is YYYY-MM-DD string parsed from TICKER_YYYYMMDD dir name.
    """
    pattern = re.compile(r"^([A-Z]+)_(\d{8})$")
    results = []
    if not work_dir.exists():
        return []
    for d in work_dir.iterdir():
        if not d.is_dir():
            continue
        m = pattern.match(d.name)
        if not m:
            continue
        report = d / "artifacts" / "final_report.md"
        if not report.exists():
            continue
        ticker, datestr = m.group(1), m.group(2)
        date = f"{datestr[:4]}-{datestr[4:6]}-{datestr[6:]}"
        results.append({
            "run_id": d.name,
            "ticker": ticker,
            "date": date,
            "path": str(report),
        })
    results.sort(key=lambda r: r["date"], reverse=True)
    return results


@app.on_event("startup")
async def startup():
    global _sort_order
    _sort_order = load_sort_order(DAG_PATH)


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
```

**Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_web.py -v
```

Expected: all 5 tests PASS.

**Step 6: Commit**

```bash
git add web.py tests/test_web.py pyproject.toml uv.lock
git commit -m "feat: add web.py scaffolding with report discovery and sort_order loading"
```

---

### Task 2: `/run`, `/status`, `/reports`, `/open` endpoints

**Files:**
- Modify: `web.py`
- Modify: `tests/test_web.py`

**Step 1: Write failing tests for the HTTP endpoints**

Append to `tests/test_web.py`:

```python
import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_reports_endpoint_empty(tmp_path, monkeypatch):
    """GET /reports returns empty list when no completed runs."""
    monkeypatch.setattr("web.WORK_DIR", tmp_path)
    from web import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/reports")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_reports_endpoint_finds_reports(tmp_path, monkeypatch):
    """GET /reports returns completed runs."""
    monkeypatch.setattr("web.WORK_DIR", tmp_path)
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
async def test_run_endpoint_rejects_empty_ticker(monkeypatch, tmp_path):
    """POST /run with empty ticker returns 400."""
    monkeypatch.setattr("web.WORK_DIR", tmp_path)
    from web import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/run", json={"ticker": ""})
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_status_endpoint_not_found(tmp_path, monkeypatch):
    """GET /status/{run_id} returns 404 when workdir does not exist."""
    monkeypatch.setattr("web.WORK_DIR", tmp_path)
    from web import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/status/FAKE_20260101")
    assert resp.status_code == 404
```

Also add `anyio` to dev deps:

```bash
uv add --dev anyio pytest-anyio
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_web.py::test_reports_endpoint_empty -v
```

Expected: FAIL — endpoints not yet defined.

**Step 3: Add endpoints to `web.py`**

Add after `list_reports` function and before `startup`:

```python
@app.get("/reports")
async def get_reports():
    return list_reports(WORK_DIR)


@app.post("/run")
async def run_pipeline(body: dict):
    ticker = body.get("ticker", "").strip().upper()
    if not ticker:
        return JSONResponse({"error": "ticker required"}, status_code=400)

    date = datetime.now().strftime("%Y%m%d")
    run_id = f"{ticker}_{date}"

    proc = running.get(run_id)
    if proc and proc.returncode is None:
        return JSONResponse({"error": "already running"}, status_code=409)

    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(ROOT / "research.py"), ticker, "--date", date,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    running[run_id] = proc
    return {"run_id": run_id, "workdir": f"work/{run_id}"}


@app.get("/status/{run_id}")
async def get_status(run_id: str):
    workdir = WORK_DIR / run_id
    if not workdir.exists():
        return JSONResponse({"error": "not found"}, status_code=404)

    try:
        result = subprocess.run(
            [sys.executable, str(DB_PY), "status", "--workdir", str(workdir)],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            # Sort tasks by sort_order from DAG YAML
            if "tasks" in data:
                data["tasks"].sort(key=lambda t: _sort_order.get(t.get("task_id", ""), 999))
            return data
    except Exception:
        pass
    return JSONResponse({"error": "status unavailable"}, status_code=500)


@app.post("/open/{run_id}")
async def open_report(run_id: str):
    report = WORK_DIR / run_id / "artifacts" / "final_report.md"
    if not report.exists():
        return JSONResponse({"error": "report not found"}, status_code=404)
    subprocess.Popen(["open", "-a", "Typora", str(report)])
    return {"ok": True}
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_web.py -v
```

Expected: all tests PASS.

**Step 5: Commit**

```bash
git add web.py tests/test_web.py pyproject.toml uv.lock
git commit -m "feat: add /run, /status, /reports, /open endpoints to web.py"
```

---

### Task 3: WebSocket log tailing

**Files:**
- Modify: `web.py`

No unit tests for the WS endpoint — it requires a live subprocess. Verified in Task 5 smoke test.

**Step 1: Add WebSocket endpoint to `web.py`**

Add after the `/open` endpoint (before the `app.mount` line):

```python
@app.websocket("/ws/{run_id}")
async def websocket_log(ws: WebSocket, run_id: str):
    await ws.accept()
    workdir = WORK_DIR / run_id

    # Wait up to 10s for workdir to appear (subprocess races with WS connect)
    for _ in range(50):
        if workdir.exists():
            break
        await asyncio.sleep(0.2)

    if not workdir.exists():
        await ws.send_json({"type": "error", "text": f"workdir not found: {run_id}"})
        await ws.close()
        return

    offsets: dict[Path, int] = {}

    async def drain(final: bool = False):
        """Read new bytes from all *_stream.log files."""
        logs = sorted(workdir.glob("*_stream.log"))
        for log_path in logs:
            if log_path not in offsets:
                offsets[log_path] = 0
            try:
                size = log_path.stat().st_size
            except FileNotFoundError:
                continue
            if size > offsets[log_path]:
                with open(log_path, "r", errors="replace") as f:
                    f.seek(offsets[log_path])
                    text = f.read()
                    offsets[log_path] = f.tell()
                if text.strip():
                    task_name = log_path.stem.replace("_stream", "")
                    await ws.send_json({"type": "log", "task": task_name, "text": text})

    try:
        while True:
            await drain()

            proc = running.get(run_id)
            if proc and proc.returncode is not None:
                # Final drain to catch any last bytes
                await asyncio.sleep(0.3)
                await drain(final=True)

                report = workdir / "artifacts" / "final_report.md"
                await ws.send_json({
                    "type": "complete",
                    "success": proc.returncode == 0,
                    "report": str(report) if report.exists() else None,
                })

                if report.exists():
                    subprocess.Popen(["open", "-a", "Typora", str(report)])
                break

            await asyncio.sleep(0.2)

    except WebSocketDisconnect:
        pass
```

**Step 2: Verify import still works**

```bash
uv run python -c "from web import app; print('ok')"
```

Expected: `ok`

**Step 3: Commit**

```bash
git add web.py
git commit -m "feat: add WebSocket log tailing endpoint to web.py"
```

---

### Task 4: Frontend — `static/index.html`

**Files:**
- Create: `static/index.html`

`test.html` is the approved prototype. This task adapts it to use real API calls by replacing the simulated `run()` / `demo()` methods with live fetch/WebSocket calls. Keep all CSS and structure identical to `test.html` — only the Alpine.js data object changes.

**Step 1: Create `static/` directory**

```bash
mkdir -p static
```

**Step 2: Create `static/index.html`**

Copy `test.html` as the base, then replace the entire `<script>` block (the `sra4()` function) with the real implementation below. All HTML structure and all CSS stays identical.

The new `sra4()` function:

```javascript
function sra4() {
  return {
    tickerInput: '',
    ticker: '',
    running: false,
    completed: false,
    pct: 0,
    tasks: [],
    logs: [],
    reports: [],
    ws: null,
    pollTimer: null,

    async init() {
      await this.loadReports();
    },

    async loadReports() {
      try {
        const resp = await fetch('/reports');
        this.reports = await resp.json();
      } catch (e) {
        console.error('Failed to load reports', e);
      }
    },

    log(html) {
      this.logs.push(html);
      this.$nextTick(() => {
        const el = document.getElementById('logbox');
        if (el) el.scrollTop = el.scrollHeight;
      });
    },

    flashError() {
      const el = document.querySelector('.ticker-input');
      if (!el) return;
      el.style.borderColor = 'var(--red)';
      el.style.boxShadow = '0 0 10px rgba(255,64,96,0.4)';
      setTimeout(() => {
        el.style.borderColor = '';
        el.style.boxShadow = '';
      }, 800);
    },

    async launch() {
      const sym = this.tickerInput.trim().toUpperCase();
      if (!sym) { this.flashError(); return; }
      if (this.running) return;

      this.ticker = sym;
      this.running = true;
      this.completed = false;
      this.pct = 0;
      this.logs = [];
      this.tasks = [];

      let run_id;
      try {
        const resp = await fetch('/run', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ticker: sym}),
        });
        if (resp.status === 409) {
          this.log('<span class="lwn">⚠ Already running — wait for current run to complete</span>');
          this.running = false;
          return;
        }
        if (!resp.ok) {
          this.log(`<span class="ler">✗ Error starting run: ${resp.status}</span>`);
          this.running = false;
          return;
        }
        const data = await resp.json();
        run_id = data.run_id;
      } catch (e) {
        this.log(`<span class="ler">✗ Network error: ${e.message}</span>`);
        this.running = false;
        return;
      }

      this.connectWS(run_id);
      this.startPoll(run_id);
    },

    connectWS(run_id, retry = false) {
      if (retry) {
        this.log('<span class="lwn">⚠ Reconnecting log stream...</span>');
      }
      const ws = new WebSocket(`ws://${location.host}/ws/${run_id}`);
      this.ws = ws;

      ws.onmessage = (evt) => {
        const msg = JSON.parse(evt.data);
        if (msg.type === 'log') {
          // Escape HTML in task name and text, then apply styling
          const task = msg.task.replace(/</g, '&lt;');
          const text = msg.text.replace(/\n/g, '<br>');
          this.log(`<span class="lt">[${task}]</span> ${text}`);
        } else if (msg.type === 'complete') {
          this.stopPoll();
          this.running = false;
          this.completed = true;
          this.pct = 100;
          this.log(msg.success
            ? '<span class="lok">✓ Research complete · Opening in Typora</span>'
            : '<span class="ler">✗ Research failed — check logs</span>'
          );
          this.loadReports();
        } else if (msg.type === 'error') {
          this.log(`<span class="ler">✗ ${msg.text}</span>`);
          this.stopPoll();
          this.running = false;
        }
      };

      ws.onerror = () => {
        if (this.running) {
          setTimeout(() => this.connectWS(run_id, true), 1000);
        }
      };

      ws.onclose = () => {
        if (this.running) {
          setTimeout(() => this.connectWS(run_id, true), 1000);
        }
      };
    },

    startPoll(run_id) {
      this.pollTimer = setInterval(async () => {
        try {
          const resp = await fetch(`/status/${run_id}`);
          if (!resp.ok) return;
          const data = await resp.json();
          if (data.tasks) {
            this.tasks = data.tasks.map(t => ({
              id: t.task_id,
              state: t.status === 'complete' ? 'complete'
                   : t.status === 'running'  ? 'running'
                   : t.status === 'failed'   ? 'failed'
                   : 'pending',
            }));
            const total = this.tasks.length;
            const done  = this.tasks.filter(t => t.state === 'complete').length;
            if (total > 0) this.pct = Math.min(99, Math.round((done / total) * 100));
          }
        } catch (e) {
          // swallow transient poll errors
        }
      }, 2000);
    },

    stopPoll() {
      if (this.pollTimer) {
        clearInterval(this.pollTimer);
        this.pollTimer = null;
      }
    },

    openReport(r) {
      fetch(`/open/${r.run_id}`, {method: 'POST'}).catch(() => {});
    },

    get counts() {
      return {
        pending:  this.tasks.filter(t => t.state === 'pending').length,
        running:  this.tasks.filter(t => t.state === 'running').length,
        complete: this.tasks.filter(t => t.state === 'complete').length,
        failed:   this.tasks.filter(t => t.state === 'failed').length,
      };
    },
  };
}
```

Key differences from `test.html`:
- `init()` calls `loadReports()` on mount (`x-init="init()"` on the root div)
- `launch()` does `POST /run`, `connectWS()`, `startPoll()` instead of simulated waves
- `demo()` removed — the Demo button can be removed from the HTML or left in and wired to launch with a preset ticker
- `openReport()` calls `POST /open/{run_id}` instead of logging a message
- `tasks[]` populated from `/status` poll, not hardcoded
- `reports[]` populated from `GET /reports`, not hardcoded

Also update the root div attribute in the HTML:
```html
<div id="app" x-data="sra4()" x-init="init()">
```

And remove the Demo button (or wire it to `tickerInput='ADSK'; launch()`):
```html
<button class="demo-btn" @click="tickerInput='ADSK'; launch()" :disabled="running" x-show="!running">
  Demo
</button>
```

**Step 3: Verify it imports cleanly**

```bash
uv run python -c "from web import app; print('ok')"
```

Expected: `ok`

**Step 4: Commit**

```bash
git add static/index.html
git commit -m "feat: add static/index.html frontend with Alpine.js and real API integration"
```

---

### Task 5: Smoke test

**Files:** none

**Step 1: Run all tests**

```bash
uv run pytest tests/test_web.py -v
```

Expected: all tests PASS.

**Step 2: Start the server**

```bash
uv run uvicorn web:app --reload --port 8000
```

Expected: server starts, no import errors.

**Step 3: Open the browser**

Navigate to `http://localhost:8000`. Verify:
- [ ] Dark navy UI renders correctly (matches test.html)
- [ ] Past reports sidebar populates from `work/`
- [ ] Clicking a past report opens Typora
- [ ] Ticker input accepts text, Run button enabled
- [ ] Typing a ticker and pressing Enter triggers run
- [ ] Progress bar and status bar appear while running
- [ ] Task pills update in YAML order as pipeline progresses
- [ ] Log area streams output with colored task labels
- [ ] On completion: all pills green, status bar shows "TICKER — Complete", Typora opens

**Step 4: Final commit**

```bash
git add -A
git commit -m "feat: web runner v2 complete — FastAPI + Alpine.js pipeline runner"
```
