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

from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from io import BytesIO
from math import atan, ceil, exp, floor, log, pi
import os
from pathlib import Path
import shutil
import sqlite3
from threading import Lock
from time import time_ns
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel, Field
import numpy
import rasterio
from rasterio.enums import Resampling
from rasterio.errors import RasterioIOError
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT

ROOT_DIRECTORY = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get("KODY_DATA_DIRECTORY", ROOT_DIRECTORY / "data"))
WORLDCOVER_DIRECTORY = DATA_ROOT / "worldcover"
WORLDCOVER_TILE_DIRECTORY = WORLDCOVER_DIRECTORY / "tiles"
CONFIGURATION_SERVICE_URL = os.environ.get(
    "KODY_CONFIGURATION_SERVICE_URL", "http://127.0.0.1:8001"
).rstrip("/")
SOURCE_URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map"
TILE_SIZE = 256
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
TILE_FORMAT_VERSION = "2"


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
    """Empty configuration model required by the shared action dialog."""


class WorldCoverTileStore:
    """SQLite-backed sparse WebP quadtree for local WorldCover tiles."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "tiles.sqlite"

    def _connect(self) -> sqlite3.Connection:
        self.directory.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("CREATE TABLE IF NOT EXISTS tiles (zoom INTEGER NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL, image_webp BLOB NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (zoom, x, y))")
        connection.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        row = connection.execute(
            "SELECT value FROM metadata WHERE key = 'tile_format_version'"
        ).fetchone()
        if row is None or row[0] != TILE_FORMAT_VERSION:
            connection.execute("DELETE FROM tiles")
            connection.execute("DELETE FROM metadata WHERE key = 'revision'")
            connection.execute(
                "INSERT INTO metadata (key, value) VALUES ('tile_format_version', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (TILE_FORMAT_VERSION,),
            )
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def get(self, zoom: int, x: int, y: int) -> bytes | None:
        with self._connection() as connection:
            row = connection.execute("SELECT image_webp FROM tiles WHERE zoom = ? AND x = ? AND y = ?", (zoom, x, y)).fetchone()
        return bytes(row[0]) if row is not None else None

    def put(self, zoom: int, x: int, y: int, image_webp: bytes) -> None:
        with self._connection() as connection:
            connection.execute("INSERT INTO tiles (zoom, x, y, image_webp, updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(zoom, x, y) DO UPDATE SET image_webp = excluded.image_webp, updated_at = excluded.updated_at", (zoom, x, y, image_webp, datetime.now(UTC).isoformat()))

    def revision(self) -> str:
        with self._connection() as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key = 'revision'").fetchone()
        return str(row[0]) if row is not None else ""

    def set_revision(self) -> None:
        with self._connection() as connection:
            connection.execute("INSERT INTO metadata (key, value) VALUES ('revision', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (datetime.now(UTC).isoformat(),))


def tile_store(directory: Path | None = None) -> WorldCoverTileStore:
    return WorldCoverTileStore(WORLDCOVER_TILE_DIRECTORY if directory is None else directory)


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


def assessment(requested_zoom: int | None = None) -> dict[str, object]:
    viewport = viewport_from_configuration_service()
    zoom, tiles = viewport_tiles(viewport, requested_zoom)
    sources = set()
    for x, y in tiles:
        west, south, east, north = tile_bounds(zoom, x, y)
        sources.add(source_coordinate((west + east) / 2, (south + north) / 2))
    return {
        "zoom": zoom,
        "tileCount": len(tiles),
        "sourceTileCount": len(sources),
        "requiresConfirmation": len(tiles) > MAXIMUM_UNCONFIRMED_DOWNLOAD_TILES,
        "message": (
            f"Download {len(tiles)} local WorldCover map tiles at zoom {zoom} "
            f"from {len(sources)} public 3° source tiles."
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


def render_tile(zoom: int, x: int, y: int) -> None:
    longitude_bounds = tile_bounds(zoom, x, y)
    west, south, east, north = web_mercator_bounds(zoom, x, y)
    transform = from_bounds(west, south, east, north, TILE_SIZE, TILE_SIZE)
    image = Image.new("RGBA", (TILE_SIZE, TILE_SIZE))
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"):
        for coordinate in source_coordinates_for_bounds(*longitude_bounds):
            try:
                with rasterio.open(source_url(coordinate)) as source:
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
            except RasterioIOError as error:
                if "HTTP response code: 404" not in str(error):
                    raise
    write_tile(zoom, x, y, image)


def download_viewport(requested_zoom: int | None = None) -> str:
    info = assessment(requested_zoom)
    zoom = int(info["zoom"])
    viewport = viewport_from_configuration_service()
    _, tiles = viewport_tiles(viewport, zoom)
    for index, (x, y) in enumerate(tiles, start=1):
        download_state.update(f"Rendering WorldCover map tile {index} of {len(tiles)}.")
        render_tile(zoom, x, y)
    parent_tiles = build_tile_pyramid(zoom, set(tiles))
    tile_store().set_revision()
    return (
        f"Downloaded {len(tiles)} local ESA WorldCover 2021 map tiles at zoom {zoom} "
        f"and built {parent_tiles} parent tiles."
    )


@lru_cache(maxsize=512)
def cached_raster(zoom: int, x: int, y: int) -> bytes | None:
    return raster_tile_bytes(zoom, x, y)


app = FastAPI(title="Kody ESA WorldCover 2021", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"], allow_methods=["GET", "PUT", "POST"], allow_headers=["Content-Type", "If-Match"])
download_state = DownloadState()


@app.get("/api/kody/manifest")
def manifest() -> dict[str, object]:
    return {
        "kodyApiVersion": "1", "serviceId": "worldcover", "displayName": "ESA WorldCover 2021",
        "capabilities": {"tileLayer": {"kind": "raster-tile", "tilePathTemplate": "/api/raster/{z}/{x}/{y}", "revisionPath": "/api/raster/revision", "contentTypes": ["image/webp"], "tileSizePixels": 256, "minZoom": 0, "maxZoom": MAX_NATIVE_ZOOM}},
        "attribution": ATTRIBUTION,
        "configuration": {"readPath": "/api/config", "writePath": "/api/config", "fields": [], "actions": [
            {"label": "Download current viewport", "path": "/api/actions/download-current-viewport", "statusPath": "/api/actions/download-current-viewport", "confirmationPath": "/api/actions/assess-current-viewport", "method": "POST"},
            {"label": "Clear local data", "path": "/api/actions/clear-local-data", "method": "POST", "confirmationMessage": "Clear all downloaded ESA WorldCover 2021 map tiles? This cannot be undone."},
        ]},
    }


@app.get("/api/config")
def get_config() -> dict[str, str]:
    return {"revision": "worldcover-v1"}


@app.put("/api/config")
def put_config(
    configuration: WorldCoverConfiguration,
    if_match: str | None = Header(default=None),
) -> dict[str, str]:
    if if_match not in (None, "worldcover-v1"):
        raise HTTPException(status_code=409, detail="configuration revision is stale")
    return {"revision": "worldcover-v1"}


@app.get("/api/raster/revision")
def revision() -> dict[str, str]:
    return {"revision": tile_store().revision()}


@app.get("/api/raster/{zoom}/{x}/{y}")
def raster(zoom: int, x: int, y: int) -> Response:
    if not 0 <= zoom <= MAX_NATIVE_ZOOM or not 0 <= x < 2**zoom or not 0 <= y < 2**zoom:
        raise HTTPException(status_code=422, detail="invalid raster tile coordinates")
    tile = cached_raster(zoom, x, y)
    if tile is None:
        raise HTTPException(status_code=404, detail="local ESA WorldCover raster tile is unavailable")
    return Response(content=tile, media_type="image/webp")


@app.get("/api/actions/assess-current-viewport")
def assess_current() -> dict[str, object]:
    return assessment()


@app.post("/api/actions/download-current-viewport", status_code=202)
def download_current(background_tasks: BackgroundTasks) -> dict[str, str]:
    info = assessment()
    download_state.start(str(info["message"]))

    def process() -> None:
        try:
            download_state.complete(download_viewport())
        except Exception as error:
            download_state.fail(error)

    background_tasks.add_task(process)
    return {"message": download_state.response()["message"]}


@app.get("/api/actions/download-current-viewport")
def current_status() -> dict[str, str]:
    return download_state.response()


@app.post("/api/actions/clear-local-data")
def clear_data() -> dict[str, str]:
    download_state.assert_idle()
    shutil.rmtree(WORLDCOVER_DIRECTORY, ignore_errors=True)
    cached_raster.cache_clear()
    return {"message": "Cleared local ESA WorldCover 2021 map tiles."}
