"""Temporary map markers for ShapeServer track-containment callbacks."""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

EARTH_RADIUS_METRES = 6_371_008.8
CIRCLE_RADIUS_METRES = 500
CIRCLE_LIFETIME_SECONDS = 1
CIRCLE_SEGMENTS = 32


class InteractionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: dict[str, Any]
    shape: dict[str, Any]
    receivedAt: float
    matchedAt: float
    delivery: str


@dataclass(frozen=True)
class ActiveInteraction:
    key: str
    event: InteractionEvent
    expires_at: float


def circle_coordinates(longitude: float, latitude: float) -> list[list[float]]:
    origin_latitude = math.radians(latitude)
    origin_longitude = math.radians(longitude)
    angular_distance = CIRCLE_RADIUS_METRES / EARTH_RADIUS_METRES
    coordinates: list[list[float]] = []
    for index in range(CIRCLE_SEGMENTS + 1):
        bearing = math.tau * index / CIRCLE_SEGMENTS
        point_latitude = math.asin(
            math.sin(origin_latitude) * math.cos(angular_distance)
            + math.cos(origin_latitude) * math.sin(angular_distance) * math.cos(bearing)
        )
        point_longitude = origin_longitude + math.atan2(
            math.sin(bearing) * math.sin(angular_distance) * math.cos(origin_latitude),
            math.cos(angular_distance) - math.sin(origin_latitude) * math.sin(point_latitude),
        )
        coordinates.append([math.degrees(point_longitude), math.degrees(point_latitude)])
    return coordinates


class InteractionStore:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self.lock = Lock()
        self.interactions: dict[str, ActiveInteraction] = {}

    @staticmethod
    def _key(event: InteractionEvent) -> str:
        point_event = event.event
        shape_properties = event.shape.get("properties")
        shape_id = (
            shape_properties.get("shapeId")
            if isinstance(shape_properties, dict)
            else event.shape.get("id")
        )
        event_id = point_event.get("eventId")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("interaction event must include event.eventId")
        if not isinstance(shape_id, str) or not shape_id:
            raise ValueError("interaction event must include shape.properties.shapeId")
        return f"{event_id}:{shape_id}"

    def record(self, event: InteractionEvent) -> ActiveInteraction:
        interaction = ActiveInteraction(
            key=self._key(event),
            event=event,
            expires_at=self.clock() + CIRCLE_LIFETIME_SECONDS,
        )
        with self.lock:
            self.interactions[interaction.key] = interaction
        return interaction

    def _active(self) -> list[ActiveInteraction]:
        now = self.clock()
        with self.lock:
            self.interactions = {
                key: interaction
                for key, interaction in self.interactions.items()
                if interaction.expires_at > now
            }
            return list(self.interactions.values())

    def geojson_response(self) -> dict[str, object]:
        features: list[dict[str, object]] = []
        for interaction in self._active():
            point_event = interaction.event.event
            longitude = point_event.get("longitude")
            latitude = point_event.get("latitude")
            if not isinstance(longitude, (int, float)) or not isinstance(latitude, (int, float)):
                continue
            properties = point_event.get("properties")
            shape_properties = interaction.event.shape.get("properties")
            track_name = properties.get("trackName") if isinstance(properties, dict) else None
            shape_name = shape_properties.get("name") if isinstance(shape_properties, dict) else None
            features.append({
                "type": "Feature",
                "id": f"interaction:{hashlib.sha256(interaction.key.encode()).hexdigest()[:16]}",
                "properties": {
                    "name": f"{track_name or point_event.get('eventId')} in {shape_name or 'shape'}",
                    "kind": "track-shape-interaction",
                    "color": "#ffcf4a",
                    "fillOpacity": 0.3,
                    "trackName": track_name,
                    "shapeName": shape_name,
                    "matchedAt": interaction.event.matchedAt,
                    "expiresAt": interaction.expires_at,
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [circle_coordinates(float(longitude), float(latitude))],
                },
            })
        return {"type": "FeatureCollection", "features": features}

    def clear(self) -> None:
        with self.lock:
            self.interactions.clear()


interactions = InteractionStore()
app = FastAPI(title="Kody InteractionServer", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/api/kody/manifest")
def get_manifest() -> dict[str, object]:
    return {
        "kodyApiVersion": "1",
        "serviceId": "interaction-server",
        "displayName": "InteractionLayer",
        "dataEndpoint": {
            "path": "/api/interactions/geojson",
            "format": "geojson",
            "refreshIntervalMilliseconds": 200,
        },
        "capabilities": {
            "dataLayer": {"kind": "geojson", "dataPath": "/api/interactions/geojson"},
        },
    }


@app.post("/api/interactions", status_code=status.HTTP_202_ACCEPTED)
def post_interaction(event: InteractionEvent) -> dict[str, object]:
    interaction = interactions.record(event)
    return {"key": interaction.key, "expiresAt": interaction.expires_at}


@app.get("/api/interactions/geojson")
def get_interactions_geojson() -> dict[str, object]:
    return interactions.geojson_response()


@app.post("/api/actions/clear-local-data")
def clear_local_data() -> dict[str, str]:
    interactions.clear()
    return {"message": "Cleared active interactions."}
