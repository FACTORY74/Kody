"""OpenSeaFeed-backed AIS vessel service with bounded local GeoJSON output."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import asin, atan2, cos, degrees, radians, sin
from pathlib import Path
from threading import Event, Lock, Thread
from time import time
from typing import Any, Callable
import hashlib
import json
import logging

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

LOGGER = logging.getLogger(__name__)
ROOT_DIRECTORY = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT_DIRECTORY / "config" / "ais.json"
STREAM_URL = "wss://stream.openseafeed.com/v1/stream"
EARTH_RADIUS_METRES = 6_371_008.8


def ais_symbol(ship_type: int | None) -> str:
    if ship_type == 30:
        return "fishing_boat"
    if ship_type in {31, 32, 52}:
        return "tug"
    if ship_type in {33, 34, 53, 54, 56, 57, 59, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99}:
        return "service_boat"
    if ship_type == 35:
        return "military_ship"
    if ship_type == 36:
        return "sailboat"
    if ship_type == 37:
        return "yacht"
    if ship_type is not None and 40 <= ship_type <= 49:
        return "high_speed_craft"
    if ship_type == 50:
        return "pilot_boat"
    if ship_type in {51, 58}:
        return "rescue_boat"
    if ship_type == 55:
        return "law_enforcement_boat"
    if ship_type is not None and 60 <= ship_type <= 69:
        return "passenger_ship"
    if ship_type is not None and 70 <= ship_type <= 79:
        return "cargo_ship"
    if ship_type is not None and 80 <= ship_type <= 89:
        return "tanker"
    return "ship"


def message_ship_type(message: dict[str, Any]) -> int | None:
    metadata = message.get("MetaData", {})
    payload = message.get("Message", {})
    for source in (metadata, payload.get("ShipStaticData", {}), payload.get("VoyageData", {})):
        for key in ("ShipType", "Type", "shipType"):
            value = source.get(key)
            if isinstance(value, int) and 0 <= value <= 99:
                return value
            if isinstance(value, str) and value.isdigit() and 0 <= int(value) <= 99:
                return int(value)
    return None


def message_mmsi(message: dict[str, Any]) -> str:
    metadata = message.get("MetaData", {})
    payload = message.get("Message", {})
    for source in (metadata, payload.get("PositionReport", {}), payload.get("ShipStaticData", {}), payload.get("VoyageData", {})):
        value = source.get("MMSI") or source.get("UserID")
        if value:
            return str(value)
    return ""


class Position(BaseModel):
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-85, le=85)


class AisConfiguration(BaseModel):
    centre: Position = Field(default_factory=lambda: Position(longitude=-1.3, latitude=50.65))
    radius_kilometres: int = Field(default=100, alias="radiusKilometres", ge=1, le=250)
    reconnect_seconds: int = Field(default=10, alias="reconnectSeconds", ge=2, le=120)
    extrapolate: bool = False
    local_refresh_seconds: int = Field(default=1, alias="localRefreshSeconds", ge=1, le=10)
    track_length_seconds: int = Field(default=300, alias="trackLengthSeconds", ge=10, le=3600)
    track_fade: bool = Field(default=False, alias="trackFade")
    draw_boundary: bool = Field(default=False, alias="drawBoundary")
    display_color: str = Field(default="#00bcd4", alias="displayColor", pattern=r"^#[0-9a-fA-F]{6}$")


class ConfigurationStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = Lock()
        self.configuration = self._load()

    def _load(self) -> AisConfiguration:
        if not self.path.is_file():
            return AisConfiguration()
        return AisConfiguration.model_validate_json(self.path.read_text(encoding="utf-8"))

    def revision(self) -> str:
        return hashlib.sha256(self.configuration.model_dump_json(by_alias=True).encode("utf-8")).hexdigest()

    def response(self) -> dict[str, object]:
        with self.lock:
            return {"revision": self.revision(), **self.configuration.model_dump(by_alias=True)}

    def update(self, configuration: AisConfiguration, expected_revision: str | None) -> AisConfiguration:
        with self.lock:
            if expected_revision is not None and expected_revision != self.revision():
                raise HTTPException(status_code=409, detail="configuration revision is stale")
            temporary = self.path.with_suffix(".tmp")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(configuration.model_dump_json(by_alias=True, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.path)
            self.configuration = configuration
            return configuration


@dataclass
class Vessel:
    mmsi: str
    name: str
    longitude: float
    latitude: float
    course_degrees: float
    speed_knots: float
    observed_at: float


def destination_point(longitude: float, latitude: float, bearing_degrees: float, distance_metres: float) -> list[float]:
    angular_distance = distance_metres / EARTH_RADIUS_METRES
    bearing = radians(bearing_degrees)
    source_latitude = radians(latitude)
    source_longitude = radians(longitude)
    target_latitude = asin(
        sin(source_latitude) * cos(angular_distance)
        + cos(source_latitude) * sin(angular_distance) * cos(bearing)
    )
    target_longitude = source_longitude + atan2(
        sin(bearing) * sin(angular_distance) * cos(source_latitude),
        cos(angular_distance) - sin(source_latitude) * sin(target_latitude),
    )
    return [degrees(target_longitude), degrees(target_latitude)]


def subscription_bounds(configuration: AisConfiguration) -> list[list[float]]:
    latitude_span = configuration.radius_kilometres / 111.32
    longitude_span = latitude_span / max(cos(radians(configuration.centre.latitude)), 0.1)
    return [
        [configuration.centre.latitude - latitude_span, configuration.centre.longitude - longitude_span],
        [configuration.centre.latitude + latitude_span, configuration.centre.longitude + longitude_span],
    ]


def is_within_subscription_bounds(longitude: float, latitude: float, configuration: AisConfiguration) -> bool:
    southwest, northeast = subscription_bounds(configuration)
    return southwest[0] <= latitude <= northeast[0] and southwest[1] <= longitude <= northeast[1]


def subscription_boundary(configuration: AisConfiguration) -> list[list[float]]:
    southwest, northeast = subscription_bounds(configuration)
    south, west = southwest
    north, east = northeast
    return [[west, south], [east, south], [east, north], [west, north], [west, south]]


class AisStore:
    def __init__(self, clock: Callable[[], float] = time, configuration: AisConfiguration | None = None) -> None:
        self.clock = clock
        self.configuration = configuration or AisConfiguration()
        self.lock = Lock()
        self.vessels: dict[str, Vessel] = {}
        self.ship_types: dict[str, int] = {}
        self.trails: dict[str, deque[tuple[float, list[float]]]] = {}
        self.last_error: str | None = None
        self.generation = 0

    def apply_configuration(self, configuration: AisConfiguration) -> None:
        with self.lock:
            location_changed = configuration.centre != self.configuration.centre or configuration.radius_kilometres != self.configuration.radius_kilometres
            self.configuration = configuration
            self.generation += 1
            if location_changed:
                self.vessels.clear()
                self.ship_types.clear()
                self.trails.clear()

    def clear(self) -> None:
        with self.lock:
            self.vessels.clear()
            self.ship_types.clear()
            self.trails.clear()
            self.last_error = None
            self.generation += 1

    def ingest(self, message: dict[str, Any], observed_at: float | None = None) -> None:
        report = message.get("Message", {}).get("PositionReport", {})
        metadata = message.get("MetaData", {})
        latitude, longitude = report.get("Latitude"), report.get("Longitude")
        mmsi = message_mmsi(message)
        ship_type = message_ship_type(message)
        if mmsi and ship_type is not None:
            with self.lock:
                self.ship_types[mmsi] = ship_type
        if not mmsi or not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
            return
        observed_at = self.clock() if observed_at is None else observed_at
        vessel = Vessel(
            mmsi=mmsi,
            name=str(metadata.get("ShipName") or mmsi).strip() or mmsi,
            longitude=float(longitude),
            latitude=float(latitude),
            course_degrees=float(report.get("Cog") or report.get("TrueHeading") or 0),
            speed_knots=float(report.get("Sog") or 0),
            observed_at=observed_at,
        )
        with self.lock:
            self.vessels[mmsi] = vessel
            trail = self.trails.setdefault(mmsi, deque())
            point = [vessel.longitude, vessel.latitude]
            if not trail or trail[-1][1] != point:
                trail.append((observed_at, point))
            cutoff = observed_at - self.configuration.track_length_seconds
            while trail and trail[0][0] < cutoff:
                trail.popleft()

    def response(self) -> dict[str, object]:
        now = self.clock()
        with self.lock:
            configuration = self.configuration
            vessels = list(self.vessels.values())
            ship_types = dict(self.ship_types)
            trails = {mmsi: list(points) for mmsi, points in self.trails.items()}
            last_error = self.last_error
        features: list[dict[str, object]] = []
        for vessel in vessels:
            age = max(0, now - vessel.observed_at)
            point = [vessel.longitude, vessel.latitude]
            extrapolated = configuration.extrapolate and vessel.speed_knots > 0 and age <= configuration.reconnect_seconds
            if extrapolated:
                point = destination_point(vessel.longitude, vessel.latitude, vessel.course_degrees, vessel.speed_knots * 0.514444 * age)
            if not is_within_subscription_bounds(point[0], point[1], configuration):
                continue
            properties = {
                "name": vessel.name,
                "color": configuration.display_color,
                "kind": "track-position",
                "symbol": ais_symbol(ship_types.get(vessel.mmsi)),
                "bearingDegrees": vessel.course_degrees,
                "mmsi": vessel.mmsi,
                "aisShipType": ship_types.get(vessel.mmsi),
                "speedKnots": vessel.speed_knots,
                "courseDegrees": vessel.course_degrees,
                "positionAgeSeconds": round(age, 1),
                "isExtrapolated": extrapolated,
            }
            trail = [
                trail_point
                for _, trail_point in trails.get(vessel.mmsi, [])
                if is_within_subscription_bounds(trail_point[0], trail_point[1], configuration)
            ]
            if len(trail) >= 2:
                features.append({
                    "type": "Feature",
                    "properties": {**properties, "name": f"{vessel.name} trail", "kind": "track-trail", "fadeTrail": configuration.track_fade, "durationSeconds": configuration.track_length_seconds},
                    "geometry": {"type": "LineString", "coordinates": [point for _, point in trail]},
                })
            features.append({"type": "Feature", "properties": properties, "geometry": {"type": "Point", "coordinates": point}})
        if configuration.draw_boundary:
            features.append({
                "type": "Feature",
                "properties": {"name": "AIS coverage boundary", "color": configuration.display_color, "kind": "ais-boundary", "lineStyle": "dotted", "label": False},
                "geometry": {"type": "LineString", "coordinates": subscription_boundary(configuration)},
            })
        collection = {"type": "FeatureCollection", "features": features}
        revision = hashlib.sha256(json.dumps(collection, separators=(",", ":"), sort_keys=True).encode("utf-8")).hexdigest()
        return {"revision": revision, **collection, "source": {"provider": "OpenSeaFeed", "lastError": last_error}}


class OpenSeaFeedClient:
    def __init__(self, store: AisStore) -> None:
        self.store = store
        self.stop_event = Event()
        self.thread: Thread | None = None

    def start(self) -> None:
        if self.thread is None:
            self.thread = Thread(target=self._run, name="kody-ais-feed", daemon=True)
            self.thread.start()

    def _run(self) -> None:
        try:
            import websocket
        except ImportError:
            LOGGER.error("AIS feed is unavailable: install websocket-client")
            return
        generation = -1
        while not self.stop_event.is_set():
            try:
                with self.store.lock:
                    configuration = self.store.configuration
                    generation = self.store.generation
                connection = websocket.create_connection(STREAM_URL, timeout=5)
                connection.send(json.dumps({"BoundingBoxes": [subscription_bounds(configuration)]}))
                while not self.stop_event.is_set() and generation == self.store.generation:
                    payload = connection.recv()
                    if isinstance(payload, bytes):
                        payload = payload.decode("utf-8")
                    self.store.ingest(json.loads(payload))
                connection.close()
            except Exception as error:
                with self.store.lock:
                    self.store.last_error = str(error)
                    delay = self.store.configuration.reconnect_seconds
                LOGGER.warning("OpenSeaFeed connection failed; retrying in %s seconds: %s", delay, error)
                self.stop_event.wait(delay)


configuration_store = ConfigurationStore(CONFIG_FILE)
ais_store = AisStore(configuration=configuration_store.configuration)
ais_client = OpenSeaFeedClient(ais_store)
app = FastAPI(title="Kody AIS Vessel Service", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"], allow_methods=["GET", "PUT", "POST"], allow_headers=["Content-Type", "If-Match"])


@app.on_event("startup")
def start_client() -> None:
    ais_client.start()


@app.get("/api/kody/manifest")
def get_manifest() -> dict[str, object]:
    return {
        "kodyApiVersion": "1", "serviceId": "ais", "displayName": "AIS vessels",
        "dataEndpoint": {"path": "/api/geojson", "format": "geojson"},
        "configuration": {"readPath": "/api/config", "writePath": "/api/config", "fields": [
            {"key": "centre", "label": "Search centre", "type": "position"},
            {"key": "radiusKilometres", "label": "Subscription half-size (km)", "type": "number", "minimum": 1, "maximum": 250, "step": 1},
            {"key": "reconnectSeconds", "label": "Reconnect delay (seconds)", "type": "number", "minimum": 2, "maximum": 120, "step": 1},
            {"key": "extrapolate", "label": "Extrapolate vessels between observations", "type": "boolean"},
            {"key": "localRefreshSeconds", "label": "Local share refresh (seconds)", "type": "number", "minimum": 1, "maximum": 10, "step": 1},
            {"key": "trackLengthSeconds", "label": "Track length (seconds)", "type": "number", "minimum": 10, "maximum": 3600, "step": 1},
            {"key": "trackFade", "label": "Track fade", "type": "boolean"},
            {"key": "drawBoundary", "label": "Draw dotted AIS coverage boundary", "type": "boolean"},
            {"key": "displayColor", "label": "Display color", "type": "color"},
        ], "actions": [{
            "label": "Clear local data",
            "path": "/api/actions/clear-local-data",
            "method": "POST",
            "confirmationMessage": "Clear all local AIS vessels data? This cannot be undone.",
        }]},
        "contextMenuActions": [
            {
                "id": "set-origin",
                "label": "Set Origin Here",
                "path": "/api/actions/set-origin",
                "method": "POST",
            }
        ],
    }


@app.get("/api/config")
def get_configuration() -> dict[str, object]:
    return configuration_store.response()


@app.put("/api/config")
def update_configuration(configuration: AisConfiguration, if_match: str | None = Header(default=None)) -> dict[str, object]:
    updated = configuration_store.update(configuration, if_match)
    ais_store.apply_configuration(updated)
    return configuration_store.response()


@app.post("/api/actions/set-origin")
def set_origin(position: Position) -> dict[str, object]:
    configuration = configuration_store.configuration.model_copy(update={"centre": position})
    updated = configuration_store.update(configuration, None)
    ais_store.apply_configuration(updated)
    return configuration_store.response()


@app.post("/api/actions/clear-local-data")
def clear_local_data() -> dict[str, str]:
    ais_store.clear()
    return {"message": "Cleared local AIS vessels data."}


@app.get("/api/geojson")
def get_vessels() -> dict[str, object]:
    return ais_store.response()
