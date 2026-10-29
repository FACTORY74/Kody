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

"""Manage Kody shapes and publish point-containment activities.

Shape definitions are kept in memory with a derived geographic bounds box.
Incoming point events are admitted to a bounded queue, filtered against those
bounds, checked with shape-specific containment logic, and sent to the
configured output service. A background poller converts TrackSim position
features into point events, allowing ShapeServer to operate as a live map data
layer and containment processor.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from fastapi import Body, FastAPI, Header, HTTPException, Request as FastAPIRequest, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from .shape_intersections import (
    AnnularSectorShape,
    Bounds,
    CircleShape,
    PolygonShape,
    RectangleShape,
    Shape,
    bearing_degrees,
    calculate_bounds,
    contains,
    destination,
    haversine_metres,
    point_in_polygon,
    shape_ring,
)

ROOT_DIRECTORY = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT_DIRECTORY / "config" / "shape-server.json"
MATCH_HISTORY_LIMIT = 1_000
COLOUR_PALETTE = ("#35b8ff", "#ff8d4a", "#9d7cff", "#4ed8a7", "#ffcf4a", "#fb6f92")
LOGGER = logging.getLogger(__name__)


class StoredShape(BaseModel):
    shape: Shape
    bounds: Bounds


class PointEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eventId: str | None = Field(default=None, max_length=200)
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    timestamp: str | None = Field(default=None, max_length=100)
    properties: dict[str, Any] = Field(default_factory=dict)


class ShapeServerConfiguration(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", validate_default=True)

    output_url: HttpUrl | None = Field(default="http://127.0.0.1:8017/api/interactions", alias="outputUrl")
    processing_rate: int = Field(default=50, alias="processingRate", ge=1, le=10_000)
    queue_capacity: int = Field(default=250, alias="queueCapacity", ge=1, le=100_000)
    track_source_url: HttpUrl | None = Field(default="http://127.0.0.1:8003", alias="trackSourceUrl")
    track_poll_interval_milliseconds: int = Field(
        default=100,
        alias="trackPollIntervalMilliseconds",
        ge=100,
        le=60_000,
    )


def shape_geojson(shape: Shape, bounds: Bounds) -> dict[str, object]:
    """Return a map-renderable GeoJSON feature with ShapeServer metadata."""
    return {
        "type": "Feature",
        "id": shape.id,
        "properties": {
            "shapeId": shape.id,
            "name": shape.name,
            "color": shape.color,
            "shapeType": shape.type,
            "bounds": bounds.model_dump(),
        },
        "geometry": {"type": "Polygon", "coordinates": [shape_ring(shape)]},
    }


class ShapeStore:
    """Thread-safe, in-memory ShapeServer definitions and derived bounds."""

    def __init__(self) -> None:
        self.lock = Lock()
        self.shapes: dict[str, StoredShape] = {}
        self.revision_number = 0

    def revision(self) -> str:
        """Return the opaque revision associated with the current shape set."""
        return hashlib.sha256(str(self.revision_number).encode("ascii")).hexdigest()

    def list(self) -> list[StoredShape]:
        """Return a consistent snapshot of all stored shape definitions."""
        with self.lock:
            return list(self.shapes.values())

    def create(self, shape: Shape) -> StoredShape:
        """Store a new shape after assigning its identifier, color, and bounds."""
        with self.lock:
            shape_id = shape.id or str(uuid.uuid4())
            if shape_id in self.shapes:
                raise HTTPException(status_code=409, detail=f"shape id already exists: {shape_id}")
            # Assign omitted values once so every stored shape is renderable.
            assigned = shape.model_copy(update={"id": shape_id, "color": shape.color or COLOUR_PALETTE[self.revision_number % len(COLOUR_PALETTE)]})
            # Bounds are cached alongside the shape for fast event rejection.
            stored = StoredShape(shape=assigned, bounds=calculate_bounds(assigned))
            self.shapes[shape_id] = stored
            self.revision_number += 1
            return stored

    def update(self, shape_id: str, shape: Shape) -> StoredShape:
        """Replace an existing shape and recalculate its derived bounds."""
        with self.lock:
            existing = self.shapes.get(shape_id)
            if existing is None:
                raise HTTPException(status_code=404, detail=f"shape not found: {shape_id}")
            if shape.id is not None and shape.id != shape_id:
                raise HTTPException(status_code=400, detail="shape id in the body must match the path")
            # Preserve the prior colour when an update intentionally omits one.
            assigned = shape.model_copy(update={"id": shape_id, "color": shape.color or existing.shape.color})
            stored = StoredShape(shape=assigned, bounds=calculate_bounds(assigned))
            self.shapes[shape_id] = stored
            self.revision_number += 1
            return stored

    def delete(self, shape_id: str) -> None:
        """Remove a shape or report that the requested identifier is unknown."""
        with self.lock:
            if shape_id not in self.shapes:
                raise HTTPException(status_code=404, detail=f"shape not found: {shape_id}")
            del self.shapes[shape_id]
            self.revision_number += 1


class ConfigurationStore:
    """Persist ShapeServer configuration with optimistic revision checking."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = Lock()
        self.configuration = self._load()

    def _load(self) -> ShapeServerConfiguration:
        """Load the saved configuration, or use service defaults when absent."""
        if not self.path.is_file():
            return ShapeServerConfiguration()
        return ShapeServerConfiguration.model_validate_json(self.path.read_text(encoding="utf-8"))

    def response(self) -> dict[str, object]:
        """Return the API configuration payload with its current revision."""
        with self.lock:
            payload = self.configuration.model_dump(by_alias=True, mode="json")
            payload["revision"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
            return payload

    def update(self, configuration: ShapeServerConfiguration, expected_revision: str | None) -> ShapeServerConfiguration:
        """Atomically persist a configuration after checking its expected revision."""
        with self.lock:
            if expected_revision is not None and expected_revision != self.response()["revision"]:
                raise HTTPException(status_code=409, detail="configuration revision is stale")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Serialize to a sibling file before replacing the active configuration.
            temporary_path = self.path.with_suffix(".tmp")
            temporary_path.write_text(configuration.model_dump_json(by_alias=True, indent=2) + "\n", encoding="utf-8")
            # Replace only a complete temporary file so configuration writes are atomic.
            temporary_path.replace(self.path)
            self.configuration = configuration
            return configuration


@dataclass(frozen=True)
class QueuedEvent:
    event: PointEvent
    received_at: float


class EventProcessor:
    """Bound and rate-limit point-containment work and record matched activity."""

    def __init__(self, shapes: ShapeStore, configuration: ConfigurationStore) -> None:
        self.shapes = shapes
        self.configuration = configuration
        self.queue: asyncio.Queue[QueuedEvent] | None = None
        self.history: deque[dict[str, object]] = deque(maxlen=MATCH_HISTORY_LIMIT)
        self.history_lock = Lock()

    def start(self) -> None:
        """Initialize the queue using the configured capacity."""
        configuration = self.configuration.configuration
        self.queue = asyncio.Queue(maxsize=configuration.queue_capacity)

    def enqueue(self, event: PointEvent) -> int:
        """Admit an event unless the processor is unavailable or at capacity."""
        if self.queue is None:
            raise HTTPException(status_code=503, detail="event processor is not available")
        if self.queue.full():
            raise HTTPException(status_code=429, detail="event queue is full; retry later")
        # Admission is non-blocking so callers receive an immediate overload result.
        self.queue.put_nowait(QueuedEvent(event=event, received_at=time.time()))
        return self.queue.qsize()

    def status(self) -> dict[str, int]:
        """Return the queue depth and retained match-history limit."""
        return {"queued": self.queue.qsize() if self.queue is not None else 0, "historyLimit": MATCH_HISTORY_LIMIT}

    def events(self) -> list[dict[str, object]]:
        """Return matched activities with the most recent first."""
        with self.history_lock:
            return list(reversed(self.history))

    def clear(self) -> None:
        """Discard retained matches and all queued, unprocessed events."""
        with self.history_lock:
            self.history.clear()
        if self.queue is not None:
            while True:
                try:
                    self.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                else:
                    self.queue.task_done()

    async def run(self) -> None:
        """Process queued events continuously at the configured maximum rate."""
        if self.queue is None:
            raise RuntimeError("event processor was not started")
        while True:
            # Wait for work instead of polling an empty queue.
            queued_event = await self.queue.get()
            try:
                await self.process(queued_event)
                await asyncio.sleep(1 / self.configuration.configuration.processing_rate)
            finally:
                self.queue.task_done()

    async def process(self, queued_event: QueuedEvent) -> None:
        """Find containing shapes, deliver each activity, and retain its outcome."""
        point = [queued_event.event.longitude, queued_event.event.latitude]
        # Bounds cheaply reject most shapes before their exact geometry test.
        matches = [
            stored for stored in self.shapes.list()
            if stored.bounds.contains(*point) and contains(stored.shape, point)
        ]
        for stored in matches:
            activity: dict[str, object] = {
                "event": queued_event.event.model_dump(mode="json"),
                "shape": shape_geojson(stored.shape, stored.bounds),
                "receivedAt": queued_event.received_at,
                "matchedAt": time.time(),
                "delivery": "not-configured",
            }
            output_url = self.configuration.configuration.output_url
            if output_url is not None:
                # Output delivery is completed before history records its result.
                activity["delivery"] = await self.deliver(str(output_url), activity)
            with self.history_lock:
                self.history.append(activity)

    async def deliver(self, output_url: str, activity: dict[str, object]) -> str:
        """POST an activity to the configured output service without blocking the loop."""
        def send() -> None:
            request = Request(
                output_url,
                data=json.dumps(activity).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=5) as response:
                if not 200 <= response.status < 300:
                    raise OSError(f"output service returned {response.status}")

        try:
            # urllib is synchronous, so isolate outbound I/O from the event loop.
            await asyncio.to_thread(send)
            return "delivered"
        except (OSError, URLError) as error:
            LOGGER.warning("ShapeServer output delivery failed: %s", error)
            return f"failed: {error}"


def track_events_from_geojson(
    payload: dict[str, object],
    source_url: str,
    timestamp: str,
) -> list[PointEvent]:
    """Convert valid TrackSim position features into ShapeServer point events.

    Non-position features and malformed positions are intentionally ignored so
    auxiliary TrackSim GeoJSON cannot prevent the next polling cycle.
    """
    features = payload.get("features")
    if not isinstance(features, list):
        raise ValueError("TrackSim GeoJSON response does not contain a features array")
    events: list[PointEvent] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties")
        geometry = feature.get("geometry")
        if not isinstance(properties, dict) or properties.get("kind") != "track-position":
            # TrackSim also publishes route and trail features that are not points.
            continue
        if (
            not isinstance(geometry, dict)
            or geometry.get("type") != "Point"
            or not isinstance(geometry.get("coordinates"), list)
        ):
            continue
        coordinates = geometry["coordinates"]
        if len(coordinates) != 2 or not all(isinstance(value, (int, float)) for value in coordinates):
            continue
        feature_id = feature.get("id")
        if not isinstance(feature_id, str) or not feature_id:
            continue
        track_name = properties.get("name")
        events.append(PointEvent(
            eventId=f"tracks:{feature_id}",
            longitude=coordinates[0],
            latitude=coordinates[1],
            timestamp=timestamp,
            properties={
                "sourceId": "tracks",
                "trackId": feature_id,
                "trackName": track_name if isinstance(track_name, str) else feature_id,
                "routeId": properties.get("routeId"),
                "state": properties.get("state"),
            },
        ))
    return events


class TrackSourcePoller:
    """Poll TrackSim GeoJSON and submit its current positions for containment."""

    def __init__(self, processor: EventProcessor, configuration: ConfigurationStore) -> None:
        self.processor = processor
        self.configuration = configuration

    @staticmethod
    def _fetch_geojson(source_url: str) -> dict[str, object]:
        """Fetch and validate the top-level TrackSim GeoJSON response."""
        endpoint = f"{source_url.rstrip('/')}/api/geojson"
        with urlopen(endpoint, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("TrackSim GeoJSON response must be an object")
        return payload

    async def poll_once(self) -> int:
        """Poll TrackSim once and return the number of admitted position events."""
        source_url = self.configuration.configuration.track_source_url
        if source_url is None:
            return 0
        # Fetch in a worker because urllib blocks while waiting for TrackSim.
        payload = await asyncio.to_thread(self._fetch_geojson, str(source_url))
        timestamp = datetime.now(timezone.utc).isoformat()
        queued = 0
        # Each current track position becomes an independently bounded event.
        for event in track_events_from_geojson(payload, str(source_url), timestamp):
            try:
                self.processor.enqueue(event)
                queued += 1
            except HTTPException as error:
                if error.status_code != 429:
                    raise
                LOGGER.warning("ShapeServer TrackSim poll dropped remaining tracks because the event queue is full")
                break
        return queued

    async def run(self) -> None:
        """Poll until cancelled, logging recoverable TrackSim failures."""
        while True:
            configuration = self.configuration.configuration
            try:
                if configuration.track_source_url is not None:
                    await self.poll_once()
            except (OSError, URLError, ValueError, json.JSONDecodeError) as error:
                # A transient TrackSim failure must not stop later polling cycles.
                LOGGER.warning("ShapeServer TrackSim poll failed: %s", error)
            await asyncio.sleep(configuration.track_poll_interval_milliseconds / 1_000)


shapes = ShapeStore()
configuration_store = ConfigurationStore(CONFIG_FILE)
events = EventProcessor(shapes, configuration_store)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Start and stop the containment worker and TrackSim polling tasks."""
    events.start()
    # Keep ingestion and rate-limited containment independent.
    worker = asyncio.create_task(events.run())
    track_poller = asyncio.create_task(TrackSourcePoller(events, configuration_store).run())
    try:
        yield
    finally:
        # Await cancellation so Uvicorn cannot leave background tasks pending.
        worker.cancel()
        track_poller.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
        with contextlib.suppress(asyncio.CancelledError):
            await track_poller


app = FastAPI(title="Kody ShapeServer", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_methods=["GET", "PUT", "POST", "DELETE"],
    allow_headers=["Content-Type", "If-Match"],
)


def stored_response(stored: StoredShape) -> dict[str, object]:
    """Return a shape definition together with its derived bounds and GeoJSON."""
    return {
        "shape": stored.shape.model_dump(mode="json"),
        "bounds": stored.bounds.model_dump(),
        "geojson": shape_geojson(stored.shape, stored.bounds),
    }


@app.get("/api/kody/manifest")
def get_manifest() -> dict[str, object]:
    """Advertise ShapeServer's map-layer and configuration capabilities."""
    return {
        "kodyApiVersion": "1",
        "serviceId": "shape-server",
        "displayName": "ShapeServer",
        "dataEndpoint": {
            "path": "/api/shapes/geojson",
            "format": "geojson",
            "refreshIntervalMilliseconds": 200,
        },
        "capabilities": {"dataLayer": {"kind": "geojson", "dataPath": "/api/shapes/geojson"}},
        "configuration": {
            "readPath": "/api/config",
            "writePath": "/api/config",
            "fields": [
                {"key": "outputUrl", "label": "Output callback URL", "type": "url", "placeholder": "https://example.com/shape-events"},
                {"key": "processingRate", "label": "Point checks per second", "type": "number", "min": 1, "max": 10_000, "step": 1},
                {"key": "queueCapacity", "label": "Maximum queued point checks", "type": "number", "min": 1, "max": 100_000, "step": 1},
                {"key": "trackSourceUrl", "label": "TrackSim service URL", "type": "url", "placeholder": "http://127.0.0.1:8003"},
                {"key": "trackPollIntervalMilliseconds", "label": "TrackSim poll interval (ms)", "type": "number", "min": 100, "max": 60_000, "step": 100},
            ],
            "actions": [{
                "label": "Clear local data",
                "path": "/api/actions/clear-local-data",
                "method": "POST",
                "confirmationMessage": "Clear all in-memory ShapeServer shapes and events? This cannot be undone.",
            }],
        },
    }


@app.get("/api/config")
def get_configuration() -> dict[str, object]:
    """Return the current persisted configuration and its revision."""
    return configuration_store.response()


@app.put("/api/config")
def update_configuration(configuration: ShapeServerConfiguration, if_match: str | None = Header(default=None)) -> dict[str, object]:
    """Persist configuration, replacing an empty queue when its capacity changes."""
    previous_capacity = configuration_store.configuration.queue_capacity
    # Replacing a non-empty queue would discard accepted point events.
    if events.queue is not None and configuration.queue_capacity != previous_capacity and not events.queue.empty():
        raise HTTPException(status_code=409, detail="wait for the event queue to empty before changing its capacity")
    configuration_store.update(configuration, if_match)
    if events.queue is not None and configuration.queue_capacity != previous_capacity:
        # Capacity changes cannot preserve asyncio.Queue's fixed maximum size.
        events.queue = asyncio.Queue(maxsize=configuration.queue_capacity)
    return configuration_store.response()


@app.get("/api/shapes")
def get_shapes() -> dict[str, object]:
    """Return all shape definitions, derived bounds, and their shared revision."""
    stored_shapes = shapes.list()
    return {"revision": shapes.revision(), "shapes": [stored_response(stored) for stored in stored_shapes]}


@app.get("/api/shapes/geojson")
def get_shapes_geojson() -> dict[str, object]:
    """Return all current shapes as a revisioned GeoJSON feature collection."""
    stored_shapes = shapes.list()
    return {
        "type": "FeatureCollection",
        "revision": shapes.revision(),
        "features": [shape_geojson(stored.shape, stored.bounds) for stored in stored_shapes],
    }


@app.post("/api/shapes", status_code=status.HTTP_201_CREATED)
def create_shape(shape: Shape = Body(...)) -> dict[str, object]:
    """Create one validated shape and return its assigned representation."""
    return stored_response(shapes.create(shape))


@app.put("/api/shapes/{shape_id}")
def update_shape(shape_id: str, shape: Shape = Body(...)) -> dict[str, object]:
    """Replace a shape identified by the request path."""
    return stored_response(shapes.update(shape_id, shape))


@app.delete("/api/shapes/{shape_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shape(shape_id: str) -> None:
    """Delete the shape identified by the request path."""
    shapes.delete(shape_id)


@app.post("/api/actions/clear-local-data")
def clear_local_data() -> dict[str, str]:
    """Clear all non-persistent shapes, queued events, and match history."""
    with shapes.lock:
        # Shape state is deliberately in-memory and can be reset by this action.
        shapes.shapes.clear()
        shapes.revision_number += 1
    events.clear()
    return {"message": "Cleared in-memory ShapeServer shapes and events."}


@app.post("/api/events", status_code=status.HTTP_202_ACCEPTED)
def submit_event(event: PointEvent, request: FastAPIRequest) -> dict[str, object]:
    """Queue a point event for asynchronous bounds and containment processing."""
    queued = events.enqueue(event)
    return {
        "message": "point event queued",
        "eventId": event.eventId,
        "queued": queued,
        "statusPath": str(request.url_for("get_events")),
    }


@app.get("/api/events", name="get_events")
def get_events() -> dict[str, object]:
    """Return processor queue status and the retained containment activities."""
    return {**events.status(), "matches": events.events()}
