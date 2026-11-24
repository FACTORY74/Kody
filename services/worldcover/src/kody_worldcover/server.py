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

"""Sparse local Web Mercator tile cache for ESA WorldCover 2021."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import hashlib
from io import BytesIO
from math import atan, floor, log, pi
import os
from pathlib import Path
import shutil
import sqlite3
from threading import BoundedSemaphore, Lock, RLock
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image
from pydantic import BaseModel, Field, field_validator
import numpy
import rasterio
from maptiles import WebPQuadTreeStore, standard_tile_response
from rasterio.enums import Resampling
from rasterio.errors import RasterioIOError
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT

ROOT_DIRECTORY = Path(__file__).resolve().parents[2]
REPOSITORY_DIRECTORY = Path(__file__).resolve().parents[4]
DATA_ROOT = Path(os.environ.get("KODY_DATA_DIRECTORY", ROOT_DIRECTORY / "data"))
WORLDCOVER_DIRECTORY = DATA_ROOT / "worldcover"
WORLDCOVER_TILE_DIRECTORY = WORLDCOVER_DIRECTORY / "tiles"
WORLDCOVER_NATIVE_DATABASE = WORLDCOVER_DIRECTORY / "native-sources.sqlite"
CONFIG_FILE = REPOSITORY_DIRECTORY / "config" / "worldcover.json"
CONFIGURATION_SERVICE_URL = os.environ.get(
    "KODY_CONFIGURATION_SERVICE_URL", "http://127.0.0.1:8001"
).rstrip("/")
SOURCE_URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map"
TILE_SIZE = 256
MAX_ACTIVE_TILE_RENDERERS = 2
MAX_QUEUED_TILE_REQUESTS = 32
MAX_NATIVE_ZOOM = 14
MAXIMUM_UNCONFIRMED_DOWNLOAD_TILES = 64
MAX_LATITUDE = 85.05112878
WEB_MERCATOR_HALF_WORLD = 20037508.342789244
PALETTE = {
    10: (0, 100, 0), 20: (255, 187, 34), 30: (255, 255, 76),
    40: (240, 150, 255), 50: (250, 0, 0), 60: (180, 180, 180),
    70: (0, 100, 200), 80: (0, 150, 160), 90: (0, 207, 117),
    95: (250, 230, 160), 100: (0, 0, 0),
}
ATTRIBUTION = "ESA WorldCover 2021"
LOCAL_TILE_FORMAT = "WEBP"
LOCAL_TILE_SAVE_OPTIONS = {"lossless": True, "method": 6}
TILE_CACHE_NAMESPACE = "worldcover-exact-v2"
TILE_FORMAT_VERSION = "4"


class Viewport(BaseModel):
    west: float = Field(ge=-180, le=180)
    south: float = Field(ge=-MAX_LATITUDE, le=MAX_LATITUDE)
    east: float = Field(ge=-180, le=180)
    north: float = Field(ge=-MAX_LATITUDE, le=MAX_LATITUDE)
    zoom: float = Field(ge=0, le=18)


class DownloadState:
    def __init__(self) -> None:
        self.lock = Lock()
        self.status: Literal["idle", "processing", "succeeded", "failed"] = "idle"
        self.message = ""

    def start(self, message: str) -> None:
        with self.lock:
            if self.status == "processing":
                raise HTTPException(status_code=409, detail="ESA WorldCover download is already running")
            self.status, self.message = "processing", message

    def update(self, message: str) -> None:
        with self.lock:
            if self.status == "processing":
                self.message = message

    def complete(self, message: str) -> None:
        with self.lock:
            self.status, self.message = "succeeded", message

    def fail(self, error: Exception) -> None:
        with self.lock:
            self.status, self.message = "failed", str(error)

    def response(self) -> dict[str, str]:
        with self.lock:
            return {"status": self.status, "message": self.message}

    def assert_idle(self) -> None:
        with self.lock:
            if self.status == "processing":
                raise HTTPException(status_code=409, detail="ESA WorldCover download is already running")


class WorldCoverConfiguration(BaseModel):
    generation_zoom_levels: str = Field(default="6, 8, 10", alias="generationZoomLevels", max_length=128)

    @field_validator("generation_zoom_levels")
    @classmethod
    def validate_generation_zoom_levels(cls, value: str) -> str:
        parse_generation_zoom_levels(value)
        return value


def parse_generation_zoom_levels(value: str) -> tuple[int, ...]:
    try:
        levels = tuple(sorted({int(level.strip()) for level in value.split(",") if level.strip()}))
    except ValueError as error:
        raise ValueError("generation zoom levels must be comma-separated whole numbers") from error
    if not levels:
        raise ValueError("at least one generation zoom level is required")
    if any(level < 0 or level > MAX_NATIVE_ZOOM for level in levels):
        raise ValueError(f"generation zoom levels must be between 0 and {MAX_NATIVE_ZOOM}")
    return levels


class ConfigurationStore:
    """Persist WorldCover's operator-selected tile generation levels."""

    def __init__(self) -> None:
        self.lock = RLock()
        self.configuration = self._load()

    def _load(self) -> WorldCoverConfiguration:
        try:
            return WorldCoverConfiguration.model_validate_json(CONFIG_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return WorldCoverConfiguration()
        except ValueError as error:
            raise RuntimeError(f"WorldCover configuration is invalid: {CONFIG_FILE}") from error

    def revision(self) -> str:
        return hashlib.sha256(
            self.configuration.model_dump_json(by_alias=True).encode("utf-8")
        ).hexdigest()

    def response(self) -> dict[str, object]:
        with self.lock:
            response = self.configuration.model_dump(by_alias=True)
            response["revision"] = self.revision()
            return response

    def update(
        self,
        configuration: WorldCoverConfiguration,
        expected_revision: str | None,
    ) -> dict[str, object]:
        with self.lock:
            if expected_revision is not None and expected_revision != self.revision():
                raise HTTPException(status_code=409, detail="configuration revision is stale")
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(
                configuration.model_dump_json(indent=2, by_alias=True) + "\n",
                encoding="utf-8",
            )
            self.configuration = configuration
            return self.response()


class NativeSourceStore:
    """Persist unmodified ESA WorldCover GeoTIFF source files in SQLite."""

    def __init__(self, path: Path = WORLDCOVER_NATIVE_DATABASE) -> None:
        self.path = path
        self.lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS native_sources (
                    longitude INTEGER NOT NULL,
                    latitude INTEGER NOT NULL,
                    source_tiff BLOB NOT NULL,
                    downloaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (longitude, latitude)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS unavailable_sources (
                    longitude INTEGER NOT NULL,
                    latitude INTEGER NOT NULL,
                    checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (longitude, latitude)
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def get(self, coordinate: tuple[int, int]) -> bytes | None:
        return cached_native_source(str(self.path.resolve()), coordinate)

    def is_unavailable(self, coordinate: tuple[int, int]) -> bool:
        with self.lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    "SELECT 1 FROM unavailable_sources WHERE longitude = ? AND latitude = ?",
                    coordinate,
                ).fetchone()
            finally:
                connection.close()
        return row is not None

    def mark_unavailable(self, coordinate: tuple[int, int]) -> None:
        with self.lock:
            connection = self._connect()
            try:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO unavailable_sources (longitude, latitude)
                    VALUES (?, ?)
                    """,
                    coordinate,
                )
                connection.commit()
            finally:
                connection.close()

    def put(self, coordinate: tuple[int, int], source_tiff: bytes) -> None:
        if not source_tiff:
            raise ValueError("native WorldCover source file is empty")
        with self.lock:
            connection = self._connect()
            try:
                connection.execute(
                    """
                    INSERT INTO native_sources (longitude, latitude, source_tiff)
                    VALUES (?, ?, ?)
                    ON CONFLICT(longitude, latitude) DO UPDATE SET
                        source_tiff = excluded.source_tiff,
                        downloaded_at = CURRENT_TIMESTAMP
                    """,
                    (*coordinate, source_tiff),
                )
                connection.execute(
                    "DELETE FROM unavailable_sources WHERE longitude = ? AND latitude = ?",
                    coordinate,
                )
                connection.commit()
                cached_native_source.cache_clear()
            finally:
                connection.close()


class TileRenderStatus:
    def __init__(self) -> None:
        self.lock = Lock()
        self.queued = 0
        self.rendering = 0
        self.completed = 0
        self.failed = 0
        self.total = 0
        self.last_error = ""

    def enqueue(self) -> None:
        with self.lock:
            if not self.queued and not self.rendering:
                self.completed = 0
                self.failed = 0
                self.total = 0
                self.last_error = ""
            self.queued += 1
            self.total += 1

    def start(self) -> None:
        with self.lock:
            self.queued -= 1
            self.rendering += 1

    def finish(self, error: str | None = None) -> None:
        with self.lock:
            self.rendering -= 1
            if error is None:
                self.completed += 1
            else:
                self.failed += 1
                self.last_error = error

    def cancel(self) -> None:
        with self.lock:
            self.queued -= 1
            self.total -= 1

    def response(self) -> dict[str, str | int]:
        with self.lock:
            processed = self.completed + self.failed
            active = bool(self.queued or self.rendering)
            if active:
                message = (
                    f"Rendering {self.rendering} WorldCover tile"
                    f"{'' if self.rendering == 1 else 's'}; {self.queued} queued."
                )
            elif self.failed:
                message = (
                    f"{self.failed} WorldCover tile render(s) failed: "
                    f"{self.last_error}"
                )
            else:
                message = ""
            return {
                "status": "processing" if active else "idle",
                "message": message,
                "progress": round(processed / self.total * 100) if self.total else 0,
                "processed": processed,
                "total": self.total,
                "queued": self.queued,
                "retained": self.queued,
            }


@lru_cache(maxsize=2)
def cached_native_source(
    database_path: str,
    coordinate: tuple[int, int],
) -> bytes | None:
    connection = sqlite3.connect(database_path, timeout=30)
    try:
        connection.execute("PRAGMA busy_timeout=30000")
        row = connection.execute(
            "SELECT source_tiff FROM native_sources WHERE longitude = ? AND latitude = ?",
            coordinate,
        ).fetchone()
    finally:
        connection.close()
    return None if row is None else bytes(row[0])


@lru_cache(maxsize=4)
def cached_native_source_store(database_path: str) -> NativeSourceStore:
    return NativeSourceStore(Path(database_path))


def native_source_store(directory: Path | None = None) -> NativeSourceStore:
    path = (
        WORLDCOVER_NATIVE_DATABASE
        if directory is None
        else directory / WORLDCOVER_NATIVE_DATABASE.name
    )
    return cached_native_source_store(str(path.resolve()))


def tile_store(directory: Path | None = None) -> WebPQuadTreeStore:
    """Return the standardized SQLite/WebP storage owned by this service."""

    return WebPQuadTreeStore(
        WORLDCOVER_TILE_DIRECTORY if directory is None else directory,
        namespace=TILE_CACHE_NAMESPACE,
        format_version=TILE_FORMAT_VERSION,
    )


def download_native_source(coordinate: tuple[int, int]) -> bytes | None:
    """Download and validate one native GeoTIFF before committing it to SQLite."""

    try:
        with urlopen(source_url(coordinate), timeout=120) as response:
            source_tiff = response.read()
    except HTTPError as error:
        if error.code == 404:
            native_source_store().mark_unavailable(coordinate)
            return None
        raise HTTPException(
            status_code=502,
            detail=f"ESA WorldCover returned {error.code} for {source_filename(*coordinate)}",
        ) from error
    except URLError as error:
        raise HTTPException(
            status_code=502,
            detail=f"could not download {source_filename(*coordinate)}: {error.reason}",
        ) from error
    try:
        with MemoryFile(source_tiff) as memory_file, memory_file.open() as dataset:
            if dataset.driver != "GTiff" or dataset.count < 1:
                raise ValueError("source is not a usable GeoTIFF")
    except RasterioIOError as error:
        raise HTTPException(
            status_code=502,
            detail=f"ESA WorldCover returned an invalid GeoTIFF for {source_filename(*coordinate)}",
        ) from error
    native_source_store().put(coordinate, source_tiff)
    return source_tiff


def longitude_tile(longitude: float, zoom: int) -> int:
    return min(2**zoom - 1, max(0, floor((longitude + 180) / 360 * 2**zoom)))


def latitude_tile(latitude: float, zoom: int) -> int:
    radians = latitude * pi / 180
    return min(2**zoom - 1, max(0, floor((1 - log(__import__("math").tan(radians) + 1 / __import__("math").cos(radians)) / pi) / 2 * 2**zoom)))


def tile_bounds(zoom: int, x: int, y: int) -> tuple[float, float, float, float]:
    west = x / 2**zoom * 360 - 180
    east = (x + 1) / 2**zoom * 360 - 180
    north = atan(__import__("math").sinh(pi * (1 - 2 * y / 2**zoom))) * 180 / pi
    south = atan(__import__("math").sinh(pi * (1 - 2 * (y + 1) / 2**zoom))) * 180 / pi
    return west, south, east, north


def web_mercator_bounds(zoom: int, x: int, y: int) -> tuple[float, float, float, float]:
    span = 2 * WEB_MERCATOR_HALF_WORLD / 2**zoom
    west = -WEB_MERCATOR_HALF_WORLD + x * span
    north = WEB_MERCATOR_HALF_WORLD - y * span
    return west, north - span, west + span, north


def source_coordinate(longitude: float, latitude: float) -> tuple[int, int]:
    return floor(longitude / 3) * 3, floor(latitude / 3) * 3


def source_coordinates_for_bounds(
    west: float, south: float, east: float, north: float
) -> set[tuple[int, int]]:
    epsilon = 1e-10
    return {
        (longitude, latitude)
        for longitude in range(floor(west / 3) * 3, floor((east - epsilon) / 3) * 3 + 1, 3)
        for latitude in range(floor(south / 3) * 3, floor((north - epsilon) / 3) * 3 + 1, 3)
    }


def source_filename(longitude: int, latitude: int) -> str:
    latitude_hemisphere = "N" if latitude >= 0 else "S"
    longitude_hemisphere = "E" if longitude >= 0 else "W"
    return (
        f"ESA_WorldCover_10m_2021_v200_{latitude_hemisphere}{abs(latitude):02d}"
        f"{longitude_hemisphere}{abs(longitude):03d}_Map.tif"
    )


def source_url(coordinate: tuple[int, int]) -> str:
    longitude, latitude = coordinate
    return f"{SOURCE_URL}/{source_filename(longitude, latitude)}"


def viewport_tiles(viewport: Viewport, requested_zoom: int | None = None) -> tuple[int, list[tuple[int, int]]]:
    zoom = min(MAX_NATIVE_ZOOM, floor(viewport.zoom)) if requested_zoom is None else requested_zoom
    west, east = longitude_tile(viewport.west, zoom), longitude_tile(viewport.east, zoom)
    north, south = latitude_tile(viewport.north, zoom), latitude_tile(viewport.south, zoom)
    return zoom, [(x, y) for y in range(north, south + 1) for x in range(west, east + 1)]


def viewport_from_configuration_service() -> Viewport:
    try:
        with urlopen(f"{CONFIGURATION_SERVICE_URL}/api/context/viewport", timeout=5) as response:
            return Viewport.model_validate_json(response.read())
    except HTTPError as error:
        if error.code == 404:
            raise HTTPException(status_code=409, detail="the map viewport is unavailable; open the map and move it before downloading") from error
        raise HTTPException(status_code=502, detail=f"configuration service returned {error.code} while reading the map viewport") from error
    except URLError as error:
        raise HTTPException(status_code=502, detail=f"could not read the map viewport: {error.reason}") from error


def generation_zoom_levels(requested_zoom: int | None = None) -> tuple[int, ...]:
    if requested_zoom is not None:
        return (requested_zoom,)
    return parse_generation_zoom_levels(configuration_store.configuration.generation_zoom_levels)


def native_source_coordinates(viewport: Viewport) -> list[tuple[int, int]]:
    return sorted(
        source_coordinates_for_bounds(
            viewport.west, viewport.south, viewport.east, viewport.north
        )
    )


def invalidate_rendered_tiles_for_sources(
    source_coordinates: list[tuple[int, int]],
) -> int:
    ranges = []
    for zoom in generation_zoom_levels():
        for longitude, latitude in source_coordinates:
            ranges.append(
                (
                    zoom,
                    longitude_tile(longitude, zoom),
                    latitude_tile(min(latitude + 3, MAX_LATITUDE) - 1e-10, zoom),
                    longitude_tile(longitude + 3 - 1e-10, zoom),
                    latitude_tile(max(latitude, -MAX_LATITUDE) + 1e-10, zoom),
                )
            )
    return tile_store().delete_tiles_in_ranges(ranges)


def native_download_assessment(viewport: Viewport | None = None) -> dict[str, object]:
    viewport = viewport or viewport_from_configuration_service()
    sources = native_source_coordinates(viewport)
    store = native_source_store()
    missing_sources = [
        coordinate for coordinate in sources
        if store.get(coordinate) is None and not store.is_unavailable(coordinate)
    ]
    unavailable_sources = sum(store.is_unavailable(coordinate) for coordinate in sources)
    return {
        "sourceTileCount": len(sources),
        "missingSourceTileCount": len(missing_sources),
        "unavailableSourceTileCount": unavailable_sources,
        "requiresConfirmation": len(missing_sources) > MAXIMUM_UNCONFIRMED_DOWNLOAD_TILES,
        "message": (
            f"Download {len(missing_sources)} missing native ESA WorldCover GeoTIFF "
            f"source files for this viewport; {unavailable_sources} empty source areas "
            "are already recorded."
        ),
    }


def rgba_image(data: numpy.ma.MaskedArray) -> Image.Image:
    rgba = numpy.zeros((TILE_SIZE, TILE_SIZE, 4), dtype=numpy.uint8)
    for value, color in PALETTE.items():
        rgba[data == value, :3] = color
        rgba[data == value, 3] = 255
    rgba[numpy.ma.getmaskarray(data), 3] = 0
    return Image.fromarray(rgba, "RGBA")


def write_tile(zoom: int, x: int, y: int, image: Image.Image) -> None:
    output = BytesIO()
    image.save(output, format=LOCAL_TILE_FORMAT, **LOCAL_TILE_SAVE_OPTIONS)
    tile_store().put(zoom, x, y, output.getvalue())
    image.close()
    cached_raster.cache_clear()


def compose_parent_level(
    child_zoom: int, child_tiles: set[tuple[int, int]]
) -> set[tuple[int, int]]:
    parent_zoom = child_zoom - 1
    parent_tiles = {(x // 2, y // 2) for x, y in child_tiles}
    for parent_x, parent_y in parent_tiles:
        existing_tile = tile_store().get(parent_zoom, parent_x, parent_y)
        if existing_tile is not None:
            with Image.open(BytesIO(existing_tile)) as existing:
                parent = existing.convert("RGBA")
        else:
            parent = Image.new("RGBA", (TILE_SIZE, TILE_SIZE))
        for child_x, child_y in child_tiles:
            if child_x // 2 != parent_x or child_y // 2 != parent_y:
                continue
            source_tile = tile_store().get(child_zoom, child_x, child_y)
            if source_tile is None:
                continue
            with Image.open(BytesIO(source_tile)) as source:
                child = source.convert("RGBA").resize((TILE_SIZE // 2, TILE_SIZE // 2), Image.Resampling.NEAREST)
            parent.alpha_composite(child, ((child_x % 2) * (TILE_SIZE // 2), (child_y % 2) * (TILE_SIZE // 2)))
            child.close()
        write_tile(parent_zoom, parent_x, parent_y, parent)
    return parent_tiles


def build_tile_pyramid(native_zoom: int, native_tiles: set[tuple[int, int]]) -> int:
    """Build local transparent parent tiles for every downloaded map tile."""

    child_tiles = native_tiles
    written_tiles = 0
    for child_zoom in range(native_zoom, 0, -1):
        child_tiles = compose_parent_level(child_zoom, child_tiles)
        written_tiles += len(child_tiles)
    cached_raster.cache_clear()
    return written_tiles


def child_tile_from_parent(parent: Image.Image, x: int, y: int) -> Image.Image:
    width, height = parent.size
    child_x, child_y = x % 2, y % 2
    return parent.crop(
        (
            child_x * width / 2,
            child_y * height / 2,
            (child_x + 1) * width / 2,
            (child_y + 1) * height / 2,
        )
    ).resize((TILE_SIZE, TILE_SIZE), Image.Resampling.NEAREST)


def raster_tile_image(zoom: int, x: int, y: int) -> Image.Image | None:
    payload = tile_store().get(zoom, x, y)
    if payload is not None:
        with Image.open(BytesIO(payload)) as source:
            tile = source.convert("RGBA")
        if tile.getchannel("A").getextrema() == (255, 255) or zoom == 0:
            return tile
        parent = raster_tile_image(zoom - 1, x // 2, y // 2)
        if parent is None:
            return tile
        background = child_tile_from_parent(parent, x, y)
        background.alpha_composite(tile)
        tile.close()
        parent.close()
        return background
    if zoom == 0:
        return None
    parent = raster_tile_image(zoom - 1, x // 2, y // 2)
    if parent is None:
        return None
    tile = child_tile_from_parent(parent, x, y)
    parent.close()
    return tile


def raster_tile_bytes(zoom: int, x: int, y: int) -> bytes | None:
    tile = raster_tile_image(zoom, x, y)
    if tile is None:
        return None
    output = BytesIO()
    tile.save(output, format=LOCAL_TILE_FORMAT, **LOCAL_TILE_SAVE_OPTIONS)
    tile.close()
    return output.getvalue()


def render_tile(zoom: int, x: int, y: int) -> bool:
    """Render one WebP map tile from locally stored native GeoTIFF sources."""

    longitude_bounds = tile_bounds(zoom, x, y)
    west, south, east, north = web_mercator_bounds(zoom, x, y)
    transform = from_bounds(west, south, east, north, TILE_SIZE, TILE_SIZE)
    image = Image.new("RGBA", (TILE_SIZE, TILE_SIZE))
    rendered = False
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
        for coordinate in source_coordinates_for_bounds(*longitude_bounds):
            source_tiff = native_source_store().get(coordinate)
            if source_tiff is None:
                continue
            try:
                with MemoryFile(source_tiff) as memory_file, memory_file.open() as source:
                    with WarpedVRT(
                        source,
                        crs="EPSG:3857",
                        transform=transform,
                        width=TILE_SIZE,
                        height=TILE_SIZE,
                        resampling=Resampling.nearest,
                    ) as warped:
                        source_image = rgba_image(warped.read(1, masked=True))
                        image.alpha_composite(source_image)
                        source_image.close()
                        rendered = True
            except RasterioIOError as error:
                raise RuntimeError(
                    f"stored WorldCover source {source_filename(*coordinate)} is invalid"
                ) from error
    if rendered:
        write_tile(zoom, x, y, image)
    else:
        image.close()
    return rendered


def download_native_viewport(viewport: Viewport | None = None) -> str:
    viewport = viewport or viewport_from_configuration_service()
    source_coordinates = native_source_coordinates(viewport)
    store = native_source_store()
    downloaded_sources = 0
    unavailable_sources = 0
    downloaded_coordinates = []
    for index, coordinate in enumerate(source_coordinates, start=1):
        if store.get(coordinate) is not None:
            continue
        if store.is_unavailable(coordinate):
            unavailable_sources += 1
            continue
        download_state.update(
            f"Downloading native WorldCover source {index} of {len(source_coordinates)}."
        )
        if download_native_source(coordinate) is None:
            unavailable_sources += 1
        else:
            downloaded_sources += 1
            downloaded_coordinates.append(coordinate)
    if downloaded_coordinates:
        invalidate_rendered_tiles_for_sources(downloaded_coordinates)
    cached_raster.cache_clear()
    tile_store().set_revision()
    return (
        f"Stored {downloaded_sources} native ESA WorldCover GeoTIFF source files "
        f"for the current viewport; {unavailable_sources} source areas had no ESA tile."
    )


@lru_cache(maxsize=512)
def cached_raster(zoom: int, x: int, y: int) -> bytes | None:
    if zoom in generation_zoom_levels():
        with tile_render_lock:
            payload = tile_store().get(zoom, x, y)
            if payload is None:
                if not render_tile(zoom, x, y):
                    return None
                payload = tile_store().get(zoom, x, y)
        return payload
    return raster_tile_bytes(zoom, x, y)


def tracked_tile_response(zoom: int, x: int, y: int):
    tile_render_status.start()
    try:
        response = standard_tile_response(
            zoom,
            x,
            y,
            cached_raster,
            maximum_zoom=MAX_NATIVE_ZOOM,
            missing_detail=(
                "local ESA WorldCover source data is unavailable for this tile; "
                "download the current viewport first"
            ),
        )
    except Exception as error:
        tile_render_status.finish(str(error))
        raise
    tile_render_status.finish()
    return response


app = FastAPI(title="Kody ESA WorldCover 2021", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"], allow_methods=["GET", "PUT", "POST"], allow_headers=["Content-Type", "If-Match"])
download_state = DownloadState()
configuration_store = ConfigurationStore()
tile_render_lock = Lock()
tile_render_executor = ThreadPoolExecutor(
    max_workers=MAX_ACTIVE_TILE_RENDERERS,
    thread_name_prefix="worldcover-tile",
)
tile_render_slots = BoundedSemaphore(
    MAX_ACTIVE_TILE_RENDERERS + MAX_QUEUED_TILE_REQUESTS
)
tile_render_status = TileRenderStatus()


@app.get("/api/kody/manifest")
def manifest() -> dict[str, object]:
    return {
        "kodyApiVersion": "1", "serviceId": "worldcover", "displayName": "ESA WorldCover 2021",
        "capabilities": {"tileLayer": {"kind": "raster-tile", "tilePathTemplate": "/api/tiles/{z}/{x}/{y}", "revisionPath": "/api/tiles/revision", "contentTypes": ["image/webp"], "tileSizePixels": 256, "minZoom": 0, "maxZoom": MAX_NATIVE_ZOOM}},
        "attribution": ATTRIBUTION,
        "configuration": {"readPath": "/api/config", "writePath": "/api/config", "activityStatusPath": "/api/tiles/status", "fields": [
            {"key": "generationZoomLevels", "label": "Generated zoom levels", "type": "string", "required": True},
        ], "actions": [
            {"label": "Download current viewport", "path": "/api/actions/download-current-viewport", "statusPath": "/api/actions/download-current-viewport", "confirmationPath": "/api/actions/assess-current-viewport", "method": "POST", "saveConfiguration": False},
            {"label": "Clear local data", "path": "/api/actions/clear-local-data", "method": "POST", "saveConfiguration": False, "confirmationMessage": "Clear all downloaded ESA WorldCover 2021 map tiles? This cannot be undone."},
        ]},
    }


@app.get("/api/config")
def get_config() -> dict[str, object]:
    return configuration_store.response()


def start_native_download(
    background_tasks: BackgroundTasks,
    viewport: Viewport,
) -> dict[str, str]:
    """Queue native GeoTIFF acquisition without rendering map tiles."""

    info = native_download_assessment(viewport)
    download_state.start(str(info["message"]))

    def process() -> None:
        try:
            download_state.complete(download_native_viewport(viewport))
        except Exception as error:
            download_state.fail(error)

    background_tasks.add_task(process)
    return {"message": download_state.response()["message"]}


@app.put("/api/config")
def put_config(
    configuration: WorldCoverConfiguration,
    if_match: str | None = Header(default=None),
) -> dict[str, object]:
    cached_raster.cache_clear()
    return configuration_store.update(configuration, if_match)


@app.get("/api/tiles/revision")
@app.get("/api/raster/revision", deprecated=True)
def revision() -> dict[str, str]:
    return {"revision": tile_store().revision()}


@app.get("/api/tiles/status")
def tile_status() -> dict[str, str | int]:
    return tile_render_status.response()


@app.get("/api/tiles/{zoom}/{x}/{y}")
@app.get("/api/raster/{zoom}/{x}/{y}", deprecated=True)
async def raster(zoom: int, x: int, y: int):
    if not tile_render_slots.acquire(blocking=False):
        raise HTTPException(
            status_code=503,
            detail="WorldCover tile renderer is busy; retry the tile request",
        )
    tile_render_status.enqueue()
    try:
        future = tile_render_executor.submit(tracked_tile_response, zoom, x, y)
    except Exception:
        tile_render_status.cancel()
        tile_render_slots.release()
        raise
    def release_tile_slot(completed_future) -> None:
        if completed_future.cancelled():
            tile_render_status.cancel()
        tile_render_slots.release()

    future.add_done_callback(release_tile_slot)
    return await asyncio.wrap_future(future)


@app.get("/api/actions/assess-current-viewport")
def assess_current() -> dict[str, object]:
    return native_download_assessment()


@app.post("/api/actions/download-current-viewport", status_code=202)
def download_current(background_tasks: BackgroundTasks) -> dict[str, str]:
    return start_native_download(
        background_tasks,
        viewport_from_configuration_service(),
    )


@app.get("/api/actions/download-current-viewport")
def current_status() -> dict[str, str]:
    return download_state.response()


@app.post("/api/actions/clear-local-data")
def clear_data() -> dict[str, str]:
    download_state.assert_idle()
    shutil.rmtree(WORLDCOVER_DIRECTORY, ignore_errors=True)
    cached_raster.cache_clear()
    cached_native_source.cache_clear()
    cached_native_source_store.cache_clear()
    return {"message": "Cleared local ESA WorldCover 2021 native sources and map tiles."}
