"""Declarative, file-backed shape catalogue for the Kody map."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
from pathlib import Path
from threading import Lock
from typing import Annotated, Any, Literal

from fastapi import Body, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

ROOT_DIRECTORY = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get("KODY_DATA_DIRECTORY", ROOT_DIRECTORY / "data"))
CATALOGUE_FILE = DATA_ROOT / "shapes" / "shapes.json"
LEGACY_SHAPES_FILE = DATA_ROOT / "shapes" / "shapes.geojson"
CONFIG_FILE = ROOT_DIRECTORY / "config" / "shapes.json"
COLOUR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$")
EARTH_RADIUS_METRES = 6_371_008.8
ARC_SEGMENTS = 64


class ShapesConfiguration(BaseModel):
    """Reserved for future Shapes-service settings."""


class ShapeBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    name: str
    color: str

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or len(value) > 200):
            raise ValueError("id must be a non-empty string of at most 200 characters")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip() or len(value) > 200:
            raise ValueError("name must be a non-empty string of at most 200 characters")
        return value

    @field_validator("color")
    @classmethod
    def validate_colour(cls, value: str) -> str:
        if not COLOUR_PATTERN.fullmatch(value):
            raise ValueError("color must be a hexadecimal #RRGGBB or #RRGGBBAA value")
        return value


def _coordinate(value: list[float]) -> list[float]:
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


class CircleDefinition(ShapeBase):
    type: Literal["circle"]
    center: Coordinate
    radius: float = Field(gt=0, le=20_000_000)

    _validate_center = field_validator("center")(_coordinate)


class RectangleDefinition(ShapeBase):
    type: Literal["rectangle"]
    origin: Coordinate
    width: float = Field(gt=0, le=40_000_000)
    height: float = Field(gt=0, le=40_000_000)
    angle: float = Field(ge=-360, le=360)

    _validate_origin = field_validator("origin")(_coordinate)


class AnnularSectorDefinition(ShapeBase):
    type: Literal["annularSector"]
    center: Coordinate
    innerRadius: float = Field(ge=0, le=20_000_000)
    outerRadius: float = Field(gt=0, le=20_000_000)
    startAngle: float = Field(ge=-360, le=360)
    endAngle: float = Field(ge=-360, le=360)

    _validate_center = field_validator("center")(_coordinate)

    @field_validator("outerRadius")
    @classmethod
    def validate_outer_radius(cls, value: float, info: Any) -> float:
        inner_radius = info.data.get("innerRadius")
        if inner_radius is not None and value <= inner_radius:
            raise ValueError("outerRadius must be greater than innerRadius")
        return value


class PolygonDefinition(ShapeBase):
    type: Literal["polygon"]
    coordinates: list[Coordinate] = Field(min_length=3, max_length=10_000)

    @field_validator("coordinates")
    @classmethod
    def validate_polygon(cls, value: list[list[float]]) -> list[list[float]]:
        if len({(coordinate[0], coordinate[1]) for coordinate in value}) < 3:
            raise ValueError("polygon coordinates must contain at least three distinct positions")
        return value


ShapeDefinition = Annotated[
    CircleDefinition | RectangleDefinition | AnnularSectorDefinition | PolygonDefinition,
    Field(discriminator="type"),
]


class ShapeCatalogue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal[1] = 1
    shapes: list[ShapeDefinition] = Field(default_factory=list)

    @field_validator("shapes")
    @classmethod
    def validate_unique_ids(cls, value: list[ShapeDefinition]) -> list[ShapeDefinition]:
        ids = [shape.id for shape in value]
        if any(shape_id is None for shape_id in ids) or len(set(ids)) != len(ids):
            raise ValueError("every shape requires a unique id")
        return value


class ConfigurationStore:
    """Persists Shapes-service presentation settings beside its own source data."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = Lock()
        self.configuration = self._load()

    def _load(self) -> ShapesConfiguration:
        if not self.path.is_file():
            return ShapesConfiguration()
        return ShapesConfiguration.model_validate_json(self.path.read_text(encoding="utf-8"))

    def revision(self) -> str:
        return hashlib.sha256(
            self.configuration.model_dump_json(by_alias=True).encode("utf-8")
        ).hexdigest()

    def response(self) -> dict[str, object]:
        with self.lock:
            return {"revision": self.revision(), **self.configuration.model_dump(by_alias=True)}

    def update(self, configuration: ShapesConfiguration, expected_revision: str | None) -> None:
        with self.lock:
            if expected_revision is not None and expected_revision != self.revision():
                raise HTTPException(status_code=409, detail="configuration revision is stale")
            atomic_json_write(self.path, configuration.model_dump(by_alias=True))
            self.configuration = configuration


