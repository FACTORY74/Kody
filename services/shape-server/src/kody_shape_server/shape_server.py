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

"""In-memory shapes, point containment events, and output delivery for Kody."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import math
import os
import re
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Annotated, Any, Literal
from urllib.error import URLError
from urllib.request import Request, urlopen

from fastapi import Body, FastAPI, Header, HTTPException, Request as FastAPIRequest, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

ROOT_DIRECTORY = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT_DIRECTORY / "config" / "shape-server.json"
EARTH_RADIUS_METRES = 6_371_008.8
ARC_SEGMENTS = 64
MATCH_HISTORY_LIMIT = 1_000
COLOUR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$")
COLOUR_PALETTE = ("#35b8ff", "#ff8d4a", "#9d7cff", "#4ed8a7", "#ffcf4a", "#fb6f92")
LOGGER = logging.getLogger(__name__)


def validate_coordinate(value: list[float]) -> list[float]:
    if (
        len(value) != 2
        or any(not isinstance(item, (int, float)) or isinstance(item, bool) for item in value)
        or not all(math.isfinite(item) for item in value)
        or not -180 <= value[0] <= 180
        or not -90 <= value[1] <= 90
    ):
        raise ValueError("coordinates must be [longitude, latitude] within valid bounds")
    return value


Coordinate = Annotated[list[float], Field(min_length=2, max_length=2)]


class ShapeBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    color: str | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value.strip()

    @field_validator("color")
    @classmethod
    def validate_color(cls, value: str | None) -> str | None:
        if value is not None and not COLOUR_PATTERN.fullmatch(value):
            raise ValueError("color must be a hexadecimal #RRGGBB or #RRGGBBAA value")
        return value


class CircleShape(ShapeBase):
    type: Literal["circle"]
    center: Coordinate
    radius: float = Field(gt=0, le=20_000_000)

    _validate_center = field_validator("center")(validate_coordinate)


class RectangleShape(ShapeBase):
    type: Literal["rectangle"]
    origin: Coordinate
    width: float = Field(gt=0, le=40_000_000)
    height: float = Field(gt=0, le=40_000_000)
    angle: float = Field(ge=-360, le=360)

    _validate_origin = field_validator("origin")(validate_coordinate)


class AnnularSectorShape(ShapeBase):
    type: Literal["annularSector"]
    center: Coordinate
    innerRadius: float = Field(ge=0, le=20_000_000)
    outerRadius: float = Field(gt=0, le=20_000_000)
    startAngle: float = Field(ge=-360, le=360)
    endAngle: float = Field(ge=-360, le=360)

    _validate_center = field_validator("center")(validate_coordinate)

    @field_validator("outerRadius")
    @classmethod
    def validate_outer_radius(cls, value: float, info: Any) -> float:
        if value <= info.data.get("innerRadius", -1):
            raise ValueError("outerRadius must be greater than innerRadius")
        return value


class PolygonShape(ShapeBase):
    type: Literal["polygon"]
    coordinates: list[Coordinate] = Field(min_length=3, max_length=10_000)

    @field_validator("coordinates")
    @classmethod
    def validate_polygon(cls, value: list[list[float]]) -> list[list[float]]:
        if len({(longitude, latitude) for longitude, latitude in value}) < 3:
            raise ValueError("polygon coordinates must contain at least three distinct positions")
        return value


Shape = Annotated[
    CircleShape | RectangleShape | AnnularSectorShape | PolygonShape,
    Field(discriminator="type"),
]


class Bounds(BaseModel):
    west: float
    south: float
    east: float
    north: float

    def contains(self, longitude: float, latitude: float) -> bool:
        return self.west <= longitude <= self.east and self.south <= latitude <= self.north


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


def destination(center: list[float], bearing: float, distance: float) -> list[float]:
    longitude, latitude = map(math.radians, center)
    angular_distance = distance / EARTH_RADIUS_METRES
    angle = math.radians(bearing)
    destination_latitude = math.asin(
        math.sin(latitude) * math.cos(angular_distance)
        + math.cos(latitude) * math.sin(angular_distance) * math.cos(angle)
    )
    destination_longitude = longitude + math.atan2(
        math.sin(angle) * math.sin(angular_distance) * math.cos(latitude),
        math.cos(angular_distance) - math.sin(latitude) * math.sin(destination_latitude),
    )
    return [((math.degrees(destination_longitude) + 540) % 360) - 180, math.degrees(destination_latitude)]


def haversine_metres(first: list[float], second: list[float]) -> float:
    longitude_delta = math.radians(second[0] - first[0])
    latitude_delta = math.radians(second[1] - first[1])
    latitude_first = math.radians(first[1])
    latitude_second = math.radians(second[1])
    value = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(latitude_first) * math.cos(latitude_second) * math.sin(longitude_delta / 2) ** 2
    )
    return EARTH_RADIUS_METRES * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def bearing_degrees(first: list[float], second: list[float]) -> float:
    longitude_delta = math.radians(second[0] - first[0])
    latitude_first = math.radians(first[1])
    latitude_second = math.radians(second[1])
    return math.degrees(math.atan2(
        math.sin(longitude_delta) * math.cos(latitude_second),
        math.cos(latitude_first) * math.sin(latitude_second)
        - math.sin(latitude_first) * math.cos(latitude_second) * math.cos(longitude_delta),
    )) % 360


def shape_ring(shape: Shape) -> list[list[float]]:
    if isinstance(shape, CircleShape):
        return [destination(shape.center, angle, shape.radius) for angle in range(0, 361, 360 // ARC_SEGMENTS)]
    if isinstance(shape, RectangleShape):
        corners: list[list[float]] = []
        angle = math.radians(shape.angle)
        for east, north in (
            (-shape.width / 2, -shape.height / 2),
            (shape.width / 2, -shape.height / 2),
            (shape.width / 2, shape.height / 2),
            (-shape.width / 2, shape.height / 2),
        ):
            rotated_east = east * math.cos(angle) + north * math.sin(angle)
            rotated_north = -east * math.sin(angle) + north * math.cos(angle)
            corners.append(destination(shape.origin, math.degrees(math.atan2(rotated_east, rotated_north)), math.hypot(rotated_east, rotated_north)))
        return corners + [corners[0]]
    if isinstance(shape, AnnularSectorShape):
        sweep = (shape.endAngle - shape.startAngle) % 360 or 360
        angles = [shape.startAngle + sweep * index / ARC_SEGMENTS for index in range(ARC_SEGMENTS + 1)]
        outer = [destination(shape.center, angle, shape.outerRadius) for angle in angles]
        inner = [destination(shape.center, angle, shape.innerRadius) for angle in reversed(angles)]
        return outer + inner + [outer[0]]
    return shape.coordinates + [shape.coordinates[0]]


def calculate_bounds(shape: Shape) -> Bounds:
    ring = shape_ring(shape)
    longitudes, latitudes = zip(*ring)
    return Bounds(west=min(longitudes), south=min(latitudes), east=max(longitudes), north=max(latitudes))


def point_in_polygon(point: list[float], ring: list[list[float]]) -> bool:
    longitude, latitude = point
    inside = False
    for current, following in zip(ring, ring[1:]):
        current_longitude, current_latitude = current
        following_longitude, following_latitude = following
        if ((current_latitude > latitude) != (following_latitude > latitude)) and (
            longitude < (following_longitude - current_longitude) * (latitude - current_latitude)
            / (following_latitude - current_latitude) + current_longitude
        ):
            inside = not inside
    return inside


def contains(shape: Shape, point: list[float]) -> bool:
    if isinstance(shape, CircleShape):
        return haversine_metres(shape.center, point) <= shape.radius
    if isinstance(shape, AnnularSectorShape):
        distance = haversine_metres(shape.center, point)
        if not shape.innerRadius <= distance <= shape.outerRadius:
            return False
        sweep = (shape.endAngle - shape.startAngle) % 360 or 360
        return (bearing_degrees(shape.center, point) - shape.startAngle) % 360 <= sweep
    return point_in_polygon(point, shape_ring(shape))


def shape_geojson(shape: Shape, bounds: Bounds) -> dict[str, object]:
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
        return hashlib.sha256(str(self.revision_number).encode("ascii")).hexdigest()

    def list(self) -> list[StoredShape]:
        with self.lock:
            return list(self.shapes.values())

    def create(self, shape: Shape) -> StoredShape:
        with self.lock:
            shape_id = shape.id or str(uuid.uuid4())
            if shape_id in self.shapes:
                raise HTTPException(status_code=409, detail=f"shape id already exists: {shape_id}")
            assigned = shape.model_copy(update={"id": shape_id, "color": shape.color or COLOUR_PALETTE[self.revision_number % len(COLOUR_PALETTE)]})
            stored = StoredShape(shape=assigned, bounds=calculate_bounds(assigned))
            self.shapes[shape_id] = stored
            self.revision_number += 1
            return stored

    def update(self, shape_id: str, shape: Shape) -> StoredShape:
        with self.lock:
            existing = self.shapes.get(shape_id)
            if existing is None:
                raise HTTPException(status_code=404, detail=f"shape not found: {shape_id}")
            if shape.id is not None and shape.id != shape_id:
                raise HTTPException(status_code=400, detail="shape id in the body must match the path")
            assigned = shape.model_copy(update={"id": shape_id, "color": shape.color or existing.shape.color})
            stored = StoredShape(shape=assigned, bounds=calculate_bounds(assigned))
            self.shapes[shape_id] = stored
            self.revision_number += 1
            return stored

    def delete(self, shape_id: str) -> None:
        with self.lock:
            if shape_id not in self.shapes:
                raise HTTPException(status_code=404, detail=f"shape not found: {shape_id}")
            del self.shapes[shape_id]
            self.revision_number += 1


class ConfigurationStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = Lock()
        self.configuration = self._load()

    def _load(self) -> ShapeServerConfiguration:
        if not self.path.is_file():
            return ShapeServerConfiguration()
        return ShapeServerConfiguration.model_validate_json(self.path.read_text(encoding="utf-8"))

    def response(self) -> dict[str, object]:
        with self.lock:
            payload = self.configuration.model_dump(by_alias=True, mode="json")
            payload["revision"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
            return payload

    def update(self, configuration: ShapeServerConfiguration, expected_revision: str | None) -> ShapeServerConfiguration:
        with self.lock:
            if expected_revision is not None and expected_revision != self.response()["revision"]:
                raise HTTPException(status_code=409, detail="configuration revision is stale")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.path.with_suffix(".tmp")
            temporary_path.write_text(configuration.model_dump_json(by_alias=True, indent=2) + "\n", encoding="utf-8")
            temporary_path.replace(self.path)
            self.configuration = configuration
            return configuration


@dataclass(frozen=True)
class QueuedEvent:
    event: PointEvent
    received_at: float


class EventProcessor:
    def __init__(self, shapes: ShapeStore, configuration: ConfigurationStore) -> None:
        self.shapes = shapes
        self.configuration = configuration
        self.queue: asyncio.Queue[QueuedEvent] | None = None
        self.history: deque[dict[str, object]] = deque(maxlen=MATCH_HISTORY_LIMIT)
        self.history_lock = Lock()

    def start(self) -> None:
        configuration = self.configuration.configuration
        self.queue = asyncio.Queue(maxsize=configuration.queue_capacity)

    def enqueue(self, event: PointEvent) -> int:
        if self.queue is None:
            raise HTTPException(status_code=503, detail="event processor is not available")
        if self.queue.full():
            raise HTTPException(status_code=429, detail="event queue is full; retry later")
        self.queue.put_nowait(QueuedEvent(event=event, received_at=time.time()))
        return self.queue.qsize()

    def status(self) -> dict[str, int]:
        return {"queued": self.queue.qsize() if self.queue is not None else 0, "historyLimit": MATCH_HISTORY_LIMIT}

    def events(self) -> list[dict[str, object]]:
        with self.history_lock:
            return list(reversed(self.history))

    def clear(self) -> None:
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
        if self.queue is None:
            raise RuntimeError("event processor was not started")
        while True:
            queued_event = await self.queue.get()
            try:
                await self.process(queued_event)
                await asyncio.sleep(1 / self.configuration.configuration.processing_rate)
            finally:
                self.queue.task_done()

    async def process(self, queued_event: QueuedEvent) -> None:
        point = [queued_event.event.longitude, queued_event.event.latitude]
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
                activity["delivery"] = await self.deliver(str(output_url), activity)
            with self.history_lock:
                self.history.append(activity)

    async def deliver(self, output_url: str, activity: dict[str, object]) -> str:
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
    def __init__(self, processor: EventProcessor, configuration: ConfigurationStore) -> None:
        self.processor = processor
        self.configuration = configuration

    @staticmethod
    def _fetch_geojson(source_url: str) -> dict[str, object]:
        endpoint = f"{source_url.rstrip('/')}/api/geojson"
        with urlopen(endpoint, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("TrackSim GeoJSON response must be an object")
        return payload

    async def poll_once(self) -> int:
        source_url = self.configuration.configuration.track_source_url
        if source_url is None:
            return 0
        payload = await asyncio.to_thread(self._fetch_geojson, str(source_url))
        timestamp = datetime.now(timezone.utc).isoformat()
        queued = 0
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
        while True:
            configuration = self.configuration.configuration
            try:
                if configuration.track_source_url is not None:
                    await self.poll_once()
            except (OSError, URLError, ValueError, json.JSONDecodeError) as error:
                LOGGER.warning("ShapeServer TrackSim poll failed: %s", error)
            await asyncio.sleep(configuration.track_poll_interval_milliseconds / 1_000)


shapes = ShapeStore()
configuration_store = ConfigurationStore(CONFIG_FILE)
events = EventProcessor(shapes, configuration_store)


@asynccontextmanager
async def lifespan(_: FastAPI):
    events.start()
    worker = asyncio.create_task(events.run())
    track_poller = asyncio.create_task(TrackSourcePoller(events, configuration_store).run())
    try:
        yield
    finally:
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
    return {
        "shape": stored.shape.model_dump(mode="json"),
        "bounds": stored.bounds.model_dump(),
        "geojson": shape_geojson(stored.shape, stored.bounds),
    }


@app.get("/api/kody/manifest")
def get_manifest() -> dict[str, object]:
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
    return configuration_store.response()


@app.put("/api/config")
def update_configuration(configuration: ShapeServerConfiguration, if_match: str | None = Header(default=None)) -> dict[str, object]:
    previous_capacity = configuration_store.configuration.queue_capacity
    if events.queue is not None and configuration.queue_capacity != previous_capacity and not events.queue.empty():
        raise HTTPException(status_code=409, detail="wait for the event queue to empty before changing its capacity")
    configuration_store.update(configuration, if_match)
    if events.queue is not None and configuration.queue_capacity != previous_capacity:
        events.queue = asyncio.Queue(maxsize=configuration.queue_capacity)
    return configuration_store.response()


@app.get("/api/shapes")
def get_shapes() -> dict[str, object]:
    stored_shapes = shapes.list()
    return {"revision": shapes.revision(), "shapes": [stored_response(stored) for stored in stored_shapes]}


@app.get("/api/shapes/geojson")
def get_shapes_geojson() -> dict[str, object]:
    stored_shapes = shapes.list()
    return {
        "type": "FeatureCollection",
        "revision": shapes.revision(),
        "features": [shape_geojson(stored.shape, stored.bounds) for stored in stored_shapes],
    }


@app.post("/api/shapes", status_code=status.HTTP_201_CREATED)
def create_shape(shape: Shape = Body(...)) -> dict[str, object]:
    return stored_response(shapes.create(shape))


@app.put("/api/shapes/{shape_id}")
def update_shape(shape_id: str, shape: Shape = Body(...)) -> dict[str, object]:
    return stored_response(shapes.update(shape_id, shape))


@app.delete("/api/shapes/{shape_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shape(shape_id: str) -> None:
    shapes.delete(shape_id)


@app.post("/api/actions/clear-local-data")
def clear_local_data() -> dict[str, str]:
    with shapes.lock:
        shapes.shapes.clear()
        shapes.revision_number += 1
    events.clear()
    return {"message": "Cleared in-memory ShapeServer shapes and events."}


@app.post("/api/events", status_code=status.HTTP_202_ACCEPTED)
def submit_event(event: PointEvent, request: FastAPIRequest) -> dict[str, object]:
    queued = events.enqueue(event)
    return {
        "message": "point event queued",
        "eventId": event.eventId,
        "queued": queued,
        "statusPath": str(request.url_for("get_events")),
    }


@app.get("/api/events", name="get_events")
def get_events() -> dict[str, object]:
    return {**events.status(), "matches": events.events()}
