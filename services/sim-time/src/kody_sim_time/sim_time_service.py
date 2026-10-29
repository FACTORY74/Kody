# Kody — General-Purpose Mapping Application
#
# A Python-based mapping application with a JavaScript/HTML web interface and a plugin architecture for extending functionality.
#
# Copyright (c) 2026 Factory74
#
# SPDX-License-Identifier: MIT
#
# Distributed under the MIT License. See LICENSE for details.
# Provided "AS IS", without warranty of any kind.

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import AsyncIterator, Callable, Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

LOGGER = logging.getLogger(__name__)
ROOT_DIRECTORY = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get("KODY_DATA_DIRECTORY", ROOT_DIRECTORY / "data"))
STATE_FILE = DATA_ROOT / "sim-time.json"


class SimulationControlAction(BaseModel):
    action: Literal["start", "pause", "resume", "stop"]


class SimulationUpdateRate(BaseModel):
    updates_per_second: int = Field(alias="updatesPerSecond", ge=1, le=60)


class PersistedSimulationTime(BaseModel):
    schemaVersion: Literal[1] = 1
    elapsedSeconds: float = Field(ge=0)
    updatesPerSecond: float = Field(ge=1, le=60)
    status: Literal["stopped", "running", "paused"]
    revision: int = Field(ge=0)
    savedAtUnixSeconds: float = Field(ge=0)


class SimulationTimeStore:
    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        state_path: Path | None = None,
    ) -> None:
        self._clock = clock
        self._wall_clock = wall_clock
        self._state_path = state_path
        self._lock = RLock()
        persisted = self._load_state()
        self._elapsed_seconds = persisted.elapsedSeconds if persisted else 0.0
        self._updates_per_second = persisted.updatesPerSecond if persisted else 1.0
        self._status: Literal["stopped", "running", "paused"] = persisted.status if persisted else "stopped"
        if persisted and self._status == "running":
            self._elapsed_seconds += max(0.0, self._wall_clock() - persisted.savedAtUnixSeconds)
        self._last_updated = clock()
        self._revision = persisted.revision if persisted else 0
        self._last_persisted_elapsed_seconds = self._elapsed_seconds
        self._event_id = 0
        self._events: list[dict[str, object]] = []
        self._persist()

    def _load_state(self) -> PersistedSimulationTime | None:
        if self._state_path is None or not self._state_path.is_file():
            return None
        try:
            return PersistedSimulationTime.model_validate_json(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise RuntimeError(f"simulation-time state is invalid: cannot read {self._state_path.name}") from error

    def _persist(self) -> None:
        if self._state_path is None:
            return
        state = PersistedSimulationTime(
            elapsedSeconds=self._elapsed_seconds,
            updatesPerSecond=self._updates_per_second,
            status=self._status,
            revision=self._revision,
            savedAtUnixSeconds=self._wall_clock(),
        )
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        temporary_path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporary_path.replace(self._state_path)
        self._last_persisted_elapsed_seconds = self._elapsed_seconds

    def _advance(self) -> None:
        now = self._clock()
        if self._status == "running":
            self._elapsed_seconds += max(0.0, now - self._last_updated)
        self._last_updated = now
        if abs(self._elapsed_seconds - self._last_persisted_elapsed_seconds) >= 1:
            self._persist()

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
            "updatesPerSecond": self._updates_per_second,
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
            self._persist()
            self._record_event({
                "start": "started",
                "pause": "paused",
                "resume": "resumed",
                "stop": "stopped",
            }[control.action])
            return self._state()

    def update_rate(self, update: SimulationUpdateRate, revision: str | None) -> dict[str, object]:
        with self._lock:
            if revision != str(self._revision):
                raise HTTPException(status_code=409, detail="simulation-time control has changed")
            self._advance()
            if update.updates_per_second != self._updates_per_second:
                self._updates_per_second = update.updates_per_second
                self._revision += 1
                self._persist()
                self._record_event("rate-changed")
            return self._state()

    def reset(self) -> dict[str, object]:
        with self._lock:
            self._elapsed_seconds = 0.0
            self._status = "stopped"
            self._last_updated = self._clock()
            self._revision += 1
            self._persist()
            self._record_event("reset")
            return self._state()

    def events_since(self, event_id: int) -> list[dict[str, object]]:
        with self._lock:
            self._advance()
            return [event for event in self._events if event["id"] > event_id]


store = SimulationTimeStore(state_path=STATE_FILE)
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
                "updateRatePath": "/api/sim-time/update-rate",
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
    request: Request,
    if_match: str | None = Header(default=None),
) -> dict[str, object]:
    client_host = request.client.host if request.client else "unknown"
    LOGGER.info(
        "SimTimeControl received %s from %s (%s)",
        control.action,
        client_host,
        request.headers.get("user-agent", "unknown"),
    )
    return store.apply_action(control, if_match)


@app.put("/api/sim-time/update-rate")
def update_simulation_update_rate(
    update: SimulationUpdateRate,
    if_match: str | None = Header(default=None),
) -> dict[str, object]:
    return store.update_rate(update, if_match)


@app.post("/api/sim-time/reset")
def reset_simulation_time(request: Request) -> dict[str, object]:
    client_host = request.client.host if request.client else "unknown"
    LOGGER.warning(
        "SimTimeControl reset requested by %s (%s)",
        client_host,
        request.headers.get("user-agent", "unknown"),
    )
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
        next_sync = time.monotonic()
        while True:
            for event in store.events_since(event_id):
                event_id = int(event["id"])
                yield encode_event(
                    str(event["type"]),
                    event["state"],
                    event_id,
                )
            current_time = time.monotonic()
            if current_time >= next_sync:
                state = store.response()
                yield encode_event("sync", state)
                next_sync = current_time + 1 / float(state["updatesPerSecond"])
            await asyncio.sleep(max(0.01, min(0.1, next_sync - time.monotonic())))

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
