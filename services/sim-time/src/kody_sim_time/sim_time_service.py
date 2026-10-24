from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from threading import RLock
from typing import AsyncIterator, Callable, Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel


class SimulationControlAction(BaseModel):
    action: Literal["start", "pause", "resume", "stop"]


class SimulationTimeStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = RLock()
        self._elapsed_seconds = 0.0
        self._status: Literal["stopped", "running", "paused"] = "stopped"
        self._last_updated = clock()
        self._revision = 0
        self._event_id = 0
        self._events: list[dict[str, object]] = []

    def _advance(self) -> None:
        now = self._clock()
        if self._status == "running":
            self._elapsed_seconds += max(0.0, now - self._last_updated)
        self._last_updated = now

    def response(self) -> dict[str, object]:
        with self._lock:
            self._advance()
            return self._state()

    def _state(self) -> dict[str, object]:
        return {
            "revision": str(self._revision),
            "status": self._status,
            "running": self._status == "running",
            "elapsedSeconds": self._elapsed_seconds,
            "wallClockUtc": datetime.now(timezone.utc).isoformat(),
        }

    def _record_event(self, event_type: str) -> None:
        self._event_id += 1
        self._events.append({
            "id": self._event_id,
            "type": event_type,
            "state": self._state(),
        })
        self._events = self._events[-256:]

    def apply_action(self, control: SimulationControlAction, revision: str | None) -> dict[str, object]:
        with self._lock:
            if revision != str(self._revision):
                raise HTTPException(status_code=409, detail="simulation-time control has changed")
            self._advance()
            transitions: dict[
                Literal["start", "pause", "resume", "stop"],
                tuple[Literal["stopped", "running", "paused"], Literal["stopped", "running", "paused"]],
            ] = {
                "start": ("stopped", "running"),
                "pause": ("running", "paused"),
                "resume": ("paused", "running"),
                "stop": ("running", "stopped"),
            }
            required_status, next_status = transitions[control.action]
            if self._status != required_status:
                raise HTTPException(
                    status_code=409,
                    detail=f"cannot {control.action} while simulation time is {self._status}",
                )
            self._status = next_status
            self._revision += 1
            self._record_event({
                "start": "started",
                "pause": "paused",
                "resume": "resumed",
                "stop": "stopped",
            }[control.action])
            return self._state()

    def reset(self) -> dict[str, object]:
        with self._lock:
            self._elapsed_seconds = 0.0
            self._status = "stopped"
            self._last_updated = self._clock()
            self._revision += 1
            self._record_event("reset")
            return self._state()

    def events_since(self, event_id: int) -> list[dict[str, object]]:
        with self._lock:
            self._advance()
            return [event for event in self._events if event["id"] > event_id]


store = SimulationTimeStore()
app = FastAPI(title="Kody Simulation Time Service", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_methods=["GET", "PUT", "POST"],
    allow_headers=["Content-Type", "If-Match"],
)


@app.get("/api/kody/manifest")
def get_manifest() -> dict[str, object]:
    return {
        "kodyApiVersion": "1",
        "serviceId": "sim-time",
        "displayName": "SimTimeControl",
        "capabilities": {
            "workspace": {
                "id": "sim-time-control",
                "label": "SimTimeControl",
                "openPath": "/api/workspace",
                "revisionHeader": "If-Match",
            },
            "simulationControls": {
                "readPath": "/api/sim-time",
                "writePath": "/api/sim-time",
                "resetPath": "/api/sim-time/reset",
                "eventStreamPath": "/api/sim-time/events",
                "revisionHeader": "If-Match",
            },
        },
    }


@app.get("/api/workspace")
def get_workspace() -> dict[str, object]:
    return {
        "kind": "sim-time-control",
        "title": "SimTimeControl",
        "resources": {},
        "panes": [{
            "id": "time",
            "label": "Simulation time",
            "description": "Control the shared simulation clock.",
            "summary": {"metrics": [], "items": []},
        }],
        "timeline": {"description": "Simulation-time progression."},
    }


@app.get("/api/sim-time")
def get_simulation_time() -> dict[str, object]:
    return store.response()


@app.put("/api/sim-time")
def update_simulation_time(
    control: SimulationControlAction,
    if_match: str | None = Header(default=None),
) -> dict[str, object]:
    return store.apply_action(control, if_match)


@app.post("/api/sim-time/reset")
def reset_simulation_time() -> dict[str, object]:
    return store.reset()


def encode_event(event_type: str, state: dict[str, object], event_id: int | None = None) -> str:
    identifier = f"id: {event_id}\n" if event_id is not None else ""
    return f"{identifier}event: {event_type}\ndata: {json.dumps(state)}\n\n"


@app.get("/api/sim-time/events")
async def stream_simulation_time_events(
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    try:
        event_id = max(0, int(last_event_id or "0"))
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Last-Event-ID must be an integer") from error

    async def events() -> AsyncIterator[str]:
        nonlocal event_id
        yield encode_event("state", store.response())
        last_sync_second = int(time.time())
        while True:
            for event in store.events_since(event_id):
                event_id = int(event["id"])
                yield encode_event(
                    str(event["type"]),
                    event["state"],
                    event_id,
                )
            current_second = int(time.time())
            if current_second > last_sync_second:
                yield encode_event("sync", store.response())
                last_sync_second = current_second
            await asyncio.sleep(max(0.01, min(0.1, 1 - time.time() % 1)))

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
