"""
Service mode HTTP server for the agent runner.

Wraps run_agent() in a persistent FastAPI server so the agent can be triggered
on demand via HTTP rather than on a cron schedule.

Endpoints:
  GET  /health           — liveness/readiness probe (always 200)
  POST /trigger          — start a new agent run; returns 202 with run_id,
                           or 409 if a run is already in progress
  GET  /status/{run_id}  — poll for result until status is "complete" or "failed"

Only one run may be active at a time. Results are retained in memory for
RESULT_TTL_SECONDS (default 3600) before being evicted.

Environment variables (in addition to those read by run_agent.py):
  SERVICE_PORT          Port to listen on. Default: 8080
  RESULT_TTL_SECONDS    Seconds to retain completed run results. Default: 3600
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from run_agent import run_agent

log = logging.getLogger(__name__)

RESULT_TTL = int(os.environ.get("RESULT_TTL_SECONDS", "3600"))

app = FastAPI()


@dataclass
class RunRecord:
    run_id: str
    status: str  # "running" | "complete" | "failed"
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    result: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        d: dict = {
            "run_id": self.run_id,
            "status": self.status,
            "started_at": self.started_at,
        }
        if self.finished_at is not None:
            d["finished_at"] = self.finished_at
        if self.result is not None:
            d["result"] = self.result
        if self.error is not None:
            d["error"] = self.error
        return d


class RunState:
    def __init__(self) -> None:
        self.current: Optional[RunRecord] = None
        self.results: dict[str, RunRecord] = {}

    def evict_expired(self) -> None:
        cutoff = time.time() - RESULT_TTL
        expired = [
            rid for rid, r in self.results.items()
            if r.finished_at is not None and r.finished_at < cutoff
        ]
        for rid in expired:
            del self.results[rid]
            log.debug("Evicted expired run result %s", rid)


state = RunState()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/trigger")
async def trigger() -> JSONResponse:
    if state.current is not None:
        raise HTTPException(status_code=409, detail=state.current.to_dict())
    run_id = uuid4().hex
    record = RunRecord(run_id=run_id, status="running")
    state.current = record
    asyncio.create_task(_execute(run_id, record))
    return JSONResponse(status_code=202, content=record.to_dict())


@app.get("/status/{run_id}")
async def status(run_id: str) -> dict:
    record: Optional[RunRecord] = None
    if state.current and state.current.run_id == run_id:
        record = state.current
    else:
        record = state.results.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return record.to_dict()


async def _execute(run_id: str, record: RunRecord) -> None:
    try:
        result = await run_agent()
        record.status = "complete"
        record.result = result
        record.finished_at = time.time()
        log.info("Run %s completed", run_id)
    except Exception as exc:
        log.error("Run %s failed: %s", run_id, exc, exc_info=True)
        record.status = "failed"
        record.error = str(exc)
        record.finished_at = time.time()
    finally:
        state.results[run_id] = record
        state.current = None
        state.evict_expired()
