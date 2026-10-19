"""Static GeoJSON Shapes microservice."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

ROOT_DIRECTORY = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get("KODY_DATA_DIRECTORY", ROOT_DIRECTORY / "data"))
SHAPES_FILE = DATA_ROOT / "shapes" / "shapes.geojson"
CONFIG_FILE = ROOT_DIRECTORY / "config" / "shapes.json"
COLOUR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$")
DRAWABLE_GEOMETRIES = {"Point", "LineString", "MultiLineString", "Polygon", "MultiPolygon"}
EMPTY_COLLECTION = {"type": "FeatureCollection", "features": []}


class ShapesConfiguration(BaseModel):
    """Reserved for future Shapes-service settings."""


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
            return {
                "revision": self.revision(),
                **self.configuration.model_dump(by_alias=True),
            }

    def update(self, configuration: ShapesConfiguration, expected_revision: str | None) -> None:
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


class ShapesStore:
    """Loads a validated static collection; future implementations can replace its source."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.collection = self._load()
        self.revision = hashlib.sha256(
            json.dumps(self.collection, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()

    def _load(self) -> dict[str, object]:
        if not self.path.is_file():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(EMPTY_COLLECTION, indent=2) + "\n",
                encoding="utf-8",
            )
            return EMPTY_COLLECTION
        collection = json.loads(self.path.read_text(encoding="utf-8"))
        if collection.get("type") != "FeatureCollection" or not isinstance(
            collection.get("features"), list
        ):
            raise RuntimeError("shapes file must be a GeoJSON FeatureCollection")
        for feature in collection["features"]:
            properties = feature.get("properties")
            geometry = feature.get("geometry")
            if not isinstance(properties, dict) or not isinstance(geometry, dict):
                raise RuntimeError("each shape must have properties and geometry")
            if geometry.get("type") not in DRAWABLE_GEOMETRIES or not isinstance(
                geometry.get("coordinates"), list
            ):
                raise RuntimeError("each shape requires a supported GeoJSON geometry")
            name = properties.get("name")
            colour = properties.get("color")
            if not isinstance(name, str) or not name.strip():
                raise RuntimeError("each shape requires a non-empty properties.name")
            if not isinstance(colour, str) or not COLOUR_PATTERN.fullmatch(colour):
                raise RuntimeError("each shape requires a hexadecimal properties.color")
        return collection

    def response(self) -> dict[str, object]:
        return {"revision": self.revision, **self.collection}


shapes_store = ShapesStore(SHAPES_FILE)
configuration_store = ConfigurationStore(CONFIG_FILE)
app = FastAPI(title="Kody Shapes Service", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_methods=["GET", "PUT"],
    allow_headers=["Content-Type", "If-Match"],
)


@app.get("/api/kody/manifest")
def get_manifest() -> dict[str, object]:
    return {
        "kodyApiVersion": "1",
        "serviceId": "shapes",
        "displayName": "Shapes",
        "dataEndpoint": {"path": "/api/shapes", "format": "geojson"},
        "configuration": {
            "readPath": "/api/config",
            "writePath": "/api/config",
            "fields": [],
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
    return shapes_store.response()
