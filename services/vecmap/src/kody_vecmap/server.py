"""Vector patch API for the Kody map."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from math import ceil
from pathlib import Path
from threading import Lock, RLock
from typing import Callable

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from shapely.geometry import box, mapping, shape

from .map_manager import (
    LAND_POLYGONS_FILE,
    install_geofabrik_gpkg,
    install_osm_pbf,
    rebuild_all_lod_pyramids,
)
from .map_packages import NativeTileStore
from .math import MapMath
from .quadtree import IndexedFeature, QuadTreeMap

ROOT_DIRECTORY = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get("KODY_DATA_DIRECTORY", ROOT_DIRECTORY / "data"))
MAPS_DIRECTORY = DATA_ROOT / "vecmap" / "maps"
IMPORTS_DIRECTORY = DATA_ROOT / "vecmap" / "imports"
SHARED_IMPORTS_DIRECTORY = DATA_ROOT / "imports"
CONFIG_FILE = ROOT_DIRECTORY / "config" / "vecmap.json"
ACTION_STATE_FILE = MAPS_DIRECTORY / "action-state.json"


def initialize_data_directory() -> None:
    IMPORTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    MAPS_DIRECTORY.mkdir(parents=True, exist_ok=True)


initialize_data_directory()


class VectorConfiguration(BaseModel):
    source_filename: str = Field(default="", alias="sourceFilename")
    show_major_roads: bool = Field(default=True, alias="showMajorRoads")
    show_urban_areas: bool = Field(default=True, alias="showUrbanAreas")
    show_woodland: bool = Field(default=True, alias="showWoodland")
    show_place_names: bool = Field(default=True, alias="showPlaceNames")


class ConfigurationStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = RLock()
        self.configuration = self._load()

    def _load(self) -> VectorConfiguration:
        if not self.path.is_file():
            return VectorConfiguration()
        return VectorConfiguration.model_validate_json(
            self.path.read_text(encoding="utf-8")
        )

    def revision(self) -> str:
        return hashlib.sha256(
            self.configuration.model_dump_json(by_alias=True).encode("utf-8")
        ).hexdigest()

    def response(self) -> dict[str, object]:
        with self.lock:
            return {"revision": self.revision(), **self.configuration.model_dump(by_alias=True)}

    def update(
        self, configuration: VectorConfiguration, expected_revision: str | None
    ) -> dict[str, object]:
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
            return self.response()


class ImportState:
    def __init__(self, path: Path = ACTION_STATE_FILE) -> None:
        self.path = path
        self.lock = Lock()
        if os.environ.get("KODY_VECTOR_WORKER") != "1":
            with self.lock:
                if self._read()["status"] == "running":
                    self._write(
                        {
                            "status": "failed",
                            "message": "Vector action was cancelled because the service restarted.",
                            "progress": 0,
                        }
                    )

    @staticmethod
    def _idle_response() -> dict[str, str | int]:
        return {"status": "idle", "message": "", "progress": 0}

    def _read(self) -> dict[str, str | int]:
        if not self.path.is_file():
            return self._idle_response()
        response = json.loads(self.path.read_text(encoding="utf-8"))
        return {
            "status": str(response["status"]),
            "message": str(response["message"]),
            "progress": int(response["progress"]),
        }

    def _write(self, response: dict[str, str | int]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(response, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(self.path)

    def start(self) -> None:
        with self.lock:
            if self._read()["status"] == "running":
                raise HTTPException(status_code=409, detail="a vector map operation is already running")
            self._write(
                {
                    "status": "running",
                    "message": "Preparing Vector import.",
                    "progress": 5,
                }
            )

    def update(self, message: str, progress: int) -> None:
        with self.lock:
            self._write(
                {
                    "status": "running",
                    "message": message,
                    "progress": progress,
                }
            )

    def finish(self, message: str) -> None:
        with self.lock:
            self._write({"status": "succeeded", "message": message, "progress": 100})

    def fail(self, error: Exception) -> None:
        with self.lock:
            self._write({"status": "failed", "message": str(error), "progress": 0})

    def response(self) -> dict[str, str | int]:
        with self.lock:
            return self._read()


def import_geofabrik_file() -> str:
    filename = configuration_store.configuration.source_filename
    configured_source = Path(filename)
    source = (
        configured_source.resolve()
        if configured_source.is_absolute()
        else (IMPORTS_DIRECTORY / configured_source).resolve()
    )
    if (
        not filename
        or not source.is_file()
        or not is_importable_vector_source(source)
        or (
            configured_source.is_absolute()
            and source.suffix.lower() not in {".pbf", ".geojson"}
        )
        or (
            not configured_source.is_absolute()
            and source.parent != IMPORTS_DIRECTORY.resolve()
        )
    ):
        raise ValueError(
            "sourceFilename must name a .geojson, .gpkg, .gpkg.zip, .pbf, or OSM "
            "land-polygon Shapefile ZIP in vector imports, or an existing absolute "
            ".osm.pbf or .geojson path"
        )
    return import_vector_source(source)


def is_vector_source(source: Path) -> bool:
    return (
        source.suffix.lower() in {".geojson", ".pbf", ".gpkg"}
        or source.name.lower().endswith(".gpkg.zip")
    )


def is_land_polygon_source(source: Path) -> bool:
    if source.suffix.lower() != ".zip":
        return False
    try:
        with zipfile.ZipFile(source) as archive:
            names = {entry.filename.lower() for entry in archive.infolist() if not entry.is_dir()}
    except zipfile.BadZipFile:
        return False
    shapefiles = [name for name in names if name.endswith(".shp")]
    return any(
        {
            f"{shapefile[:-4]}.shx",
            f"{shapefile[:-4]}.dbf",
        }.issubset(names)
        for shapefile in shapefiles
    )


def is_importable_vector_source(source: Path) -> bool:
    return is_vector_source(source) or is_land_polygon_source(source)


def importable_vector_sources(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        (
            source
            for source in directory.iterdir()
            if source.is_file() and is_importable_vector_source(source)
        ),
        key=lambda source: (not is_land_polygon_source(source), source.name.lower()),
    )


def all_importable_vector_sources() -> list[Path]:
    sources: dict[Path, Path] = {}
    for directory in (IMPORTS_DIRECTORY, SHARED_IMPORTS_DIRECTORY):
        for source in importable_vector_sources(directory):
            sources[source.resolve()] = source
    return sorted(
        sources.values(),
        key=lambda source: (not is_land_polygon_source(source), source.name.lower()),
    )


def geojson_features(source: Path) -> list[dict[str, object]]:
    try:
        collection = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"GeoJSON source is unreadable: {source}") from error
    if collection.get("type") != "FeatureCollection" or not isinstance(
        collection.get("features"), list
    ):
        raise ValueError(f"GeoJSON source must be a FeatureCollection: {source}")

    features: list[dict[str, object]] = []
    for index, feature in enumerate(collection["features"]):
        if not isinstance(feature, dict):
            raise ValueError(f"GeoJSON feature {index} must be an object")
        properties = feature.get("properties")
        geometry = feature.get("geometry")
        if not isinstance(properties, dict) or not isinstance(geometry, dict):
            raise ValueError(f"GeoJSON feature {index} requires properties and geometry")
        min_zoom = properties.get("minZoom", properties.get("MIN_ZOOM", 0))
        if (
            not isinstance(min_zoom, (int, float))
            or isinstance(min_zoom, bool)
            or not 0 <= min_zoom <= 18
        ):
            raise ValueError(f"GeoJSON feature {index} has an invalid minZoom")
        features.append(
            {
                "type": "Feature",
                "properties": {
                    **properties,
                    "layer": properties.get("layer", "osm-land"),
                    "minZoom": ceil(min_zoom),
                },
                "geometry": geometry,
            }
        )
    return features


def import_land_polygon_source(source: Path) -> str:
    if source.resolve() != LAND_POLYGONS_FILE.resolve():
        LAND_POLYGONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, LAND_POLYGONS_FILE)
    return (
        f"Registered {source.name} as the supporting OSM land-polygon source. "
        "It will be used automatically by the next .osm.pbf or GeoPackage import."
    )


def import_vector_source(
    source: Path, progress: Callable[[str, int], None] | None = None
) -> str:
    package_id = f"import-{hashlib.sha256(source.name.encode('utf-8')).hexdigest()[:10]}"
    report = progress or import_state.update
    report("Preparing source.", 1)
    arguments = argparse.Namespace(
        id=package_id,
        source=source,
        coverage_zoom=11,
        min_zoom=8,
        max_zoom=14,
        replace=True,
        progress=report,
    )
    if is_land_polygon_source(source):
        report("Registering supporting OSM land polygons.", 90)
        return import_land_polygon_source(source)
    if source.suffix.lower() == ".geojson":
        report("Loading GeoJSON features.", 25)
        features = geojson_features(source)
        report("Writing GeoPackage feature library.", 75)
        count = NativeTileStore(MAPS_DIRECTORY).replace_source(
            f"geojson-{source.resolve()}",
            features,
        )
        report("Finalizing native Vector tiles.", 99)
        return f"Merged {count} GeoJSON features from {source.name} into the native vector tile store."
    (install_osm_pbf if source.suffix.lower() == ".pbf" else install_geofabrik_gpkg)(
        arguments
    )
    report("Finalizing native Vector tiles.", 99)
    return f"Merged {source.name} into the native vector tile store."


def import_all_vector_sources() -> str:
    sources = all_importable_vector_sources()
    if not sources:
        raise ValueError(
            "no .geojson, .osm.pbf, .gpkg, .gpkg.zip, or OSM land-polygon Shapefile "
            "ZIP sources were found in vector or shared imports"
        )
    for index, source in enumerate(sources, start=1):
        source_start = round((index - 1) / len(sources) * 100)
        source_end = round(index / len(sources) * 100)

        def report(message: str, progress: int) -> None:
            overall_progress = source_start + round(
                max(0, min(100, progress)) / 100 * (source_end - source_start)
            )
            import_state.update(
                f"Importing {source.name} ({index} of {len(sources)}): {message}",
                overall_progress,
            )

        import_vector_source(source, report)
    return f"Merged {len(sources)} Vector source file(s) into the native tile store."


def filter_vector_features(
    collection: dict[str, object], configuration: VectorConfiguration
) -> dict[str, object]:
    visible_layers = {
        "osm-road": configuration.show_major_roads,
        "osm-urban": configuration.show_urban_areas,
        "osm-forest": configuration.show_woodland,
        "osm-place": configuration.show_place_names,
    }
    features = collection.get("features", [])
    if not isinstance(features, list):
        return collection
    return {
        **collection,
        "features": [
            feature
            for feature in features
            if not isinstance(feature, dict)
            or visible_layers.get(
                feature.get("properties", {}).get("layer"), True
            )
        ],
    }


class VectorPatchService:
    """Serves directly addressed native Vector tiles, otherwise the Earth base."""

    def __init__(self, sources: list[Path], maps_directory: Path = MAPS_DIRECTORY) -> None:
        self.base_index = QuadTreeMap()
        for source in sources:
            self._load_source(source, self.base_index)
        self.tile_store = NativeTileStore(maps_directory)

    @staticmethod
    def _load_source(source: Path, index: QuadTreeMap) -> None:
        if not source.is_file():
            raise RuntimeError(f"required map data file is missing: {source}")
        with source.open(encoding="utf-8") as stream:
            collection = json.load(stream)
        if collection.get("type") != "FeatureCollection":
            raise RuntimeError(f"map data is not a GeoJSON FeatureCollection: {source}")
        for feature in collection.get("features", []):
            geometry_data = feature.get("geometry")
            if not geometry_data:
                continue
            geometry = shape(geometry_data)
            if not geometry.is_empty:
                index.insert(IndexedFeature(feature.get("properties", {}), geometry))

    def patch(self, zoom: int, x: int, y: int, buffer_pixels: int = 0) -> dict[str, object]:
        try:
            tile_bounds = MapMath.tile_bounds(zoom, x, y, buffer_pixels)
            display_bounds = MapMath.tile_bounds(zoom, x, y)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        tile_geometry = box(
            tile_bounds.west, tile_bounds.south, tile_bounds.east, tile_bounds.north
        )
        tile = self.tile_store.tile_collection(zoom, x, y)
        if tile is not None:
            collection, lod = tile
            return {
                **collection,
                "tile": {
                    "z": zoom,
                    "x": x,
                    "y": y,
                    "bounds": [
                        display_bounds.west,
                        display_bounds.south,
                        display_bounds.east,
                        display_bounds.north,
                    ],
                },
                "source": {
                    "id": "native-vector-tiles",
                    "attribution": "© OpenStreetMap contributors",
                    "lod": lod,
                },
            }
        features = []
        for entry in self.base_index.query(tile_bounds):
            clipped_geometry = entry.geometry.intersection(tile_geometry)
            if not clipped_geometry.is_empty:
                features.append(
                    {
                        "type": "Feature",
                        "properties": entry.properties,
                        "geometry": mapping(clipped_geometry),
                    }
                )

        return {
            "type": "FeatureCollection",
            "features": features,
            "tile": {
                "z": zoom,
                "x": x,
                "y": y,
                "bounds": [
                    display_bounds.west,
                    display_bounds.south,
                    display_bounds.east,
                    display_bounds.north,
                ],
            },
            "source": {
                "id": "earth-base",
                "attribution": None,
            },
        }


patch_service = VectorPatchService([])
configuration_store = ConfigurationStore(CONFIG_FILE)
import_state = ImportState()
app = FastAPI(title="Kody Vector Map Service", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_methods=["GET", "PUT", "POST"],
    allow_headers=["Content-Type", "If-Match"],
)


@app.get("/api/patch/{zoom}/{x}/{y}")
def get_patch(
    zoom: int,
    x: int,
    y: int,
    buffer_pixels: int = Query(default=0, ge=0, le=16),
) -> dict[str, object]:
    return filter_vector_features(
        patch_service.patch(zoom, x, y, buffer_pixels),
        configuration_store.configuration,
    )


@app.get("/api/kody/manifest")
async def get_manifest() -> dict[str, object]:
    return {
        "kodyApiVersion": "1",
        "serviceId": "vector-map",
        "displayName": "Vector map",
        "configuration": {
            "readPath": "/api/config",
            "writePath": "/api/config",
            "initialConfiguration": configuration_store.response(),
            "fields": [
                {"key": "showMajorRoads", "label": "Show major roads", "type": "boolean"},
                {"key": "showUrbanAreas", "label": "Show urban areas", "type": "boolean"},
                {"key": "showWoodland", "label": "Show woodland", "type": "boolean"},
                {"key": "showPlaceNames", "label": "Show placenames", "type": "boolean"},
                {"key": "sourceFilename", "label": "Vector filename in imports", "type": "string", "required": False},
            ],
            "actions": [
                {
                    "label": "Import all Vector sources",
                    "path": "/api/actions/import-all",
                    "statusPath": "/api/actions/import-all",
                    "method": "POST",
                },
                {
                    "label": "Import selected Vector file",
                    "path": "/api/actions/import-geofabrik",
                    "statusPath": "/api/actions/import-geofabrik",
                    "method": "POST",
                },
                {
                    "label": "Rebuild vector pyramid",
                    "path": "/api/actions/rebuild-pyramid",
                    "statusPath": "/api/actions/rebuild-pyramid",
                    "method": "POST",
                },
            ],
        },
    }


@app.get("/api/config")
async def get_configuration() -> dict[str, object]:
    return configuration_store.response()


@app.put("/api/config")
async def update_configuration(
    configuration: VectorConfiguration, if_match: str | None = Header(default=None)
) -> dict[str, object]:
    return configuration_store.update(configuration, if_match)


def run_vector_action(action: str) -> None:
    try:
        if action == "import-selected":
            result = import_geofabrik_file()
        elif action == "import-all":
            result = import_all_vector_sources()
        elif action == "rebuild-pyramid":
            import_state.update("Building intermediate local vector LOD tiles.", 10)
            package_count, tile_count = rebuild_all_lod_pyramids()
            result = (
                f"Rebuilt {tile_count} LOD tile(s) across {package_count} "
                "native Vector tile set(s)."
            )
        else:
            raise ValueError(f"unsupported Vector action: {action}")
        import_state.finish(result)
    except Exception as error:
        import_state.fail(error)


def start_vector_action(action: str, message: str) -> dict[str, str]:
    import_state.start()
    try:
        subprocess.Popen(
            [sys.executable, "-m", "kody_vecmap.vector_worker", action],
            cwd=ROOT_DIRECTORY,
            env={**os.environ, "KODY_VECTOR_WORKER": "1"},
        )
    except Exception as error:
        import_state.fail(error)
        raise HTTPException(status_code=500, detail=f"unable to start Vector worker: {error}") from error
    return {"message": message}


@app.post("/api/actions/import-geofabrik", status_code=202)
async def start_geofabrik_import() -> dict[str, str]:
    return start_vector_action("import-selected", "Vector import started.")


@app.get("/api/actions/import-geofabrik")
async def get_geofabrik_import_status() -> dict[str, str | int]:
    return import_state.response()


@app.post("/api/actions/import-all", status_code=202)
async def start_all_vector_imports() -> dict[str, str]:
    return start_vector_action("import-all", "Importing all Vector sources.")


@app.get("/api/actions/import-all")
async def get_all_vector_import_status() -> dict[str, str | int]:
    return import_state.response()


@app.post("/api/actions/rebuild-pyramid", status_code=202)
async def rebuild_pyramid() -> dict[str, str]:
    return start_vector_action(
        "rebuild-pyramid", "Rebuilding local vector LOD pyramid."
    )


@app.get("/api/actions/rebuild-pyramid")
async def get_rebuild_pyramid_status() -> dict[str, str | int]:
    return import_state.response()
