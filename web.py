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
    if not re.fullmatch(r"[A-Z]{1,10}", ticker):
        return JSONResponse({"error": "invalid ticker"}, status_code=400)

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
    if not re.fullmatch(r"[A-Z]{1,10}_\d{8}", run_id):
        return JSONResponse({"error": "invalid run_id"}, status_code=400)
    workdir = WORK_DIR / run_id
    if not workdir.exists():
        return JSONResponse({"error": "not found"}, status_code=404)

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(DB_PY), "status", "--workdir", str(workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        if proc.returncode == 0:
            data = json.loads(stdout.decode())
            # Sort tasks by sort_order from DAG YAML
            if "tasks" in data:
                data["tasks"].sort(key=lambda t: _sort_order.get(t.get("task_id", ""), 999))
            return data
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("status query failed for %s: %s", run_id, e)
    return JSONResponse({"error": "status unavailable"}, status_code=500)


@app.post("/open/{run_id}")
async def open_report(run_id: str):
    if not re.fullmatch(r"[A-Z]{1,10}_\d{8}", run_id):
        return JSONResponse({"error": "invalid run_id"}, status_code=400)
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

    async def drain():
        """Read new bytes from all *_stream.log files and send to client."""
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
                # Final drain to catch any last bytes written before exit
                await asyncio.sleep(0.3)
                await drain()

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


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
