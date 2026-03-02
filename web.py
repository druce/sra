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


@app.on_event("startup")
async def startup():
    global _sort_order
    _sort_order = load_sort_order(DAG_PATH)


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
