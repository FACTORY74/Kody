"""Static WebP tile serving and source-build coordination for Kody."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

ROOT_DIRECTORY = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get("KODY_DATA_DIRECTORY", ROOT_DIRECTORY / "data"))
IMPORTS_DIRECTORY = DATA_ROOT / "osmtiles" / "imports"
TILES_DIRECTORY = DATA_ROOT / "osmtiles" / "tiles"
CONFIG_FILE = ROOT_DIRECTORY / "config" / "osmtiles.json"
STYLE_FILE = ROOT_DIRECTORY / "config" / "kody-style.json"


def initialize_data_directory() -> None:
    IMPORTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    TILES_DIRECTORY.mkdir(parents=True, exist_ok=True)


initialize_data_directory()


class TileConfiguration(BaseModel):
    source_filename: str = Field(default="", alias="sourceFilename")
    min_zoom: int = Field(default=0, alias="minZoom", ge=0, le=18)
    max_zoom: int = Field(default=12, alias="maxZoom", ge=0, le=18)
    refresh_existing: bool = Field(default=False, alias="refreshExisting")


class ConfigurationStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = Lock()
        self.configuration = self._load()

    def _load(self) -> TileConfiguration:
        if not self.path.is_file():
            return TileConfiguration()
        return TileConfiguration.model_validate_json(self.path.read_text(encoding="utf-8"))

    def response(self) -> dict[str, object]:
        with self.lock:
            document = self.configuration.model_dump(by_alias=True)
            revision = hashlib.sha256(json.dumps(document, sort_keys=True).encode("utf-8")).hexdigest()
            return {"revision": revision, **document}

    def update(self, configuration: TileConfiguration, expected_revision: str | None) -> dict[str, object]:
        with self.lock:
            if configuration.max_zoom < configuration.min_zoom:
                raise HTTPException(status_code=422, detail="maxZoom must be at least minZoom")
            if expected_revision is not None and expected_revision != self.response()["revision"]:
                raise HTTPException(status_code=409, detail="configuration revision is stale")
            temporary = self.path.with_suffix(".tmp")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(configuration.model_dump_json(by_alias=True, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.path)
            self.configuration = configuration
            return self.response()


def source_files() -> list[str]:
    if not IMPORTS_DIRECTORY.is_dir():
        return []
    return sorted(
        source.name
        for source in IMPORTS_DIRECTORY.iterdir()
        if source.is_file() and (
            source.suffix.lower() in {".pbf", ".gpkg"}
            or source.name.lower().endswith(".gpkg.zip")
        )
    )


configuration_store = ConfigurationStore(CONFIG_FILE)
app = FastAPI(title="Kody static OSM tile service", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_methods=["GET", "PUT"],
    allow_headers=["Content-Type", "If-Match"],
)


@app.get("/api/tile/{zoom}/{x}/{y}.webp")
def get_tile(zoom: int, x: int, y: int) -> FileResponse:
    if not 0 <= zoom <= 18 or not 0 <= x < 2**zoom or not 0 <= y < 2**zoom:
        raise HTTPException(status_code=422, detail="tile coordinates are outside the supported range")
    path = TILES_DIRECTORY / str(zoom) / str(x) / f"{y}.webp"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="static tile is not built")
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/api/kody/manifest")
def get_manifest() -> dict[str, object]:
    return {
        "kodyApiVersion": "1",
        "serviceId": "osm-tiles",
        "displayName": "Static OSM tiles",
        "configuration": {
            "readPath": "/api/config",
            "writePath": "/api/config",
            "fields": [
                {"key": "sourceFilename", "label": "OSM source filename in imports", "type": "string", "required": False},
                {"key": "minZoom", "label": "Minimum zoom", "type": "number", "minimum": 0, "maximum": 18, "step": 1},
                {"key": "maxZoom", "label": "Maximum zoom", "type": "number", "minimum": 0, "maximum": 18, "step": 1},
                {"key": "refreshExisting", "label": "Refresh existing static tiles", "type": "boolean"},
            ],
        },
    }


@app.get("/api/config")
def get_configuration() -> dict[str, object]:
    return configuration_store.response()


@app.put("/api/config")
def update_configuration(configuration: TileConfiguration, if_match: str | None = Header(default=None)) -> dict[str, object]:
    return configuration_store.update(configuration, if_match)


@app.get("/api/sources")
def get_sources() -> dict[str, object]:
    return {"sources": source_files(), "style": json.loads(STYLE_FILE.read_text(encoding="utf-8"))}
