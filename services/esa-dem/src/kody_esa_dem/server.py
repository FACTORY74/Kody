"""On-demand Copernicus DEM GLO-30 terrain tiles for the Kody map."""

from __future__ import annotations

from array import array
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from io import BytesIO
from math import asin, asinh, atan2, ceil, cos, floor, isfinite, log, pi, sin, sinh
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from threading import Lock, RLock
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel, Field
import requests

ROOT_DIRECTORY = Path(__file__).resolve().parents[2]
REPOSITORY_DIRECTORY = Path(__file__).resolve().parents[4]
DATA_ROOT = Path(os.environ.get("KODY_DATA_DIRECTORY", ROOT_DIRECTORY / "data"))
DEM_DIRECTORY = DATA_ROOT / "esa-dem"
CONFIG_FILE = REPOSITORY_DIRECTORY / "config" / "esa_dem.json"
CONFIGURATION_SERVICE_URL = os.environ.get("KODY_CONFIGURATION_SERVICE_URL", "http://127.0.0.1:8001").rstrip("/")
TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"
WEB_MERCATOR_HALF_WORLD = 20037508.342789244
EARTH_RADIUS_METRES = 6_371_008.8
NATIVE_RESOLUTION_METRES = 30
NATIVE_TILE_ZOOM = ceil(log(2 * WEB_MERCATOR_HALF_WORLD / (256 * NATIVE_RESOLUTION_METRES), 2))
MAXIMUM_UNCONFIRMED_DOWNLOAD_TILES = 64
TILE_SIZE = 256
PALETTE = ((-100, (29, 87, 55)), (0, (91, 143, 67)), (250, (184, 170, 93)), (750, (151, 111, 74)), (1500, (241, 241, 237)))
ATTRIBUTION = "© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved"
EVALSCRIPT = """//VERSION=3
function setup() {
  return { input: ["DEM"], output: { bands: 1, sampleType: "FLOAT32" } };
}
function evaluatePixel(sample) { return [sample.DEM]; }
"""


class DemConfiguration(BaseModel):
    client_id: str = Field(default="", alias="clientId", max_length=256)
    client_secret: str = Field(default="", alias="clientSecret", max_length=1024)
    rebuild_pyramid_with_download: bool = Field(default=True, alias="rebuildPyramidWithDownload")


class Viewport(BaseModel):
    west: float = Field(ge=-180, le=180)
    south: float = Field(ge=-85.05112878, le=85.05112878)
    east: float = Field(ge=-180, le=180)
    north: float = Field(ge=-85.05112878, le=85.05112878)
    zoom: float = Field(ge=0, le=18)


class ProfilePoint(BaseModel):
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-85.05112878, le=85.05112878)


class ProfileRequest(BaseModel):
    start: ProfilePoint
    end: ProfilePoint
    sample_count: int = Field(default=128, alias="sampleCount", ge=2, le=1024)


