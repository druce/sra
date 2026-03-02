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
    return {task_id: (cfg or {}).get("sort_order", 999) for task_id, cfg in tasks.items()}


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


@app.on_event("startup")
async def startup():
    global _sort_order
    _sort_order = load_sort_order(DAG_PATH)


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
