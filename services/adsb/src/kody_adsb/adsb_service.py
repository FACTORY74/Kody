"""ADSB.lol-backed, bounded live-flight feed for the Kody map."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
import hashlib
import json
import logging
import os
from math import asin, atan2, cos, degrees, pi, radians, sin
from pathlib import Path
import time
from threading import Lock
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .units import kilometres_to_metres, kilometres_to_nautical_miles, knots_to_metres_per_second

LOGGER = logging.getLogger(__name__)
ADSB_LOL_BASE_URL = "https://api.adsb.lol/v2/point"
DEFAULT_LATITUDE = 50.65
DEFAULT_LONGITUDE = -1.3
DEFAULT_RADIUS_KILOMETRES = 25
REFRESH_INTERVAL_SECONDS = 10
TRAIL_DURATION_SECONDS = 300
MAX_FAILURE_DELAY_SECONDS = 300
VIEWPORT_CONTEXT_URL = os.environ.get(
    "KODY_VIEWPORT_CONTEXT_URL",
    "http://127.0.0.1:8001/api/context/viewport",
)
VIEWPORT_MARGIN_KILOMETRES = 5
ROOT_DIRECTORY = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT_DIRECTORY / "config" / "adsb.json"


class Position(BaseModel):
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-85, le=85)


class ViewportContext(BaseModel):
    west: float = Field(ge=-180, le=180)
    south: float = Field(ge=-85, le=85)
    east: float = Field(ge=-180, le=180)
    north: float = Field(ge=-85, le=85)
    zoom: float = Field(ge=0, le=18)


class AdsbConfiguration(BaseModel):
    centre: Position = Field(
        default_factory=lambda: Position(
            longitude=DEFAULT_LONGITUDE,
            latitude=DEFAULT_LATITUDE,
        )
    )
    radius_kilometres: int = Field(
        default=DEFAULT_RADIUS_KILOMETRES,
        alias="radiusKilometres",
        ge=1,
        le=250,
    )
    upstream_refresh_seconds: int = Field(
        default=REFRESH_INTERVAL_SECONDS,
        alias="upstreamRefreshSeconds",
        ge=REFRESH_INTERVAL_SECONDS,
        le=300,
    )
    extrapolate: bool = False
    local_refresh_seconds: int = Field(
        default=1,
        alias="localRefreshSeconds",
        ge=1,
        le=10,
    )
    track_length_seconds: int = Field(
        default=TRAIL_DURATION_SECONDS,
        alias="trackLengthSeconds",
        ge=10,
        le=3600,
    )
    track_fade: bool = Field(default=False, alias="trackFade")
    draw_boundary: bool = Field(default=False, alias="drawBoundary")
    display_color: str = Field(
        default="#ff9f1c",
        alias="displayColor",
        pattern=r"^#[0-9a-fA-F]{6}$",
    )


class ConfigurationStore:
    """Persists the ADS-B service's own validated operational settings."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = Lock()
        self.configuration = self._load()

    def _load(self) -> AdsbConfiguration:
        if not self.path.is_file():
            return AdsbConfiguration()
        return AdsbConfiguration.model_validate_json(self.path.read_text(encoding="utf-8"))

    def revision(self) -> str:
        return hashlib.sha256(
            self.configuration.model_dump_json(by_alias=True).encode("utf-8")
        ).hexdigest()

    def response(self) -> dict[str, object]:
        with self.lock:
            return {
                "revision": self.revision(),
                **self.configuration.model_dump(by_alias=True),
            }

    def update(self, configuration: AdsbConfiguration, expected_revision: str | None) -> AdsbConfiguration:
        with self.lock:
            if expected_revision is not None and expected_revision != self.revision():
                raise HTTPException(status_code=409, detail="configuration revision is stale")
            temporary_path = self.path.with_suffix(".tmp")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(
                configuration.model_dump_json(by_alias=True, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary_path.replace(self.path)
            self.configuration = configuration
            return configuration


def environment_float(name: str, default: float, minimum: float, maximum: float) -> float:
    """Read a validated float setting without accepting unsafe query parameters."""

    value = float(os.environ.get(name, default))
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def fetch_viewport() -> ViewportContext | None:
    """Read the optional current map viewport published by the central product."""

    request = Request(VIEWPORT_CONTEXT_URL, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=1) as response:
            return ViewportContext.model_validate(json.load(response))
    except HTTPError as error:
        if error.code == 404:
            return None
        LOGGER.warning("Unable to read Kody viewport context: %s", error)
    except (OSError, URLError, ValueError) as error:
        LOGGER.warning("Unable to read Kody viewport context: %s", error)
    return None


def expanded_viewport(viewport: ViewportContext) -> ViewportContext:
    latitude_margin = VIEWPORT_MARGIN_KILOMETRES / 111.32
    centre_latitude = (viewport.south + viewport.north) / 2
    longitude_margin = latitude_margin / max(0.01, cos(radians(centre_latitude)))
    return ViewportContext(
        west=max(-180, viewport.west - longitude_margin),
        south=max(-85, viewport.south - latitude_margin),
        east=min(180, viewport.east + longitude_margin),
        north=min(85, viewport.north + latitude_margin),
        zoom=viewport.zoom,
    )


def point_in_viewport(point: list[float], viewport: ViewportContext) -> bool:
    longitude, latitude = point
    return viewport.west <= longitude <= viewport.east and viewport.south <= latitude <= viewport.north


def segment_intersects_viewport(
    start: list[float],
    end: list[float],
    viewport: ViewportContext,
) -> bool:
    start_longitude, start_latitude = start
    delta_longitude = end[0] - start_longitude
    delta_latitude = end[1] - start_latitude
    start_ratio, end_ratio = 0.0, 1.0
    for change, distance in (
        (-delta_longitude, start_longitude - viewport.west),
        (delta_longitude, viewport.east - start_longitude),
        (-delta_latitude, start_latitude - viewport.south),
        (delta_latitude, viewport.north - start_latitude),
    ):
        if change == 0:
            if distance < 0:
                return False
            continue
        ratio = distance / change
        if change < 0:
            start_ratio = max(start_ratio, ratio)
        else:
            end_ratio = min(end_ratio, ratio)
        if start_ratio > end_ratio:
            return False
    return True


def line_intersects_viewport(coordinates: list[list[float]], viewport: ViewportContext) -> bool:
    return any(
        segment_intersects_viewport(start, end, viewport)
        for start, end in zip(coordinates, coordinates[1:])
    )


def geometry_intersects_viewport(geometry: dict[str, object], viewport: ViewportContext) -> bool:
    geometry_type = geometry["type"]
    coordinates = geometry.get("coordinates")
    if geometry_type == "Point":
        return point_in_viewport(coordinates, viewport)
    if geometry_type == "LineString":
        return line_intersects_viewport(coordinates, viewport)
    if geometry_type == "MultiLineString":
        return any(line_intersects_viewport(line, viewport) for line in coordinates)
    return True


def aircraft_url(latitude: float, longitude: float, radius_kilometres: int) -> str:
    """Build ADSB.lol's point query, whose radius is expressed in nautical miles."""

    radius_nautical_miles = kilometres_to_nautical_miles(radius_kilometres)
    return f"{ADSB_LOL_BASE_URL}/{latitude}/{longitude}/{radius_nautical_miles:.3f}"


def destination_point(
    longitude: float,
    latitude: float,
    bearing_degrees: float,
    distance_metres: float,
) -> list[float]:
    """Project a short great-circle distance for locally extrapolated display."""

    angular_distance = distance_metres / 6_378_137.0
    bearing = radians(bearing_degrees)
    origin_latitude = radians(latitude)
    origin_longitude = radians(longitude)
    target_latitude = asin(
        sin(origin_latitude) * cos(angular_distance)
        + cos(origin_latitude) * sin(angular_distance) * cos(bearing)
    )
    target_longitude = origin_longitude + atan2(
        sin(bearing) * sin(angular_distance) * cos(origin_latitude),
        cos(angular_distance) - sin(origin_latitude) * sin(target_latitude),
    )
    return [degrees(target_longitude), degrees(target_latitude)]


def fetch_aircraft(url: str) -> list[dict[str, Any]]:
    """Fetch one bounded ADSB.lol point query."""

    request = Request(url, headers={"User-Agent": "Kody-ADSB-Service/0.1"})
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError(f"ADSB.lol request failed: {error}") from error
    aircraft = payload.get("ac")
    if not isinstance(aircraft, list):
        raise RuntimeError("ADSB.lol response did not contain an aircraft list")
    return [item for item in aircraft if isinstance(item, dict)]


class AdsbStore:
    """Caches small-area ADS-B observations and creates rolling GeoJSON tails."""

    def __init__(
        self,
        fetcher: Callable[[str], list[dict[str, Any]]] = fetch_aircraft,
        clock: Callable[[], float] = time.time,
        configuration: AdsbConfiguration | None = None,
        viewport_reader: Callable[[], ViewportContext | None] | None = None,
    ) -> None:
        self.fetcher = fetcher
        self.clock = clock
        self.configuration = configuration or AdsbConfiguration()
        self.viewport_reader = viewport_reader or (lambda: None)
        self.url = aircraft_url(
            self.configuration.centre.latitude,
            self.configuration.centre.longitude,
            self.configuration.radius_kilometres,
        )
        self.lock = Lock()
        self.tracks: dict[str, deque[tuple[float, list[float]]]] = {}
        self.last_seen: dict[str, float] = {}
        self.features: list[dict[str, object]] = []
        self.last_success: float | None = None
        self.last_error: str | None = None
        self.next_refresh = 0.0
        self.failure_count = 0

    def apply_configuration(self, configuration: AdsbConfiguration) -> None:
        with self.lock:
            location_changed = (
                configuration.centre != self.configuration.centre
                or configuration.radius_kilometres != self.configuration.radius_kilometres
            )
            self.configuration = configuration
            self.url = aircraft_url(
                configuration.centre.latitude,
                configuration.centre.longitude,
                configuration.radius_kilometres,
            )
            self.next_refresh = 0.0
            self.failure_count = 0
            for feature in self.features:
                properties = feature.get("properties")
                if isinstance(properties, dict):
                    properties["color"] = configuration.display_color
            if location_changed:
                self.tracks.clear()
                self.last_seen.clear()
                self.features = []

    def _feature_properties(self, aircraft: dict[str, Any], aircraft_id: str) -> dict[str, object]:
        callsign = str(aircraft.get("flight") or "").strip()
        registration = str(aircraft.get("r") or "").strip()
        category = str(aircraft.get("category") or "")
        return {
            "name": callsign or registration or aircraft_id,
            "color": self.configuration.display_color,
            "kind": "track-position",
            "symbol": "heli" if category == "A7" else "plane",
            "bearingDegrees": aircraft.get("true_heading") or aircraft.get("track") or 0,
            "icao24": aircraft_id,
            "callsign": callsign or None,
            "registration": registration or None,
            "aircraftType": aircraft.get("t"),
            "altitudeBarometricFeet": aircraft.get("alt_baro"),
            "groundSpeedKnots": aircraft.get("gs"),
            "trackDegrees": aircraft.get("track"),
            "trueHeadingDegrees": aircraft.get("true_heading"),
            "verticalRateFeetPerMinute": aircraft.get("baro_rate"),
            "positionAgeSeconds": aircraft.get("seen_pos"),
            "sourceType": aircraft.get("type"),
        }

    def _update_features(self, observations: list[dict[str, Any]], sampled_at: float) -> None:
        features: list[dict[str, object]] = []
        active_ids: set[str] = set()
        for aircraft in observations:
            aircraft_id = str(aircraft.get("hex") or "").lower()
            latitude, longitude = aircraft.get("lat"), aircraft.get("lon")
            if not aircraft_id or not isinstance(latitude, (int, float)) or not isinstance(
                longitude, (int, float)
            ):
                continue
            point = [float(longitude), float(latitude)]
            track = self.tracks.setdefault(aircraft_id, deque())
            if not track or track[-1][1] != point:
                track.append((sampled_at, point))
            trail_start = sampled_at - self.configuration.track_length_seconds
            while track and track[0][0] < trail_start:
                track.popleft()
            self.last_seen[aircraft_id] = sampled_at
            active_ids.add(aircraft_id)
            properties = self._feature_properties(aircraft, aircraft_id)
            if len(track) >= 2:
                features.append(
                    {
                        "type": "Feature",
                        "properties": {
                            **properties,
                            "name": f"{properties['name']} trail",
                            "kind": "track-trail",
                            "durationSeconds": self.configuration.track_length_seconds,
                            "fadeTrail": self.configuration.track_fade,
                        },
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [point for _, point in track],
                        },
                    }
                )
            features.append(
                {
                    "type": "Feature",
                    "properties": properties,
                    "geometry": {"type": "Point", "coordinates": point},
                }
            )
        stale_before = sampled_at - self.configuration.track_length_seconds
        for aircraft_id, last_seen in list(self.last_seen.items()):
            if aircraft_id not in active_ids and last_seen < stale_before:
                self.last_seen.pop(aircraft_id)
                self.tracks.pop(aircraft_id, None)
        self.features = features

    def refresh_if_due(self) -> None:
        with self.lock:
            sampled_at = self.clock()
            if sampled_at < self.next_refresh:
                return
            try:
                observations = self.fetcher(self.url)
            except RuntimeError as error:
                self.failure_count += 1
                delay = min(
                    self.configuration.upstream_refresh_seconds * 2**self.failure_count,
                    MAX_FAILURE_DELAY_SECONDS,
                )
                self.next_refresh = sampled_at + delay
                self.last_error = str(error)
                LOGGER.warning("ADSB.lol refresh failed; retrying in %s seconds: %s", delay, error)
                return
            self._update_features(observations, sampled_at)
            self.last_success = sampled_at
            self.last_error = None
            self.failure_count = 0
            self.next_refresh = sampled_at + self.configuration.upstream_refresh_seconds

    def _shared_features(self, shared_at: float) -> list[dict[str, object]]:
        configuration = self.configuration
        projected_positions: dict[str, list[float]] = {}
        if configuration.extrapolate and self.last_success is not None:
            interval = configuration.local_refresh_seconds
            elapsed = min(
                max(0.0, (shared_at // interval) * interval - self.last_success),
                float(configuration.upstream_refresh_seconds),
            )
            for feature in self.features:
                if feature["geometry"]["type"] != "Point":
                    continue
                properties = feature["properties"]
                speed = properties.get("groundSpeedKnots")
                heading = properties.get("trackDegrees")
                if not isinstance(speed, (int, float)) or not isinstance(heading, (int, float)):
                    continue
                longitude, latitude = feature["geometry"]["coordinates"]
                projected_positions[properties["icao24"]] = destination_point(
                    longitude,
                    latitude,
                    heading,
                    knots_to_metres_per_second(speed) * elapsed,
                )
        features: list[dict[str, object]] = []
        for feature in self.features:
            geometry = feature["geometry"]
            properties = feature["properties"]
            aircraft_id = properties["icao24"]
            projected = projected_positions.get(aircraft_id)
            if geometry["type"] == "Point" and projected is not None:
                features.append(
                    {
                        **feature,
                        "properties": {**properties, "isExtrapolated": True},
                        "geometry": {"type": "Point", "coordinates": projected},
                    }
                )
            elif geometry["type"] == "LineString" and projected is not None:
                features.append(
                    {
                        **feature,
                        "properties": {**properties, "isExtrapolated": True},
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [*geometry["coordinates"], projected],
                        },
                    }
                )
            else:
                features.append(feature)
        if configuration.draw_boundary:
            coordinates = [
                destination_point(
                    configuration.centre.longitude,
                    configuration.centre.latitude,
                    step * 360 / 48,
                    kilometres_to_metres(configuration.radius_kilometres),
                )
                for step in range(49)
            ]
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "name": "ADS-B coverage boundary",
                        "color": configuration.display_color,
                        "kind": "adsb-boundary",
                        "lineStyle": "dotted",
                    },
                    "geometry": {"type": "LineString", "coordinates": coordinates},
                }
            )
        return features

    def response(self) -> dict[str, object]:
        self.refresh_if_due()
        viewport = self.viewport_reader()
        with self.lock:
            features = self._shared_features(self.clock())
            if viewport is not None:
                viewport = expanded_viewport(viewport)
                features = [
                    feature
                    for feature in features
                    if geometry_intersects_viewport(feature["geometry"], viewport)
                ]
            collection: dict[str, object] = {
                "type": "FeatureCollection",
                "features": features,
                "source": {
                    "id": "adsb-lol",
                    "attribution": "ADSB.lol data, ODbL 1.0",
                    "upstreamUrl": self.url,
                    "lastSuccess": self.last_success,
                    "lastError": self.last_error,
                    "viewportFiltered": viewport is not None,
                },
            }
            revision = hashlib.sha256(
                json.dumps(collection, separators=(",", ":"), sort_keys=True).encode("utf-8")
            ).hexdigest()
            return {"revision": revision, **collection}