class ConfigurationStore:
    def __init__(self) -> None:
        self.lock = RLock()
        self.configuration = self._load()

    def _load(self) -> DemConfiguration:
        try:
            return DemConfiguration.model_validate_json(CONFIG_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return DemConfiguration()
        except ValueError as error:
            raise RuntimeError(f"ESA DEM configuration is invalid: {CONFIG_FILE}") from error

    def revision(self) -> str:
        return hashlib.sha256(self.configuration.model_dump_json(by_alias=True).encode()).hexdigest()

    def response(self) -> dict[str, object]:
        with self.lock:
            result = self.configuration.model_dump(by_alias=True, exclude={"client_secret"})
            result["clientSecretSaved"] = bool(self.configuration.client_secret)
            result["revision"] = self.revision()
            return result

    def update(self, configuration: DemConfiguration, expected_revision: str | None) -> dict[str, object]:
        with self.lock:
            if expected_revision is not None and expected_revision != self.revision():
                raise HTTPException(status_code=409, detail="configuration revision is stale")
            if not configuration.client_secret:
                configuration.client_secret = self.configuration.client_secret
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(configuration.model_dump_json(indent=2, by_alias=True) + "\n", encoding="utf-8")
            self.configuration = configuration
            return self.response()


class DownloadState:
    def __init__(self) -> None:
        self.lock = Lock()
        self.status: Literal["idle", "processing", "succeeded", "failed"] = "idle"
        self.message = ""

    def start(self, message: str) -> None:
        with self.lock:
            if self.status == "processing":
                raise HTTPException(status_code=409, detail="ESA DEM download is already running")
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
                raise HTTPException(status_code=409, detail="ESA DEM download is already running")


def numeric_path(zoom: int, x: int, y: int) -> Path:
    return DEM_DIRECTORY / "numeric" / str(zoom) / str(x) / f"{y}.f32.gz"


def image_path(zoom: int, x: int, y: int) -> Path:
    return DEM_DIRECTORY / "tiles" / str(zoom) / str(x) / f"{y}.png"


def revision_path() -> Path:
    return DEM_DIRECTORY / ".revision"


def longitude_tile(longitude: float, zoom: int) -> int:
    return min(2**zoom - 1, max(0, floor((longitude + 180) / 360 * 2**zoom)))


def latitude_tile(latitude: float, zoom: int) -> int:
    radians = latitude * pi / 180
    return min(2**zoom - 1, max(0, floor((1 - log(__import__("math").tan(radians) + 1 / cos(radians)) / pi) / 2 * 2**zoom)))


def viewport_tiles(viewport: Viewport, requested_zoom: int | None = None) -> tuple[int, list[tuple[int, int]]]:
    zoom = floor(viewport.zoom) if requested_zoom is None else requested_zoom
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


def tile_bounds(zoom: int, x: int, y: int) -> tuple[float, float, float, float]:
    span = 2 * WEB_MERCATOR_HALF_WORLD / 2**zoom
    left, top = -WEB_MERCATOR_HALF_WORLD + x * span, WEB_MERCATOR_HALF_WORLD - y * span
    return left, top - span, left + span, top


def token(configuration: DemConfiguration) -> str:
    response = requests.post(TOKEN_URL, data={"grant_type": "client_credentials", "client_id": configuration.client_id, "client_secret": configuration.client_secret}, timeout=30)
    response.raise_for_status()
    value = response.json().get("access_token")
    if not isinstance(value, str) or not value:
        raise RuntimeError("Copernicus token response did not include an access token")
    return value


def dem_request(bounds: tuple[float, float, float, float]) -> dict[str, object]:
    return {
        "input": {"bounds": {"bbox": list(bounds), "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/3857"}}, "data": [{"type": "dem", "dataFilter": {"demInstance": "COPERNICUS_30"}}]},
        "output": {"width": TILE_SIZE, "height": TILE_SIZE, "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}]},
        "evalscript": EVALSCRIPT,
    }


def terrain_colour(value: float) -> tuple[int, int, int, int]:
    if value <= PALETTE[0][0]:
        return *PALETTE[0][1], 255
    if value >= PALETTE[-1][0]:
        return *PALETTE[-1][1], 255
    for (low, start), (high, end) in zip(PALETTE, PALETTE[1:]):
        if low <= value <= high:
            fraction = (value - low) / (high - low)
            return *(round(start[index] + (end[index] - start[index]) * fraction) for index in range(3)), 255
    raise RuntimeError("terrain palette does not cover elevation")


def write_tile(zoom: int, x: int, y: int, values: array) -> None:
    if len(values) != TILE_SIZE * TILE_SIZE:
        raise ValueError("DEM response does not contain a 256x256 elevation grid")
    numeric = numeric_path(zoom, x, y)
    numeric.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(numeric, "wb") as output:
        output.write(values.tobytes())
    image = Image.new("RGBA", (TILE_SIZE, TILE_SIZE))
    image.putdata([terrain_colour(value) if isfinite(value) else (0, 0, 0, 0) for value in values])
    rendered = image_path(zoom, x, y)
    rendered.parent.mkdir(parents=True, exist_ok=True)
    image.save(rendered, format="PNG", optimize=True)
    image.close()


def download_tile(session: requests.Session, access_token: str, zoom: int, x: int, y: int) -> None:
    response = session.post(PROCESS_URL, headers={"Authorization": f"Bearer {access_token}"}, json=dem_request(tile_bounds(zoom, x, y)), timeout=120)
    if response.status_code == 403:
        raise PermissionError(
            "Copernicus DEM GLO-30 requires a Copernicus Contributing Missions "
            "(CCM) registered account. Register the account for CCM access, then "
            "enter client credentials created for that authorized account."
        )
    response.raise_for_status()
    with Image.open(BytesIO(response.content)) as image:
        values = array("f", (float(value) for value in image.convert("F").getdata()))
    write_tile(zoom, x, y, values)


def parent_tiles(zoom: int, tiles: set[tuple[int, int]]) -> int:
    written = 0
    children = tiles
    for child_zoom in range(zoom, 0, -1):
        parents = {(x // 2, y // 2) for x, y in children}
        for parent_x, parent_y in parents:
            parent = Image.new("RGBA", (256, 256))
            for child_x, child_y in children:
                if child_x // 2 == parent_x and child_y // 2 == parent_y:
                    path = image_path(child_zoom, child_x, child_y)
                    if path.is_file():
                        with Image.open(path) as child:
                            parent.alpha_composite(child.convert("RGBA").resize((128, 128), Image.Resampling.LANCZOS), ((child_x % 2) * 128, (child_y % 2) * 128))
            path = image_path(child_zoom - 1, parent_x, parent_y)
            path.parent.mkdir(parents=True, exist_ok=True)
            parent.save(path, format="PNG", optimize=True)
            parent.close()
        written += len(parents)
        children = parents
    return written


def download_viewport(configuration: DemConfiguration, requested_zoom: int | None = None, label: str = "") -> str:
    if not configuration.client_id or not configuration.client_secret:
        raise ValueError("Copernicus client ID and client secret are required")
    zoom, tiles = viewport_tiles(viewport_from_configuration_service(), requested_zoom)
    access_token = token(configuration)
    session = requests.Session()
    for index, (x, y) in enumerate(tiles, start=1):
        download_state.update(f"Downloading GLO-30 elevation tile {index} of {len(tiles)} at zoom {zoom}.")
        download_tile(session, access_token, zoom, x, y)
    revision_path().parent.mkdir(parents=True, exist_ok=True)
    revision_path().write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
    cached_raster.cache_clear()
    overview_count = parent_tiles(zoom, set(tiles)) if configuration.rebuild_pyramid_with_download else 0
    detail = f" at {label}" if label else ""
    return f"Downloaded {len(tiles)} ESA GLO-30 terrain tiles{detail} (zoom {zoom})." + (f" Updated {overview_count} parent overview tiles." if overview_count else "")


def assessment(requested_zoom: int | None = None, label: str = "") -> dict[str, object]:
    zoom, tiles = viewport_tiles(viewport_from_configuration_service(), requested_zoom)
    confirm = len(tiles) > MAXIMUM_UNCONFIRMED_DOWNLOAD_TILES
    return {"tileCount": len(tiles), "zoom": zoom, "requiresConfirmation": confirm, "message": f"The current viewport needs {len(tiles)} {label}terrain tiles at zoom {zoom}. " + ("Continue with this download?" if confirm else "Download now?")}


def read_numeric(zoom: int, x: int, y: int) -> array | None:
    path = numeric_path(zoom, x, y)
    if not path.is_file():
        return None
    with gzip.open(path, "rb") as source:
        values = array("f")
        values.frombytes(source.read())
    return values if len(values) == TILE_SIZE * TILE_SIZE else None


def elevation(longitude: float, latitude: float) -> float | None:
    numeric_root = DEM_DIRECTORY / "numeric"
    if not numeric_root.is_dir():
        return None
    for directory in sorted(numeric_root.iterdir(), key=lambda item: int(item.name), reverse=True):
        if not directory.is_dir() or not directory.name.isdigit():
            continue
        zoom = int(directory.name)
        x, y = longitude_tile(longitude, zoom), latitude_tile(latitude, zoom)
        values = read_numeric(zoom, x, y)
        if values is None:
            continue
        pixel_x = min(255, max(0, floor(((longitude + 180) / 360 * 2**zoom - x) * 256)))
        world_y = (1 - asinh(__import__("math").tan(latitude * pi / 180)) / pi) / 2 * 2**zoom
        pixel_y = min(255, max(0, floor((world_y - y) * 256)))
        value = values[pixel_y * 256 + pixel_x]
        return value if isfinite(value) else None
    return None


@lru_cache(maxsize=512)
def cached_raster(zoom: int, x: int, y: int) -> bytes | None:
    path = image_path(zoom, x, y)
    return path.read_bytes() if path.is_file() else None


app = FastAPI(title="Kody ESA Download Terrain", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"], allow_methods=["GET", "PUT", "POST", "DELETE"], allow_headers=["Content-Type", "If-Match"])
configuration_store = ConfigurationStore()
download_state = DownloadState()


@app.get("/api/kody/manifest")
def manifest() -> dict[str, object]:
    return {"kodyApiVersion": "1", "serviceId": "esa-dem", "displayName": "ESA Download Terrain", "capabilities": {"tileLayer": {"kind": "raster-tile", "tilePathTemplate": "/api/raster/{z}/{x}/{y}", "revisionPath": "/api/raster/revision", "contentTypes": ["image/png"], "tileSizePixels": 256, "minZoom": 0, "maxZoom": 18}}, "attribution": ATTRIBUTION, "configuration": {"readPath": "/api/config", "writePath": "/api/config", "fields": [{"key": "clientId", "label": "Copernicus client ID", "type": "string", "required": True}, {"key": "clientSecret", "label": "Copernicus client secret", "type": "password", "required": False, "secretStoredKey": "clientSecretSaved"}, {"key": "rebuildPyramidWithDownload", "label": "Rebuild tile pyramid after download", "type": "boolean"}], "actions": [{"label": "Download current viewport", "path": "/api/actions/download-current-viewport", "statusPath": "/api/actions/download-current-viewport", "confirmationPath": "/api/actions/assess-current-viewport", "method": "POST"}, {"label": "Download viewport at GLO-30 resolution", "path": "/api/actions/download-viewport-at-glo-30", "statusPath": "/api/actions/download-viewport-at-glo-30", "confirmationPath": "/api/actions/assess-viewport-at-glo-30", "method": "POST"}, {"label": "Clear local data", "path": "/api/actions/clear-local-data", "method": "POST", "confirmationMessage": "Clear all downloaded ESA GLO-30 terrain data? This cannot be undone."}]}}


@app.get("/api/config")
def get_config() -> dict[str, object]:
    return configuration_store.response()


@app.put("/api/config")
def put_config(configuration: DemConfiguration, if_match: str | None = Header(default=None)) -> dict[str, object]:
    return configuration_store.update(configuration, if_match)


@app.get("/api/raster/revision")
def revision() -> dict[str, str]:
    return {"revision": revision_path().read_text(encoding="utf-8") if revision_path().is_file() else ""}


@app.get("/api/raster/{zoom}/{x}/{y}")
def raster(zoom: int, x: int, y: int) -> Response:
    if not 0 <= zoom <= 18 or not 0 <= x < 2**zoom or not 0 <= y < 2**zoom:
        raise HTTPException(status_code=422, detail="invalid raster tile coordinates")
    tile = cached_raster(zoom, x, y)
    if tile is None:
        raise HTTPException(status_code=404, detail="local ESA DEM raster tile is unavailable")
    return Response(content=tile, media_type="image/png")


@app.get("/api/elevation")
def get_elevation(longitude: float, latitude: float) -> dict[str, object]:
    if not -180 <= longitude <= 180 or not -85.05112878 <= latitude <= 85.05112878:
        raise HTTPException(status_code=422, detail="invalid longitude or latitude")
    return {"heightMetres": elevation(longitude, latitude), "verticalDatum": "EGM2008"}


@app.post("/api/terrain/profile")
def profile(request: ProfileRequest) -> dict[str, object]:
    latitude_delta, longitude_delta = (request.end.latitude - request.start.latitude) * pi / 180, (request.end.longitude - request.start.longitude) * pi / 180
    haversine = sin(latitude_delta / 2) ** 2 + cos(request.start.latitude * pi / 180) * cos(request.end.latitude * pi / 180) * sin(longitude_delta / 2) ** 2
    distance = EARTH_RADIUS_METRES * 2 * atan2(haversine**0.5, (1 - haversine)**0.5)
    samples = []
    for index in range(request.sample_count):
        fraction = index / (request.sample_count - 1)
        longitude, latitude = request.start.longitude + (request.end.longitude - request.start.longitude) * fraction, request.start.latitude + (request.end.latitude - request.start.latitude) * fraction
        samples.append({"distanceMetres": distance * fraction, "longitude": longitude, "latitude": latitude, "heightMetres": elevation(longitude, latitude)})
    heights = [sample["heightMetres"] for sample in samples if sample["heightMetres"] is not None]
    return {"start": request.start.model_dump(), "end": request.end.model_dump(), "distanceMetres": distance, "verticalDatum": "EGM2008", "samples": samples, "statistics": {"minimumMetres": min(heights) if heights else None, "maximumMetres": max(heights) if heights else None, "missingSampleCount": request.sample_count - len(heights)}}


def run_download(background_tasks: BackgroundTasks, requested_zoom: int | None, label: str) -> dict[str, str]:
    configuration = configuration_store.configuration
    if not configuration.client_id or not configuration.client_secret:
        raise HTTPException(status_code=422, detail="Copernicus client ID and client secret are required")
    info = assessment(requested_zoom, label)
    download_state.start(f"Preparing {info['tileCount']} ESA GLO-30 terrain tiles at zoom {info['zoom']}.")
    def process() -> None:
        try:
            download_state.complete(download_viewport(configuration, requested_zoom, label.strip()))
        except Exception as error:
            download_state.fail(error)
    background_tasks.add_task(process)
    return {"message": download_state.response()["message"]}


@app.get("/api/actions/assess-current-viewport")
def assess_current() -> dict[str, object]:
    return assessment()


@app.get("/api/actions/assess-viewport-at-glo-30")
def assess_native() -> dict[str, object]:
    return assessment(NATIVE_TILE_ZOOM, "GLO-30 ")


@app.post("/api/actions/download-current-viewport", status_code=202)
def download_current(background_tasks: BackgroundTasks) -> dict[str, str]:
    return run_download(background_tasks, None, "")


@app.get("/api/actions/download-current-viewport")
def current_status() -> dict[str, str]:
    return download_state.response()


@app.post("/api/actions/download-viewport-at-glo-30", status_code=202)
def download_native(background_tasks: BackgroundTasks) -> dict[str, str]:
    return run_download(background_tasks, NATIVE_TILE_ZOOM, "GLO-30 ")


@app.get("/api/actions/download-viewport-at-glo-30")
def native_status() -> dict[str, str]:
    return download_state.response()


@app.post("/api/actions/clear-local-data")
def clear_data() -> dict[str, str]:
    download_state.assert_idle()
    shutil.rmtree(DEM_DIRECTORY, ignore_errors=True)
    cached_raster.cache_clear()
    return {"message": "Cleared local ESA GLO-30 terrain data."}
