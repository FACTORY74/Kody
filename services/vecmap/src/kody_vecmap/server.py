"""Vector patch API for the Kody map."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
from itertools import chain
import json
import logging
import os
from queue import Queue
import re
import shutil
import subprocess
import sys
from threading import Thread
import zipfile
from pathlib import Path
from threading import Lock, RLock
from time import sleep
from typing import Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import shapefile
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from shapely.geometry import mapping, shape

from .vector_theme import load_vector_theme
from .native_tiles import NativeTileStore
from .math import MapMath
from .raster_tiles import (
    MAXIMUM_ZOOM as RASTER_MAXIMUM_ZOOM,
    NaturalEarthRasterCache,
    cache_revision,
    render_tile,
)

ROOT_DIRECTORY = Path(__file__).resolve().parents[2]
PROJECT_DIRECTORY = ROOT_DIRECTORY.parent.parent
DATA_ROOT = Path(os.environ.get("KODY_DATA_DIRECTORY", ROOT_DIRECTORY / "data"))
MAPS_DIRECTORY = DATA_ROOT / "vecmap" / "maps"
IMPORTS_DIRECTORY = DATA_ROOT / "vecmap" / "imports"
DEFAULT_NATURAL_EARTH_DIRECTORY = PROJECT_DIRECTORY / "temp_import" / "NaturalEarth"
DEFAULT_NATURAL_EARTH_PHYSICAL_URL = (
    "https://www.factory74.com/permenant/kody/NaturalEarth/10m_physical.zip"
)
DEFAULT_NATURAL_EARTH_CULTURAL_URL = (
    "https://www.factory74.com/permenant/kody/NaturalEarth/10m_cultural.zip"
)
CONFIG_FILE = ROOT_DIRECTORY / "config" / "vecmap.json"
ACTION_STATE_FILE = MAPS_DIRECTORY / "action-state.json"
REMOTE_ARCHIVE_MAXIMUM_BYTES = 512 * 1024 * 1024
REMOTE_ARCHIVE_CHUNK_SIZE = 1024 * 1024
NATURAL_EARTH_MAXIMUM_DETAIL_ZOOM = 4
NATURAL_EARTH_RASTER_DETAIL_ZOOM = 8
DEFAULT_NATURAL_EARTH_TILE_ZOOMS = "0, 2, 4, 6, 8, 10, 12"
NATURAL_EARTH_CATEGORIES = {
    "physical": "Physical land and ocean",
    "coastline": "Coastlines and islands",
    "hydrography": "Lakes, rivers, and playas",
    "marine": "Marine regions and reefs",
    "bathymetry": "Bathymetry",
    "ice": "Ice and glaciated areas",
    "geography": "Geographic regions and labels",
    "graticules": "Graticules and reference lines",
    "administrative": "Country and administrative boundaries",
    "places": "Cities, towns, and place labels",
    "transport": "Roads, railways, airports, and ports",
    "protected": "Parks and protected areas",
    "timezones": "Time zones",
    "other": "Other Natural Earth data",
}
TILE_RENDER_RETRY_MILLISECONDS = 250
logger = logging.getLogger(__name__)


def initialize_data_directory() -> None:
    IMPORTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    MAPS_DIRECTORY.mkdir(parents=True, exist_ok=True)


initialize_data_directory()


def natural_earth_tile_zooms(value: str) -> tuple[int, ...]:
    try:
        zooms = tuple(
            sorted({int(item.strip()) for item in value.split(",") if item.strip()})
        )
    except ValueError as error:
        raise ValueError("Natural Earth tile zooms must be comma-separated integers.") from error
    if not zooms:
        raise ValueError("Natural Earth tile zooms must include at least one zoom.")
    if zooms[0] < 0 or zooms[-1] > RASTER_MAXIMUM_ZOOM:
        raise ValueError(
            f"Natural Earth tile zooms must be between 0 and {RASTER_MAXIMUM_ZOOM}."
        )
    return zooms


class VectorConfiguration(BaseModel):
    natural_earth_directory: str = Field(
        default=str(DEFAULT_NATURAL_EARTH_DIRECTORY), alias="naturalEarthDirectory"
    )
    natural_earth_physical_url: str = Field(
        default=DEFAULT_NATURAL_EARTH_PHYSICAL_URL,
        alias="naturalEarthPhysicalUrl",
    )
    natural_earth_cultural_url: str = Field(
        default=DEFAULT_NATURAL_EARTH_CULTURAL_URL,
        alias="naturalEarthCulturalUrl",
    )
    natural_earth_tile_zooms: str = Field(
        default=DEFAULT_NATURAL_EARTH_TILE_ZOOMS,
        alias="naturalEarthTileZooms",
    )
    show_natural_earth_physical: bool = Field(default=True, alias="showNaturalEarthPhysical")
    show_natural_earth_coastline: bool = Field(default=False, alias="showNaturalEarthCoastline")
    show_natural_earth_hydrography: bool = Field(default=True, alias="showNaturalEarthHydrography")
    show_natural_earth_marine: bool = Field(default=False, alias="showNaturalEarthMarine")
    show_natural_earth_bathymetry: bool = Field(default=False, alias="showNaturalEarthBathymetry")
    show_natural_earth_ice: bool = Field(default=True, alias="showNaturalEarthIce")
    show_natural_earth_geography: bool = Field(default=False, alias="showNaturalEarthGeography")
    show_natural_earth_graticules: bool = Field(default=False, alias="showNaturalEarthGraticules")
    show_natural_earth_administrative: bool = Field(default=True, alias="showNaturalEarthAdministrative")
    show_natural_earth_places: bool = Field(default=True, alias="showNaturalEarthPlaces")
    show_natural_earth_transport: bool = Field(default=False, alias="showNaturalEarthTransport")
    show_natural_earth_protected: bool = Field(default=True, alias="showNaturalEarthProtected")
    show_natural_earth_timezones: bool = Field(default=False, alias="showNaturalEarthTimezones")
    show_natural_earth_other: bool = Field(default=False, alias="showNaturalEarthOther")

    @field_validator("natural_earth_tile_zooms")
    @classmethod
    def validate_natural_earth_tile_zooms(cls, value: str) -> str:
        return ", ".join(str(zoom) for zoom in natural_earth_tile_zooms(value))


@dataclass(frozen=True)
class RasterTileJob:
    key: str
    render: Callable[[], None]


class RasterTileRenderQueue:
    """Serializes cold-tile rendering and reports the active batch."""

    def __init__(self) -> None:
        self._jobs: Queue[RasterTileJob] = Queue()
        self._lock = RLock()
        self._pending: set[str] = set()
        self._active: str | None = None
        self._total = 0
        self._completed = 0
        self._failed = 0
        self._last_error = ""
        Thread(target=self._run, name="natural-earth-tile-renderer", daemon=True).start()

    def enqueue(self, key: str, render: Callable[[], None]) -> bool:
        with self._lock:
            if key in self._pending:
                return False
            if not self._pending:
                self._total = 0
                self._completed = 0
                self._failed = 0
                self._last_error = ""
            self._pending.add(key)
            self._total += 1
            self._jobs.put(RasterTileJob(key, render))
            return True

    def response(self) -> dict[str, str | int]:
        with self._lock:
            queued = self._jobs.qsize()
            rendering = 1 if self._active is not None else 0
            processed = self._completed + self._failed
            active = bool(self._pending)
            if active:
                message = (
                    f"Rendering {rendering} Natural Earth tile"
                    f"{'' if rendering == 1 else 's'}; {queued} queued."
                )
            elif self._failed:
                message = f"{self._failed} Natural Earth tile render(s) failed: {self._last_error}"
            else:
                message = ""
            return {
                "status": "processing" if active else "idle",
                "message": message,
                "progress": round(processed / self._total * 100) if self._total else 0,
                "processed": processed,
                "total": self._total,
                "queued": queued,
                "retained": queued,
            }

    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            with self._lock:
                self._active = job.key
            try:
                job.render()
            except Exception as error:
                logger.exception("Natural Earth tile rendering failed for %s", job.key)
                with self._lock:
                    self._failed += 1
                    self._last_error = str(error)
            else:
                with self._lock:
                    self._completed += 1
            finally:
                with self._lock:
                    self._pending.discard(job.key)
                    self._active = None
                self._jobs.task_done()


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
                            "status":    "failed",
                            "message":   "Natural Earth import was cancelled because the service restarted.",
                            "progress":  0,
                            "processed": 0,
                            "total":     0,
                            "retained":  0,
                        }
                    )

    @staticmethod
    def _idle_response() -> dict[str, str | int]:
        return {
            "status":    "idle",
            "message":   "",
            "progress":  0,
            "processed": 0,
            "total":     0,
            "retained":  0,
        }

    def _read(self) -> dict[str, str | int]:
        if not self.path.is_file():
            return self._idle_response()
        response = json.loads(self.path.read_text(encoding="utf-8"))
        return {
            "status":    str(response["status"]),
            "message":   str(response["message"]),
            "progress":  int(response["progress"]),
            "processed": int(response.get("processed", 0)),
            "total":     int(response.get("total", 0)),
            "retained":  int(response.get("retained", 0)),
        }

    def _write(self, response: dict[str, str | int]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(f".{os.getpid()}.tmp")
        temporary_path.write_text(
            json.dumps(response, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        for attempt in range(5):
            try:
                temporary_path.replace(self.path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                sleep(0.05 * (attempt + 1))

    def start(self) -> None:
        with self.lock:
            if self._read()["status"] == "running":
                raise HTTPException(status_code=409, detail="a Natural Earth import is already running")
            self._write(
                {
                    "status":    "running",
                    "message":   "Preparing Natural Earth import.",
                    "progress":  0,
                    "processed": 0,
                    "total":     0,
                    "retained":  0,
                }
            )

    def update(self, message: str, progress: int) -> None:
        with self.lock:
            previous = self._read()
            self._write(
                {
                    "status":    "running",
                    "message":   message,
                    "progress":  progress,
                    "processed": previous["processed"],
                    "total":     previous["total"],
                    "retained":  previous["retained"],
                }
            )

    def finish(self, message: str) -> None:
        with self.lock:
            previous = self._read()
            self._write(
                {
                    "status":    "succeeded",
                    "message":   message,
                    "progress":  100,
                    "processed": previous["processed"],
                    "total":     previous["total"],
                    "retained":  previous["retained"],
                }
            )

    def fail(self, error: Exception) -> None:
        with self.lock:
            previous = self._read()
            self._write(
                {
                    "status":    "failed",
                    "message":   str(error),
                    "progress":  0,
                    "processed": previous["processed"],
                    "total":     previous["total"],
                    "retained":  previous["retained"],
                }
            )

    def response(self) -> dict[str, str | int]:
        with self.lock:
            return self._read()


def natural_earth_source_layers(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    members = {
        Path(info.filename).as_posix().casefold(): info.filename
        for info in archive.infolist()
        if not info.is_dir()
    }
    layers = []
    for folded_name, filename in members.items():
        if not folded_name.endswith(".shp"):
            continue
        layer_stem = filename[:-4]
        layer_name = Path(filename).stem
        if not layer_name.casefold().startswith("ne_10m_"):
            continue
        if all(
            f"{folded_name[:-4]}.{extension}" in members
            for extension in ("shx", "dbf")
        ):
            layers.append((layer_stem, layer_name))
    return sorted(layers, key=lambda layer: layer[1].casefold())


def is_natural_earth_archive(source: Path) -> bool:
    if source.suffix.lower() != ".zip":
        return False
    try:
        with zipfile.ZipFile(source) as archive:
            return bool(natural_earth_source_layers(archive))
    except (OSError, zipfile.BadZipFile):
        return False


def natural_earth_archives(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        (
            source.resolve()
            for source in directory.iterdir()
            if source.is_file() and is_natural_earth_archive(source)
        ),
        key=lambda source: source.name.casefold(),
    )


def configured_natural_earth_directory() -> Path:
    configured = Path(
        configuration_store.configuration.natural_earth_directory
    ).expanduser()
    if configured.is_absolute():
        return configured.resolve()
    return (IMPORTS_DIRECTORY / configured).resolve()


def download_natural_earth_archive(
    source_url: str,
    progress: Callable[[str, int], None],
    progress_start: int,
    progress_end: int,
) -> Path:
    parsed_url = urlparse(source_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise ValueError("Natural Earth archive URLs must use HTTPS and include a host.")
    filename = Path(parsed_url.path).name
    if not filename.lower().endswith(".zip"):
        raise ValueError("Natural Earth archive URLs must end in .zip.")
    archive_directory = IMPORTS_DIRECTORY / "natural-earth"
    archive_directory.mkdir(parents=True, exist_ok=True)
    destination = archive_directory / (
        f"{hashlib.sha256(source_url.encode('utf-8')).hexdigest()[:12]}-{filename}"
    )
    temporary = destination.with_name(
        f"{destination.stem}.download{destination.suffix}"
    )
    request = Request(source_url, headers={"User-Agent": "Kody Natural Earth importer"})
    try:
        with urlopen(request, timeout=30) as response:
            final_url = urlparse(response.geturl())
            if final_url.scheme != "https" or not final_url.netloc:
                raise ValueError("Natural Earth archive redirected to an invalid location.")
            content_length = response.headers.get("Content-Length")
            if content_length is not None and int(content_length) > REMOTE_ARCHIVE_MAXIMUM_BYTES:
                raise ValueError("Natural Earth archive exceeds the 512 MB download limit.")
            received = 0
            with temporary.open("wb") as stream:
                while chunk := response.read(REMOTE_ARCHIVE_CHUNK_SIZE):
                    received += len(chunk)
                    if received > REMOTE_ARCHIVE_MAXIMUM_BYTES:
                        raise ValueError("Natural Earth archive exceeds the 512 MB download limit.")
                    stream.write(chunk)
                    if content_length is not None:
                        progress(
                            f"Downloading {filename}: {received:,} of {int(content_length):,} bytes.",
                            progress_start
                            + round(
                                received / int(content_length)
                                * (progress_end - progress_start)
                            ),
                        )
        if not is_natural_earth_archive(temporary):
            raise ValueError(
                f"Downloaded {filename} is not a Natural Earth 1:10m archive."
            )
        temporary.replace(destination)
    except HTTPError as error:
        temporary.unlink(missing_ok=True)
        raise ValueError(
            f"Unable to download Natural Earth archive {filename}: HTTP {error.code}."
        ) from error
    except (OSError, URLError, ValueError) as error:
        temporary.unlink(missing_ok=True)
        if isinstance(error, ValueError):
            raise
        raise ValueError(
            f"Unable to download Natural Earth archive {filename}: {error}."
        ) from error
    return destination


def configured_natural_earth_archives() -> list[Path]:
    configuration = configuration_store.configuration
    urls = (
        configuration.natural_earth_physical_url.strip(),
        configuration.natural_earth_cultural_url.strip(),
    )
    if any(urls):
        if not all(urls):
            raise ValueError(
                "Both Natural Earth physical and cultural archive URLs must be configured."
            )
        return [
            download_natural_earth_archive(
                source_url,
                import_state.update,
                index * 20,
                (index + 1) * 20,
            )
            for index, source_url in enumerate(urls)
        ]
    return natural_earth_archives(configured_natural_earth_directory())


def natural_earth_category(source_layer: str) -> tuple[str, int]:
    name = source_layer.casefold()
    if "bathymetry" in name:
        return "bathymetry", 0
    if "graticule" in name or "bounding_box" in name:
        return "graticules", 6
    if "scale_rank" in name:
        return "geography", 6
    if "urban_areas" in name:
        return "places", 6
    if "admin_" in name and "label_points" in name:
        return "administrative", 3
    if (
        "populated_places" in name
        or "label_points" in name
        or "land_ocean_label" in name
    ):
        return "places", 4
    if any(
        token in name
        for token in (
            "admin_",
            "antarctic_claim",
            "boundary",
            "countries",
            "disputed",
            "map_subunits",
            "map_units",
            "sovereignty",
            "pacific_groupings",
        )
    ):
        return "administrative", 3
    if "land_ocean_seams" in name:
        return "coastline", 2
    if "coastline" in name or (
        "minor_islands" in name and "admin_" not in name
    ):
        return "coastline", 2
    if "river" in name or "lake" in name or "playa" in name:
        return "hydrography", 4
    if "reef" in name or "marine" in name:
        return "marine", 6
    if "ice" in name or "glaciated" in name:
        return "ice", 5
    if "parks_and_protected_lands" in name:
        return "protected", 7
    if "time_zone" in name:
        return "timezones", 5
    if any(token in name for token in ("airports", "roads", "railroads", "ports")):
        return "transport", 5
    if "geography" in name or "geographic" in name:
        return "geography", 7
    if "land" in name or "ocean" in name:
        return "physical", 0
    return "other", 6


def natural_earth_display_layer(source_layer: str, category: str) -> str:
    name = source_layer.casefold()
    if "bathymetry" in name:
        match = re.search(r"_bathymetry_[a-z]_(\d+)$", name)
        if match:
            return f"ne-bathymetry-{match.group(1)}"
        return "ne-bathymetry"
    if "graticule" in name or "bounding_box" in name:
        return "ne-graticule"
    if "urban_areas" in name:
        return "ne-urban"
    if category == "administrative":
        if "admin_0" in name:
            return "ne-admin0"
        if "admin_1" in name:
            return "ne-admin1"
        if "admin_2" in name:
            return "ne-admin2"
        return "ne-admin"
    if "label_points" in name or "populated_places" in name or "land_ocean_label" in name:
        return "ne-place"
    if "airports" in name or "ports" in name:
        return "ne-transport-point"
    if "roads" in name:
        return "ne-road"
    if "railroads" in name:
        return "ne-rail"
    if "time_zone" in name:
        return "ne-timezone"
    if "parks_and_protected_lands" in name:
        return "ne-protected"
    if "disputed" in name or "antarctic_claim" in name:
        return "ne-disputed"
    if "coastline" in name or ("seams" in name and category == "coastline"):
        return "ne-coastline"
    if "river" in name:
        return "ne-river"
    if "lake" in name:
        return "ne-lake"
    if "playa" in name:
        return "ne-playa"
    if "reef" in name:
        return "ne-reef"
    if "marine" in name:
        return "ne-marine"
    if "ice" in name or "glaciated" in name:
        return "ne-ice"
    if "geography_regions_points" in name or "geography_regions_elevation_points" in name:
        return "ne-geography-point"
    if "geography" in name or "geographic" in name:
        return "ne-geography"
    if "ocean" in name:
        return "ne-ocean"
    if "land" in name or "minor_islands" in name:
        return "ne-land"
    return f"ne-{category}"


def natural_earth_minimum_zoom(
    category: str, default_zoom: int, attributes: dict[str, object]
) -> int:
    normalized_attributes = {
        str(key).casefold(): value for key, value in attributes.items()
    }
    for key in ("min_zoom", "minzoom"):
        value = normalized_attributes.get(key)
        if value is not None:
            try:
                return max(default_zoom, min(18, int(float(value) + 0.999)))
            except (TypeError, ValueError, OverflowError):
                pass
    if category != "places":
        return default_zoom
    for key in ("scalerank", "labelrank"):
        value = normalized_attributes.get(key)
        if value is not None:
            try:
                rank = max(0, int(float(value)))
            except (TypeError, ValueError, OverflowError):
                continue
            return min(16, default_zoom + rank)
    return default_zoom


def natural_earth_features(
    source: Path, progress: Callable[[str, int], None] | None = None
) -> Iterator[dict[str, object]]:
    with zipfile.ZipFile(source) as archive:
        source_layers = natural_earth_source_layers(archive)
        if not source_layers:
            raise ValueError(f"No Natural Earth 10m shapefiles found in {source.name}.")
        for layer_index, (layer_stem, source_layer) in enumerate(source_layers):
            category, minimum_zoom = natural_earth_category(source_layer)
            display_layer = natural_earth_display_layer(source_layer, category)
            if progress:
                progress(
                    f"Reading {source_layer}.",
                    min(80, 15 + round((layer_index / len(source_layers)) * 65)),
                )
            with ExitStack() as streams:
                shapefile_streams = [
                    streams.enter_context(archive.open(f"{layer_stem}.{extension}"))
                    for extension in ("shp", "shx", "dbf")
                ]
                reader = shapefile.Reader(
                    shp=shapefile_streams[0],
                    shx=shapefile_streams[1],
                    dbf=shapefile_streams[2],
                )
                for record in reader.iterShapeRecords():
                    if record.shape.shapeType == shapefile.NULL:
                        continue
                    geometry = shape(record.shape.__geo_interface__)
                    if geometry.is_empty:
                        continue
                    attributes = record.record.as_dict()
                    normalized_attributes = {
                        str(key).casefold(): value
                        for key, value in attributes.items()
                    }
                    minimum_feature_zoom = natural_earth_minimum_zoom(
                        category, minimum_zoom, attributes
                    )
                    yield {
                        "type": "Feature",
                        "properties": {
                            "layer": display_layer,
                            "category": category,
                            "name": normalized_attributes.get("name")
                            or normalized_attributes.get("name_en"),
                            "minZoom": minimum_feature_zoom,
                            "sourceLayer": source_layer,
                        },
                        "geometry": mapping(geometry),
                    }


def import_natural_earth_dataset() -> str:
    sources = configured_natural_earth_archives()
    if not sources:
        raise ValueError(
            f"No Natural Earth 10m ZIP archives were found in "
            f"{configured_natural_earth_directory()}."
        )

    import_state.update(
        f"Importing {len(sources)} Natural Earth archive(s).", 5
    )
    features = chain.from_iterable(
        natural_earth_features(
            source,
            lambda message, progress: import_state.update(
                f"{source.name}: {message}",
                min(95, round((index + progress / 100) / len(sources) * 100)),
            ),
        )
        for index, source in enumerate(sources)
    )
    count = NativeTileStore(MAPS_DIRECTORY).replace_source_prefix(
        "natural-earth", "natural-earth", features
    )
    store = NativeTileStore(MAPS_DIRECTORY)
    store.build_base_overview(force=True)
    import_state.update("Finished writing the Natural Earth library.", 99)
    return (
        f"Imported {count:,} Natural Earth features from "
        f"{len(sources)} configured archive(s). Map tiles will render and cache on demand."
    )


def filter_vector_features(
    collection: dict[str, object], configuration: VectorConfiguration
) -> dict[str, object]:
    features = collection.get("features", [])
    if not isinstance(features, list):
        return collection
    return {
        **collection,
        "features": [
            feature
            for feature in features
            if not isinstance(feature, dict)
            or not isinstance(feature.get("properties"), dict)
            or (
                "admin_" not in str(
                    feature["properties"].get("sourceLayer", "")
                ).casefold()
                or "label_points" not in str(
                    feature["properties"].get("sourceLayer", "")
                ).casefold()
            )
            and getattr(
                configuration,
                "show_natural_earth_"
                + str(feature["properties"].get("category", "")),
                True,
            )
        ],
    }


class VectorPatchService:
    """Serves directly addressed Natural Earth display tiles."""

    def __init__(self, maps_directory: Path = MAPS_DIRECTORY) -> None:
        self.tile_store = NativeTileStore(maps_directory)
        self.tile_store.build_world_overview()

    def patch(self, zoom: int, x: int, y: int, buffer_pixels: int = 0) -> dict[str, object]:
        try:
            display_bounds = MapMath.tile_bounds(zoom, x, y)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        tile = self.tile_store.tile_collection(
            zoom,
            x,
            y,
            buffer_pixels,
            detail_zoom=min(zoom, NATURAL_EARTH_MAXIMUM_DETAIL_ZOOM),
        )
        collection, lod = tile or (
            {"type": "FeatureCollection", "features": []},
            zoom,
        )
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
                "id": "natural-earth",
                "attribution": "Natural Earth",
                "lod": lod,
            },
        }


patch_service = VectorPatchService()
configuration_store = ConfigurationStore(CONFIG_FILE)
import_state = ImportState()
tile_render_queue = RasterTileRenderQueue()
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
    collection = filter_vector_features(
        patch_service.patch(zoom, x, y, buffer_pixels),
        configuration_store.configuration,
    )
    return {
        **collection,
        "styles": {
            layer: style.model_dump(by_alias=True)
            for layer, style in load_vector_theme().styles.items()
        },
    }


@app.get("/api/tiles/{zoom}/{x}/{y}.webp", response_model=None)
def get_raster_tile(zoom: int, x: int, y: int) -> FileResponse | JSONResponse:
    configuration = configuration_store.configuration
    enabled_zooms = natural_earth_tile_zooms(configuration.natural_earth_tile_zooms)
    if not (zoom in enabled_zooms and 0 <= x < 2**zoom and 0 <= y < 2**zoom):
        raise HTTPException(status_code=422, detail="tile coordinates are outside the Natural Earth range")
    store = patch_service.tile_store
    styles = load_vector_theme().styles
    cache = NaturalEarthRasterCache(
        MAPS_DIRECTORY,
        cache_revision(store, configuration.model_dump(by_alias=True), styles),
    )

    def render() -> bytes:
        tile = store.tile_collection(
            zoom,
            x,
            y,
            buffer_pixels=2,
            detail_zoom=min(zoom, NATURAL_EARTH_RASTER_DETAIL_ZOOM),
        )
        collection = tile[0] if tile else {"type": "FeatureCollection", "features": []}
        return render_tile(filter_vector_features(collection, configuration), styles, zoom, x, y)

    path = cache.tile_path(zoom, x, y)
    if not path.is_file():
        queued = tile_render_queue.enqueue(
            str(path),
            lambda: cache.get_or_render(zoom, x, y, render),
        )
        return JSONResponse(
            status_code=202,
            content={
                "status": "queued" if queued else "rendering",
                "retryAfterMilliseconds": TILE_RENDER_RETRY_MILLISECONDS,
            },
            headers={
                "Cache-Control": "no-store",
                "Retry-After": "1",
            },
        )
    return FileResponse(
        path,
        media_type="image/webp",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/api/tiles/revision")
def get_raster_tile_revision() -> dict[str, object]:
    store = patch_service.tile_store
    return {
        "revision": cache_revision(
            store,
            configuration_store.configuration.model_dump(by_alias=True),
            load_vector_theme().styles,
        ),
        "tileZooms": list(
            natural_earth_tile_zooms(
                configuration_store.configuration.natural_earth_tile_zooms
            )
        ),
    }


@app.get("/api/tiles/status")
def get_raster_tile_status() -> dict[str, str | int]:
    return tile_render_queue.response()


@app.get("/api/kody/manifest")
async def get_manifest() -> dict[str, object]:
    return {
        "kodyApiVersion": "1",
        "serviceId": "vector-map",
        "displayName": "Vector map",
        "configuration": {
            "readPath": "/api/config",
            "writePath": "/api/config",
            "activityStatusPath": "/api/tiles/status",
            "initialConfiguration": configuration_store.response(),
            "fields": [
                {
                    "key": "naturalEarthDirectory",
                    "label": "Natural Earth archive folder (local fallback)",
                    "type": "string",
                    "required": False,
                },
                {
                    "key": "naturalEarthPhysicalUrl",
                    "label": "Natural Earth physical archive URL",
                    "type": "string",
                    "required": False,
                },
                {
                    "key": "naturalEarthCulturalUrl",
                    "label": "Natural Earth cultural archive URL",
                    "type": "string",
                    "required": False,
                },
                {
                    "key": "naturalEarthTileZooms",
                    "label": "Natural Earth cached tile zooms",
                    "type": "string",
                    "required": True,
                },
            ]
            + [
                {
                    "key": f"showNaturalEarth{category.title()}",
                    "label": f"Natural Earth: {label.lower()}",
                    "type": "boolean",
                }
                for category, label in NATURAL_EARTH_CATEGORIES.items()
            ],
            "actions": [
                {
                    "label": "Import Natural Earth dataset",
                    "path": "/api/actions/import-natural-earth",
                    "statusPath": "/api/actions/import-natural-earth",
                    "method": "POST",
                },
                {
                    "label": "Clear local data",
                    "path": "/api/actions/clear-local-data",
                    "method": "POST",
                    "confirmationMessage": "Clear all local Natural Earth map data? This cannot be undone.",
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


def run_natural_earth_action(action: str) -> None:
    try:
        if action == "import-natural-earth":
            result = import_natural_earth_dataset()
        else:
            raise ValueError(f"unsupported Natural Earth action: {action}")
        import_state.finish(result)
    except Exception as error:
        import_state.fail(error)


def start_natural_earth_action(action: str, message: str) -> dict[str, str]:
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


@app.post("/api/actions/import-natural-earth", status_code=202)
async def start_natural_earth_import() -> dict[str, str]:
    return start_natural_earth_action(
        "import-natural-earth", "Natural Earth import started."
    )


@app.get("/api/actions/import-natural-earth")
async def get_natural_earth_import_status() -> dict[str, str | int]:
    return import_state.response()


@app.post("/api/actions/clear-local-data")
async def clear_local_data() -> dict[str, str]:
    if import_state.response()["status"] == "running":
        raise HTTPException(status_code=409, detail="a Natural Earth import is already running")
    if tile_render_queue.response()["status"] == "processing":
        raise HTTPException(status_code=409, detail="Natural Earth tile rendering is still running")
    shutil.rmtree(MAPS_DIRECTORY, ignore_errors=True)
    shutil.rmtree(IMPORTS_DIRECTORY, ignore_errors=True)
    initialize_data_directory()
    return {"message": "Cleared local Vector map data."}