configuration_store = ConfigurationStore(CONFIG_FILE)
adsb_store = AdsbStore(
    configuration=configuration_store.configuration,
    viewport_reader=fetch_viewport,
)
app = FastAPI(title="Kody ADS-B Service", version="0.1.0")
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
        "serviceId": "adsb-live-flights",
        "displayName": "ADS-B live flights",
        "dataEndpoint": {"path": "/api/geojson", "format": "geojson"},
        "configuration": {
            "readPath": "/api/config",
            "writePath": "/api/config",
            "fields": [
                {"key": "centre", "label": "Search centre", "type": "position"},
                {
                    "key": "radiusKilometres",
                    "label": "Search radius (km)",
                    "type": "number",
                    "minimum": 1,
                    "maximum": 250,
                    "step": 1,
                },
                {
                    "key": "upstreamRefreshSeconds",
                    "label": "Upstream refresh (seconds)",
                    "type": "number",
                    "minimum": 10,
                    "maximum": 300,
                    "step": 1,
                },
                {
                    "key": "extrapolate",
                    "label": "Extrapolate tracks between observations",
                    "type": "boolean",
                },
                {
                    "key": "localRefreshSeconds",
                    "label": "Local share refresh (seconds)",
                    "type": "number",
                    "minimum": 1,
                    "maximum": 10,
                    "step": 1,
                },
                {
                    "key": "trackLengthSeconds",
                    "label": "Track length (seconds)",
                    "type": "number",
                    "minimum": 10,
                    "maximum": 3600,
                    "step": 1,
                },
                {
                    "key": "trackFade",
                    "label": "Track fade",
                    "type": "boolean",
                },
                {
                    "key": "drawBoundary",
                    "label": "Draw dotted ADS-B coverage boundary",
                    "type": "boolean",
                },
                {
                    "key": "displayColor",
                    "label": "Display color",
                    "type": "color",
                },
            ],
        },
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
def update_configuration(
    configuration: AdsbConfiguration,
    if_match: str | None = Header(default=None),
) -> dict[str, object]:
    updated = configuration_store.update(configuration, if_match)
    adsb_store.apply_configuration(updated)
    return configuration_store.response()


@app.post("/api/actions/set-origin")
def set_origin(position: Position) -> dict[str, object]:
    configuration = configuration_store.configuration.model_copy(update={"centre": position})
    updated = configuration_store.update(configuration, None)
    adsb_store.apply_configuration(updated)
    return configuration_store.response()


@app.get("/api/geojson")
def get_aircraft() -> dict[str, object]:
    """Return the cached live aircraft positions and rolling local tails."""

    return adsb_store.response()
