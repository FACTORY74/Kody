"""Live GeoJSON demonstration service for a simulated Isle of Wight flight track."""

from __future__ import annotations

import hashlib
import json
import random
import time
from math import cos, pi, sin
from pathlib import Path
from threading import Lock
from typing import Callable

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .math import MapMath

ISLE_OF_WIGHT_LONGITUDE = -1.3
ISLE_OF_WIGHT_LATITUDE = 50.65
TRACK_RADIUS_METRES = 50_000.0
TRACK_SPEED_METRES_PER_SECOND = 100.0
TRAIL_DURATION_SECONDS = 300
ADDITIONAL_TRACK_COUNT = 20
UK_LONGITUDE_RANGE = (-7.5, 1.8)
UK_LATITUDE_RANGE = (50.1, 58.7)
TRACK_COLOURS = ("#00d4ff", "#ff6b6b", "#ffd166", "#06d6a0", "#a78bfa")
TRACK_SYMBOLS = ("plane", "heli", "balloon", "undefined")
ROOT_DIRECTORY = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT_DIRECTORY / "config" / "tracks.json"


class TracksConfiguration(BaseModel):
    trail_duration_seconds: int = Field(
        default=TRAIL_DURATION_SECONDS,
        alias="trailDurationSeconds",
        ge=30,
        le=1800,
    )


class ConfigurationStore:
    """Persists Tracks-service simulation presentation settings."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = Lock()
        self.configuration = self._load()

    def _load(self) -> TracksConfiguration:
        if not self.path.is_file():
            return TracksConfiguration()
        return TracksConfiguration.model_validate_json(self.path.read_text(encoding="utf-8"))

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

    def update(self, configuration: TracksConfiguration, expected_revision: str | None) -> None:
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


class FlightTrack:
    """Generates a constant-speed circular track in the Web Mercator plane."""

    def __init__(
        self,
        name: str,
        longitude: float,
        latitude: float,
        radius_metres: float,
        colour: str,
        phase: float,
        symbol: str,
    ) -> None:
        self.name = name
        self.radius_metres = radius_metres
        self.colour = colour
        self.phase = phase
        self.symbol = symbol
        self.angular_velocity = TRACK_SPEED_METRES_PER_SECOND / radius_metres
        self.center_x, self.center_y = MapMath.lon_lat_to_world(longitude, latitude)

    def position_at(self, timestamp: float) -> list[float]:
        angle = self.phase + timestamp * self.angular_velocity
        offset = MapMath.metres_to_world_units(self.radius_metres)
        longitude, latitude = MapMath.world_to_lon_lat(
            self.center_x + cos(angle) * offset,
            self.center_y - sin(angle) * offset,
        )
        return [longitude, latitude]

    def features_at(
        self,
        sample_time: int,
        trail_duration_seconds: int,
    ) -> list[dict[str, object]]:
        trail = [
            self.position_at(timestamp)
            for timestamp in range(sample_time - trail_duration_seconds, sample_time + 1)
        ]
        return [
            {
                "type": "Feature",
                "properties": {
                    "name": f"{self.name} trail",
                    "color": self.colour,
                    "kind": "track-trail",
                    "durationSeconds": trail_duration_seconds,
                },
                "geometry": {"type": "LineString", "coordinates": trail},
            },
            {
                "type": "Feature",
                "id": f"track:{self.name}",
                "properties": {
                    "name": self.name,
                    "color": self.colour,
                    "kind": "track-position",
                    "symbol": self.symbol,
                    "bearingDegrees": (-self.phase - sample_time * self.angular_velocity) * 180 / pi % 360,
                    "speedMetresPerSecond": TRACK_SPEED_METRES_PER_SECOND,
                    "kody": {
                        "selectable": True,
                        "selectionLabel": self.name,
                    },
                },
                "geometry": {"type": "Point", "coordinates": trail[-1]},
            },
        ]


class TrackStore:
    """Produces a reproducible group of live aircraft tracks across the UK."""

    def __init__(
        self,
        clock: Callable[[], float] = time.time,
        configuration: TracksConfiguration | None = None,
    ) -> None:
        self.clock = clock
        self.configuration = configuration or TracksConfiguration()
        self.lock = Lock()
        generator = random.Random(20_261_015)
        symbols = list(TRACK_SYMBOLS)
        generator.shuffle(symbols)
        self.tracks = (
            FlightTrack(
                "Kody demo flight",
                ISLE_OF_WIGHT_LONGITUDE,
                ISLE_OF_WIGHT_LATITUDE,
                TRACK_RADIUS_METRES,
                TRACK_COLOURS[0],
                phase=0,
                symbol=symbols[0],
            ),
            *tuple(
                FlightTrack(
                    f"Kody UK demo flight {index + 1}",
                    generator.uniform(*UK_LONGITUDE_RANGE),
                    generator.uniform(*UK_LATITUDE_RANGE),
                    generator.uniform(10_000.0, 100_000.0),
                    TRACK_COLOURS[(index + 1) % len(TRACK_COLOURS)],
                    generator.uniform(0, 2 * pi),
                    symbols[(index + 1) % len(symbols)],
                )
                for index in range(ADDITIONAL_TRACK_COUNT)
            ),
        )

    def apply_configuration(self, configuration: TracksConfiguration) -> None:
        with self.lock:
            self.configuration = configuration

    def clear(self) -> None:
        with self.lock:
            self.tracks = ()

    def response(self) -> dict[str, object]:
        sample_time = int(self.clock())
        with self.lock:
            configuration = self.configuration
        features = [
            feature
            for track in self.tracks
            for feature in track.features_at(
                sample_time,
                configuration.trail_duration_seconds,
            )
        ]
        collection: dict[str, object] = {
            "type": "FeatureCollection",
            "features": features,
        }
        revision = hashlib.sha256(
            json.dumps(collection, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()
        return {"revision": revision, **collection}


configuration_store = ConfigurationStore(CONFIG_FILE)
track_store = TrackStore(configuration=configuration_store.configuration)
app = FastAPI(title="Kody Track GeoJSON Service", version="0.1.0")
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
        "serviceId": "tracks",
        "displayName": "Live tracks",
        "dataEndpoint": {"path": "/api/geojson", "format": "geojson"},
        "configuration": {
            "readPath": "/api/config",
            "writePath": "/api/config",
            "fields": [
                {
                    "key": "trailDurationSeconds",
                    "label": "Trail duration (seconds)",
                    "type": "number",
                    "minimum": 30,
                    "maximum": 1800,
                    "step": 1,
                },
            ],
            "actions": [{
                "label": "Clear local data",
                "path": "/api/actions/clear-local-data",
                "method": "POST",
                "confirmationMessage": "Clear all local Live tracks data? This cannot be undone.",
            }],
        },
    }


@app.get("/api/config")
def get_configuration() -> dict[str, object]:
    return configuration_store.response()


@app.put("/api/config")
def update_configuration(
    configuration: TracksConfiguration,
    if_match: str | None = Header(default=None),
) -> dict[str, object]:
    configuration_store.update(configuration, if_match)
    track_store.apply_configuration(configuration)
    return configuration_store.response()


@app.post("/api/actions/clear-local-data")
def clear_local_data() -> dict[str, str]:
    track_store.clear()
    return {"message": "Cleared local Live tracks data."}


@app.get("/api/geojson")
def get_track() -> dict[str, object]:
    """Return one-hertz aircraft positions and rolling five-minute tails."""

    return track_store.response()