def atomic_json_write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(path)


def catalogue_error(message: str) -> RuntimeError:
    return RuntimeError(f"shapes catalogue is invalid: {message}")


def migrate_legacy_shapes(path: Path) -> ShapeCatalogue:
    """Migrate polygons from the former GeoJSON source without discarding it."""
    if not path.is_file():
        return ShapeCatalogue()
    try:
        collection = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise catalogue_error(f"cannot read legacy {path.name}") from error
    features = collection.get("features") if isinstance(collection, dict) else None
    if collection.get("type") != "FeatureCollection" or not isinstance(features, list):
        raise catalogue_error("legacy shapes.geojson must be a GeoJSON FeatureCollection")

    shapes: list[ShapeDefinition] = []
    for index, feature in enumerate(features):
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties")
        geometry = feature.get("geometry")
        if not isinstance(properties, dict) or not isinstance(geometry, dict):
            continue
        name, color = properties.get("name"), properties.get("color")
        if not isinstance(name, str) or not isinstance(color, str):
            continue
        geometry_type = geometry.get("type")
        polygons = [geometry.get("coordinates")] if geometry_type == "Polygon" else (
            geometry.get("coordinates") if geometry_type == "MultiPolygon" else []
        )
        if not isinstance(polygons, list):
            continue
        for polygon_number, polygon in enumerate(polygons):
            if not isinstance(polygon, list) or not polygon or not isinstance(polygon[0], list):
                continue
            ring = polygon[0]
            if len(ring) > 1 and ring[0] == ring[-1]:
                ring = ring[:-1]
            try:
                shapes.append(
                    PolygonDefinition(
                        id=f"legacy-{index}-{polygon_number}",
                        name=name if len(polygons) == 1 else f"{name} {polygon_number + 1}",
                        color=color,
                        type="polygon",
                        coordinates=ring,
                    )
                )
            except ValidationError:
                continue
    return ShapeCatalogue(shapes=shapes)


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
    return [
        ((math.degrees(destination_longitude) + 540) % 360) - 180,
        math.degrees(destination_latitude),
    ]


def shape_feature(shape: ShapeDefinition) -> dict[str, object]:
    properties = {
        "name": shape.name,
        "color": shape.color,
        "shapeId": shape.id,
        "shapeType": shape.type,
        "kody": {"selectable": True, "selectionLabel": shape.name},
        "kodyActions": ["edit-on-map"],
    }
    if isinstance(shape, CircleDefinition):
        coordinates = [destination(shape.center, angle, shape.radius) for angle in range(0, 361, 360 // ARC_SEGMENTS)]
    elif isinstance(shape, RectangleDefinition):
        coordinates = []
        for east, north in ((-shape.width / 2, -shape.height / 2), (shape.width / 2, -shape.height / 2),
                            (shape.width / 2, shape.height / 2), (-shape.width / 2, shape.height / 2)):
            radians = math.radians(shape.angle)
            rotated_east = east * math.cos(radians) + north * math.sin(radians)
            rotated_north = -east * math.sin(radians) + north * math.cos(radians)
            coordinates.append(destination(shape.origin, math.degrees(math.atan2(rotated_east, rotated_north)), math.hypot(rotated_east, rotated_north)))
        coordinates.append(coordinates[0])
    elif isinstance(shape, AnnularSectorDefinition):
        sweep = (shape.endAngle - shape.startAngle) % 360 or 360
        angles = [shape.startAngle + sweep * index / ARC_SEGMENTS for index in range(ARC_SEGMENTS + 1)]
        outer = [destination(shape.center, angle, shape.outerRadius) for angle in angles]
        inner = [destination(shape.center, angle, shape.innerRadius) for angle in reversed(angles)]
        coordinates = outer + inner + [outer[0]]
    else:
        coordinates = shape.coordinates + [shape.coordinates[0]]
    return {
        "type": "Feature",
        "id": shape.id,
        "properties": properties,
        "geometry": {"type": "Polygon", "coordinates": [coordinates]},
    }


class ShapesStore:
    """Atomically persisted declarative shapes, rendered to GeoJSON on demand."""

    def __init__(self, path: Path, legacy_path: Path | None = None) -> None:
        self.path = path
        self.legacy_path = legacy_path or path.with_suffix(".geojson")
        self.lock = Lock()
        self.catalogue = self._load()

    def _load(self) -> ShapeCatalogue:
        if self.path.is_file():
            try:
                return ShapeCatalogue.model_validate_json(self.path.read_text(encoding="utf-8"))
            except (OSError, ValidationError, json.JSONDecodeError) as error:
                raise catalogue_error(f"cannot read {self.path.name}") from error
        catalogue = migrate_legacy_shapes(self.legacy_path)
        atomic_json_write(self.path, catalogue.model_dump(mode="json"))
        return catalogue

    def revision(self) -> str:
        return hashlib.sha256(
            json.dumps(self.catalogue.model_dump(mode="json"), separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()

    def definitions_response(self) -> dict[str, object]:
        with self.lock:
            return {"revision": self.revision(), **self.catalogue.model_dump(mode="json")}

    def geojson_response(self) -> dict[str, object]:
        with self.lock:
            return {
                "revision": self.revision(),
                "type": "FeatureCollection",
                "features": [shape_feature(shape) for shape in self.catalogue.shapes],
            }

    def _check_revision(self, expected_revision: str | None) -> None:
        if expected_revision is not None and expected_revision != self.revision():
            raise HTTPException(status_code=409, detail="shape catalogue revision is stale")

    def _persist(self) -> None:
        atomic_json_write(self.path, self.catalogue.model_dump(mode="json"))

    def create(self, shape: ShapeDefinition, expected_revision: str | None) -> ShapeDefinition:
        with self.lock:
            self._check_revision(expected_revision)
            assigned = shape.model_copy(update={"id": shape.id or str(uuid.uuid4())})
            if any(existing.id == assigned.id for existing in self.catalogue.shapes):
                raise HTTPException(status_code=409, detail=f"shape id already exists: {assigned.id}")
            self.catalogue = ShapeCatalogue(shapes=[*self.catalogue.shapes, assigned])
            self._persist()
            return assigned

    def update(self, shape_id: str, shape: ShapeDefinition, expected_revision: str | None) -> ShapeDefinition:
        with self.lock:
            self._check_revision(expected_revision)
            for index, existing in enumerate(self.catalogue.shapes):
                if existing.id == shape_id:
                    if shape.id is not None and shape.id != shape_id:
                        raise HTTPException(status_code=400, detail="shape id in the body must match the path")
                    updated = shape.model_copy(update={"id": shape_id})
                    shapes = list(self.catalogue.shapes)
                    shapes[index] = updated
                    self.catalogue = ShapeCatalogue(shapes=shapes)
                    self._persist()
                    return updated
            raise HTTPException(status_code=404, detail=f"shape not found: {shape_id}")

    def delete(self, shape_id: str, expected_revision: str | None) -> None:
        with self.lock:
            self._check_revision(expected_revision)
            shapes = [shape for shape in self.catalogue.shapes if shape.id != shape_id]
            if len(shapes) == len(self.catalogue.shapes):
                raise HTTPException(status_code=404, detail=f"shape not found: {shape_id}")
            self.catalogue = ShapeCatalogue(shapes=shapes)
            self._persist()

    def clear(self) -> None:
        with self.lock:
            self.catalogue = ShapeCatalogue()
            self._persist()


shapes_store = ShapesStore(CATALOGUE_FILE, LEGACY_SHAPES_FILE)
configuration_store = ConfigurationStore(CONFIG_FILE)
app = FastAPI(title="Kody Shapes Service", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_methods=["GET", "PUT", "POST", "DELETE"],
    allow_headers=["Content-Type", "If-Match"],
)


@app.get("/api/kody/manifest")
def get_manifest() -> dict[str, object]:
    return {
        "kodyApiVersion": "1",
        "serviceId": "shapes",
        "displayName": "Shapes",
        "dataEndpoint": {"path": "/api/shapes", "format": "geojson"},
        "capabilities": {
            "dataLayer": {
                "kind": "geojson",
                "dataPath": "/api/shapes",
            },
            "shapeEditor": {
                "readPath": "/api/shapes/definitions",
                "createPath": "/api/shapes/definitions",
                "itemPath": "/api/shapes/definitions/{id}",
                "updateMethod": "PUT",
                "deleteMethod": "DELETE",
                "revisionHeader": "If-Match",
                "types": ["circle", "rectangle", "annularSector", "polygon"],
            },
            "workspace": {
                "id": "shapes-catalog",
                "label": "Shapes",
                "openPath": "/api/workspace",
                "revisionHeader": "If-Match",
            },
        },
        "featureActions": [{
            "id": "edit-on-map",
            "label": "Edit on map",
            "path": "/api/workspace",
            "workspace": {
                "mode": "edit-on-map",
                "resource": "definitions",
                "featureIdProperty": "shapeId",
            },
        }],
        "configuration": {
            "readPath": "/api/config",
            "writePath": "/api/config",
            "fields": [],
            "actions": [{
                "label": "Clear local data",
                "path": "/api/actions/clear-local-data",
                "method": "POST",
                "confirmationMessage": "Clear all local Shapes data? This cannot be undone.",
            }],
        },
    }


@app.get("/api/workspace")
def get_workspace() -> dict[str, object]:
    """Describe the trusted Shapes catalogue workspace."""
    return {
        "kind": "shapes-catalog",
        "title": "Shapes catalog",
        "resources": {
            "definitions": "/api/shapes/definitions",
        },
        "panes": [{
            "id": "catalog",
            "label": "Catalog",
            "description": "Create, select, and maintain declarative map shapes.",
            "summary": {"metrics": [], "items": []},
        }],
        "timeline": {
            "description": "Draw a shape on the map to populate its geometry, then save it from the inspector.",
        },
    }


@app.get("/api/config")
def get_configuration() -> dict[str, object]:
    return configuration_store.response()


@app.put("/api/config")
def update_configuration(
    configuration: ShapesConfiguration,
    if_match: str | None = Header(default=None),
) -> dict[str, object]:
    configuration_store.update(configuration, if_match)
    return configuration_store.response()


@app.get("/api/shapes")
def get_shapes() -> dict[str, object]:
    return shapes_store.geojson_response()


@app.get("/api/shapes/definitions")
def get_shape_definitions() -> dict[str, object]:
    return shapes_store.definitions_response()


@app.post("/api/shapes/definitions", status_code=status.HTTP_201_CREATED)
def create_shape(
    shape: ShapeDefinition = Body(...),
    if_match: str | None = Header(default=None),
) -> dict[str, object]:
    created = shapes_store.create(shape, if_match)
    return {"revision": shapes_store.definitions_response()["revision"], "shape": created.model_dump(mode="json")}


@app.put("/api/shapes/definitions/{shape_id}")
def update_shape(
    shape_id: str,
    shape: ShapeDefinition = Body(...),
    if_match: str | None = Header(default=None),
) -> dict[str, object]:
    updated = shapes_store.update(shape_id, shape, if_match)
    return {"revision": shapes_store.definitions_response()["revision"], "shape": updated.model_dump(mode="json")}


@app.delete("/api/shapes/definitions/{shape_id}")
def delete_shape(shape_id: str, if_match: str | None = Header(default=None)) -> dict[str, object]:
    shapes_store.delete(shape_id, if_match)
    return shapes_store.definitions_response()


@app.post("/api/actions/clear-local-data")
def clear_local_data() -> dict[str, str]:
    shapes_store.clear()
    return {"message": "Cleared local Shapes data."}
