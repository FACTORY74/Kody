"""On-demand Copernicus Global Mosaic raster tiles for the Kody map."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
import hashlib
from contextlib import contextmanager
from io import BytesIO
from math import atan, ceil, cos, exp, floor, log, log2, pi, sin, sinh, tan
from pathlib import Path
import os
import shutil
import sqlite3
from threading import Lock, RLock
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel, Field, field_validator
import requests

ROOT_DIRECTORY = Path(__file__).resolve().parents[2]
REPOSITORY_DIRECTORY = Path(__file__).resolve().parents[4]
DATA_ROOT = Path(os.environ.get("KODY_DATA_DIRECTORY", ROOT_DIRECTORY / "data"))
COPERNICUS_DIRECTORY = DATA_ROOT / "copernicus" / "tiles"
CONFIG_FILE = REPOSITORY_DIRECTORY / "config" / "copernicus.json"
WEB_MERCATOR_HALF_WORLD = 20037508.342789244
EARTH_RADIUS_METRES = 6378137
CONFIGURATION_SERVICE_URL = os.environ.get(
    "KODY_CONFIGURATION_SERVICE_URL", "http://127.0.0.1:8001"
).rstrip("/")
COPERNICUS_TOKEN_URL = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
)
COPERNICUS_PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"
COPERNICUS_CATALOG_URL = "https://sh.dataspace.copernicus.eu/catalog/v1/search"
GLOBAL_MOSAIC_COLLECTION = "byoc-5460de54-082e-473a-b6ea-d5cbe3c17cca"
MAXIMUM_UNCONFIRMED_DOWNLOAD_TILES = 64
MAXIMUM_PROCESS_REQUEST_DEGREES = 10
MAXIMUM_PROCESS_PIXEL_SIZE_METRES = 1_600
MAXIMUM_PROCESS_OUTPUT_PIXELS = 2_500
COPERNICUS_NATIVE_RESOLUTION_METRES = 10
COPERNICUS_NATIVE_TILE_ZOOM = ceil(
    log2(2 * WEB_MERCATOR_HALF_WORLD / (256 * COPERNICUS_NATIVE_RESOLUTION_METRES))
)
LOCAL_TILE_FORMAT = "WEBP"
LOCAL_TILE_SAVE_OPTIONS = {"quality": 90, "method": 6}
TRUE_COLOR_EVALSCRIPT = """//VERSION=3
function setup() {
return {
    input: ["B02", "B03", "B04"],
    output: { bands: 3 }
};
}

function evaluatePixel(sample) {
    return [3.5 * sample.B04 / 10000, 3.5 * sample.B03 / 10000, 3.5 * sample.B02 / 10000];
}
"""


class CopernicusTileStore:
    """SQLite-backed local WebP tile cache."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "tiles.sqlite"

    def _connect(self) -> sqlite3.Connection:
        self.directory.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tiles (
                zoom INTEGER NOT NULL,
                x INTEGER NOT NULL,
                y INTEGER NOT NULL,
                image_webp BLOB NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (zoom, x, y)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
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

    def migrate_legacy_files(self) -> int:
        files: list[tuple[Path, int, int, int]] = []
        for path in self.directory.glob("*/*/*.webp"):
            if not path.is_file():
                continue
            try:
                files.append(
                    (path, int(path.parents[1].name), int(path.parent.name), int(path.stem))
                )
            except ValueError:
                continue
        revision_marker = self.directory / ".revision"
        if not files and not revision_marker.is_file():
            return 0
        migrated = 0
        with self._connection() as connection:
            for path, zoom, x, y in files:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO tiles (zoom, x, y, image_webp, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (zoom, x, y, path.read_bytes(), datetime.now(UTC).isoformat()),
                )
                migrated += 1
            if revision_marker.is_file():
                connection.execute(
                    """
                    INSERT INTO metadata (key, value) VALUES ('revision', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (revision_marker.read_text(encoding="utf-8").strip(),),
                )
        for path, _, _, _ in files:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        for directory in sorted(
            (path for path in self.directory.glob("*/*") if path.is_dir()),
            reverse=True,
        ):
            try:
                directory.rmdir()
            except OSError:
                pass
        if revision_marker.is_file():
            revision_marker.unlink()
        return migrated

    def get(self, zoom: int, x: int, y: int) -> bytes | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT image_webp FROM tiles WHERE zoom = ? AND x = ? AND y = ?",
                (zoom, x, y),
            ).fetchone()
        return bytes(row[0]) if row is not None else None

    def put(self, zoom: int, x: int, y: int, image_webp: bytes) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO tiles (zoom, x, y, image_webp, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(zoom, x, y) DO UPDATE SET
                    image_webp = excluded.image_webp,
                    updated_at = excluded.updated_at
                """,
                (zoom, x, y, image_webp, datetime.now(UTC).isoformat()),
            )

    def coordinates(self, zoom: int) -> set[tuple[int, int]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT x, y FROM tiles WHERE zoom = ?", (zoom,)
            ).fetchall()
        return {(int(x), int(y)) for x, y in rows}

    def zooms(self) -> list[int]:
        with self._connection() as connection:
            rows = connection.execute("SELECT DISTINCT zoom FROM tiles").fetchall()
        return sorted(int(row[0]) for row in rows)

    def revision(self) -> str:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key = 'revision'"
            ).fetchone()
        return str(row[0]) if row is not None else ""

    def set_revision(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO metadata (key, value) VALUES ('revision', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (datetime.now(UTC).isoformat(),),
            )


def tile_store(directory: Path | None = None) -> CopernicusTileStore:
    return CopernicusTileStore(COPERNICUS_DIRECTORY if directory is None else directory)


def initialize_data_directory() -> None:
    tile_store().migrate_legacy_files()


initialize_data_directory()


class CopernicusConfiguration(BaseModel):
    client_id: str = Field(default="", alias="clientId", max_length=256)
    client_secret: str = Field(default="", alias="clientSecret", max_length=1024)
    mosaic_period: str = Field(default="latest", alias="mosaicPeriod", max_length=128)
    rebuild_pyramid_with_download: bool = Field(
        default=True, alias="rebuildPyramidWithDownload"
    )

    @field_validator("mosaic_period")
    @classmethod
    def validate_mosaic_period(cls, value: str) -> str:
        if value == "latest":
            return value
        try:
            start, end = value.split("/", maxsplit=1)
            datetime.fromisoformat(start.replace("Z", "+00:00"))
            datetime.fromisoformat(end.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(
                "mosaicPeriod must be 'latest' or an ISO 8601 date-time range"
            ) from error
        return value


class ConfigurationStore:
    def __init__(self, path: Path = CONFIG_FILE) -> None:
        self.path = path
        self.lock = RLock()
        self.configuration = self._read()

    def _read(self) -> CopernicusConfiguration:
        try:
            return CopernicusConfiguration.model_validate_json(
                self.path.read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            return CopernicusConfiguration()
        except ValueError as error:
            raise RuntimeError(
                f"Copernicus configuration is invalid: {self.path}"
            ) from error

    def _write(self, configuration: CopernicusConfiguration) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(".tmp")
        temporary_path.write_text(
            configuration.model_dump_json(indent=2, by_alias=True) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(self.path)

    def revision(self) -> str:
        encoded = self.configuration.model_dump_json(by_alias=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def response(self) -> dict[str, object]:
        with self.lock:
            configuration = self.configuration.model_dump(by_alias=True)
            configuration["clientSecretSaved"] = bool(configuration["clientSecret"])
            configuration["clientSecret"] = ""
            return {"revision": self.revision(), **configuration}

    def update(
        self, configuration: CopernicusConfiguration, expected_revision: str | None
    ) -> dict[str, object]:
        with self.lock:
            if expected_revision is not None and expected_revision != self.revision():
                raise HTTPException(status_code=409, detail="configuration revision is stale")
            if not configuration.client_secret:
                configuration = configuration.model_copy(
                    update={"client_secret": self.configuration.client_secret}
                )
            self._write(configuration)
            self.configuration = configuration
            return self.response()


class ImportState:
    def __init__(self) -> None:
        self.lock = Lock()
        self.status: Literal["idle", "processing", "succeeded", "failed"] = "idle"
        self.message = ""

    def start(self, message: str) -> None:
        with self.lock:
            if self.status == "processing":
                raise HTTPException(status_code=409, detail="satellite image processing is already running")
            self.status = "processing"
            self.message = message

    def complete(self, message: str) -> None:
        with self.lock:
            self.status = "succeeded"
            self.message = message

    def update(self, message: str) -> None:
        with self.lock:
            if self.status == "processing":
                self.message = message

    def fail(self, error: Exception) -> None:
        with self.lock:
            self.status = "failed"
            self.message = str(error)

    def response(self) -> dict[str, str]:
        with self.lock:
            return {"status": self.status, "message": self.message}

    def assert_idle(self) -> None:
        with self.lock:
            if self.status == "processing":
                raise HTTPException(status_code=409, detail="Copernicus tile download is already running")


class Viewport(BaseModel):
    west: float = Field(ge=-180, le=180)
    south: float = Field(ge=-85.05112878, le=85.05112878)
    east: float = Field(ge=-180, le=180)
    north: float = Field(ge=-85.05112878, le=85.05112878)
    zoom: float = Field(ge=0, le=18)


def viewport_from_configuration_service() -> Viewport:
    try:
        with urlopen(f"{CONFIGURATION_SERVICE_URL}/api/context/viewport", timeout=5) as response:
            return Viewport.model_validate_json(response.read())
    except HTTPError as error:
        if error.code == 404:
            raise HTTPException(
                status_code=409,
                detail="the map viewport is unavailable; open the map and move it before downloading",
            ) from error
        raise HTTPException(
            status_code=502,
            detail=f"configuration service returned {error.code} while reading the map viewport",
        ) from error
    except URLError as error:
        raise HTTPException(
            status_code=502,
            detail=f"could not read the map viewport: {error.reason}",
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=502, detail="configuration service returned an invalid viewport") from error


def longitude_tile(longitude: float, zoom: int) -> int:
    return min(2**zoom - 1, max(0, floor((longitude + 180) / 360 * 2**zoom)))


def latitude_tile(latitude: float, zoom: int) -> int:
    latitude_radians = latitude * pi / 180
    coordinate = (1 - log(tan(latitude_radians) + 1 / cos(latitude_radians)) / pi) / 2 * 2**zoom
    return min(2**zoom - 1, max(0, floor(coordinate)))


def viewport_tiles(
    viewport: Viewport, requested_zoom: int | None = None
) -> tuple[int, list[tuple[int, int]]]:
    zoom = floor(viewport.zoom) if requested_zoom is None else requested_zoom
    west = longitude_tile(viewport.west, zoom)
    east = min(2**zoom - 1, max(0, ceil((viewport.east + 180) / 360 * 2**zoom) - 1))
    north = latitude_tile(viewport.north, zoom)
    south = min(
        2**zoom - 1,
        max(
            0,
            ceil(
                (1 - log(tan(viewport.south * pi / 180) + 1 / cos(viewport.south * pi / 180)) / pi)
                / 2
                * 2**zoom
            )
            - 1,
        ),
    )
    return zoom, [(x, y) for y in range(north, south + 1) for x in range(west, east + 1)]


def web_mercator_longitude(x: float) -> float:
    return x / WEB_MERCATOR_HALF_WORLD * 180


def web_mercator_latitude(y: float) -> float:
    return atan(sinh(y / WEB_MERCATOR_HALF_WORLD * pi)) * 180 / pi


def longitude_web_mercator(longitude: float) -> float:
    return longitude / 180 * WEB_MERCATOR_HALF_WORLD


def latitude_web_mercator(latitude: float) -> float:
    return log(tan(pi / 4 + latitude * pi / 360)) / pi * WEB_MERCATOR_HALF_WORLD


def copernicus_tile_sections(
    tile_zoom: int, x: int, y: int
) -> list[tuple[tuple[float, float, float, float], tuple[int, int, int, int]]]:
    tile_span = 2 * WEB_MERCATOR_HALF_WORLD / 2**tile_zoom
    left = -WEB_MERCATOR_HALF_WORLD + x * tile_span
    top = WEB_MERCATOR_HALF_WORLD - y * tile_span
    right = left + tile_span
    bottom = top - tile_span
    west = web_mercator_longitude(left)
    east = web_mercator_longitude(right)
    north = web_mercator_latitude(top)
    south = web_mercator_latitude(bottom)
    maximum_projected_span = (
        MAXIMUM_PROCESS_PIXEL_SIZE_METRES * MAXIMUM_PROCESS_OUTPUT_PIXELS
    )
    columns = max(
        1,
        ceil((east - west) / MAXIMUM_PROCESS_REQUEST_DEGREES),
        ceil(tile_span / maximum_projected_span),
    )
    rows = max(
        1,
        ceil((north - south) / MAXIMUM_PROCESS_REQUEST_DEGREES),
        ceil(tile_span / maximum_projected_span),
    )
    while any(
        web_mercator_latitude(top - tile_span * row / rows)
        - web_mercator_latitude(top - tile_span * (row + 1) / rows)
        > MAXIMUM_PROCESS_REQUEST_DEGREES
        for row in range(rows)
    ):
        rows += 1
    sections = []
    for row in range(rows):
        section_top = top - tile_span * row / rows
        section_bottom = top - tile_span * (row + 1) / rows
        for column in range(columns):
            section_left = left + tile_span * column / columns
            section_right = left + tile_span * (column + 1) / columns
            sections.append((
                (
                    section_left,
                    section_bottom,
                    section_right,
                    section_top,
                ),
                (
                    round(column * 256 / columns),
                    round(row * 256 / rows),
                    round((column + 1) * 256 / columns),
                    round((row + 1) * 256 / rows),
                ),
            ))
    return sections


def copernicus_request_dimensions(
    bounds: tuple[float, float, float, float],
    destination: tuple[int, int, int, int],
) -> tuple[int, int]:
    left, bottom, right, top = bounds
    destination_left, destination_top, destination_right, destination_bottom = destination
    return (
        min(
            max(
                destination_right - destination_left,
                ceil((right - left) / MAXIMUM_PROCESS_PIXEL_SIZE_METRES),
            ),
            max(1, floor((right - left) / COPERNICUS_NATIVE_RESOLUTION_METRES)),
        ),
        min(
            max(
                destination_bottom - destination_top,
                ceil((top - bottom) / MAXIMUM_PROCESS_PIXEL_SIZE_METRES),
            ),
            max(1, floor((top - bottom) / COPERNICUS_NATIVE_RESOLUTION_METRES)),
        ),
    )


def copernicus_request(
    bounds: tuple[float, float, float, float],
    width: int,
    height: int,
    mosaic_period: str = "latest",
) -> dict[str, Any]:
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if mosaic_period == "latest":
        start, end = "2000-01-01T00:00:00Z", now
    else:
        try:
            start, end = mosaic_period.split("/", maxsplit=1)
            datetime.fromisoformat(start.replace("Z", "+00:00"))
            datetime.fromisoformat(end.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("Copernicus mosaic period must be 'latest' or an ISO 8601 range") from error
    return {
        "input": {
            "bounds": {
                "bbox": list(bounds),
                "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/3857"},
            },
            "data": [{
                "dataFilter": {
                    "timeRange": {"from": start, "to": end},
                    "mosaickingOrder": "mostRecent",
                },
                "type": GLOBAL_MOSAIC_COLLECTION,
            }],
        },
        "output": {
            "width": width,
            "height": height,
            "responses": [{"identifier": "default", "format": {"type": "image/png"}}],
        },
        "evalscript": TRUE_COLOR_EVALSCRIPT,
    }


def process_response_bytes(
    response: requests.Response,
    bounds: tuple[float, float, float, float],
    width: int,
    height: int,
) -> bytes:
    try:
        response.raise_for_status()
    except requests.HTTPError as error:
        detail = response.text.strip().replace("\n", " ")
        if len(detail) > 1_000:
            detail = f"{detail[:1_000]}..."
        raise RuntimeError(
            "Copernicus Process API rejected "
            f"{width}x{height}px EPSG:3857 bbox {bounds}: {detail or error}"
        ) from error
    return response.content


def copernicus_mosaic_options(
    configuration: CopernicusConfiguration,
) -> list[dict[str, str]]:
    if not configuration.client_id or not configuration.client_secret:
        raise ValueError("Copernicus client ID and client secret are required")
    session = requests.Session()
    token_response = session.post(
        COPERNICUS_TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": configuration.client_id,
            "client_secret": configuration.client_secret,
        },
        timeout=30,
    )
    token_response.raise_for_status()
    access_token = token_response.json().get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise RuntimeError("Copernicus token response did not include an access token")
    response = session.post(
        COPERNICUS_CATALOG_URL,
        json={
            "collections": [GLOBAL_MOSAIC_COLLECTION],
            "bbox": [-180, -85, 180, 85],
            "datetime": "2000-01-01T00:00:00Z/..",
            "distinct": "date",
            "limit": 100,
        },
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    response.raise_for_status()
    catalogue = response.json()
    features = catalogue.get("features")
    if not isinstance(features, list):
        raise RuntimeError("Copernicus catalogue response did not include mosaic items")
    periods: dict[str, str] = {}
    for feature in features:
        if not isinstance(feature, str):
            continue
        try:
            start_date = date.fromisoformat(feature)
        except ValueError:
            continue
        next_quarter = date(
            start_date.year + (start_date.month == 10),
            1 if start_date.month == 10 else start_date.month + 3,
            1,
        )
        end_date = next_quarter - timedelta(days=1)
        start = (
            datetime.combine(start_date, datetime.min.time(), UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )
        end = datetime.combine(
            end_date, datetime.max.time(), UTC
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
        quarter = (start_date.month - 1) // 3 + 1
        periods[f"{start}/{end}"] = (
            f"{start_date.year} Q{quarter} ({start_date.isoformat()} to {end_date.isoformat()})"
        )
    return [{"value": "latest", "label": "Latest available mosaic"}] + [
        {"value": period, "label": label}
        for period, label in sorted(periods.items(), reverse=True)
    ]


def download_copernicus_tile(
    session: requests.Session,
    headers: dict[str, str],
    tile_zoom: int,
    x: int,
    y: int,
    mosaic_period: str = "latest",
) -> int:
    image = Image.new("RGB", (256, 256))
    sections = copernicus_tile_sections(tile_zoom, x, y)
    try:
        for bounds, destination_bounds in sections:
            left, top, right, bottom = destination_bounds
            request_width, request_height = copernicus_request_dimensions(
                bounds, destination_bounds
            )
            response = session.post(
                COPERNICUS_PROCESS_URL,
                json=copernicus_request(
                    bounds, request_width, request_height, mosaic_period
                ),
                headers=headers,
                timeout=120,
            )
            with Image.open(
                BytesIO(process_response_bytes(response, bounds, request_width, request_height))
            ) as section:
                image.paste(
                    section.convert("RGB").resize(
                        (right - left, bottom - top), Image.Resampling.LANCZOS
                    ),
                    (left, top),
                )
        output = BytesIO()
        image.save(output, format=LOCAL_TILE_FORMAT, **LOCAL_TILE_SAVE_OPTIONS)
        tile_store().put(tile_zoom, x, y, output.getvalue())
    finally:
        image.close()
    return len(sections)


def download_copernicus_tile_grid(
    session: requests.Session,
    headers: dict[str, str],
    tile_zoom: int,
    tiles: list[tuple[int, int]],
    mosaic_period: str = "latest",
) -> int:
    """Download one contiguous tile grid as a single, consistently mosaicked image."""

    if not tiles:
        return 0
    columns = sorted({x for x, _ in tiles})
    rows = sorted({y for _, y in tiles})
    if columns != list(range(columns[0], columns[-1] + 1)) or rows != list(
        range(rows[0], rows[-1] + 1)
    ):
        raise ValueError("Copernicus tile grid must be rectangular and contiguous")
    expected_tiles = {(x, y) for y in rows for x in columns}
    if set(tiles) != expected_tiles:
        raise ValueError("Copernicus tile grid must be rectangular and contiguous")
    minimum_x, maximum_x = columns[0], columns[-1]
    minimum_y, maximum_y = rows[0], rows[-1]
    tile_span = 2 * WEB_MERCATOR_HALF_WORLD / 2**tile_zoom
    bounds = (
        -WEB_MERCATOR_HALF_WORLD + minimum_x * tile_span,
        WEB_MERCATOR_HALF_WORLD - (maximum_y + 1) * tile_span,
        -WEB_MERCATOR_HALF_WORLD + (maximum_x + 1) * tile_span,
        WEB_MERCATOR_HALF_WORLD - minimum_y * tile_span,
    )
    width = len(columns) * 256
    height = len(rows) * 256
    response = session.post(
        COPERNICUS_PROCESS_URL,
        json=copernicus_request(bounds, width, height, mosaic_period),
        headers=headers,
        timeout=120,
    )
    with Image.open(
        BytesIO(process_response_bytes(response, bounds, width, height))
    ) as source:
        mosaic = source.convert("RGB").resize(
            (width, height), Image.Resampling.LANCZOS
        )
    try:
        for y in rows:
            for x in columns:
                offset_x = (x - minimum_x) * 256
                offset_y = (y - minimum_y) * 256
                tile = mosaic.crop((offset_x, offset_y, offset_x + 256, offset_y + 256))
                try:
                    output = BytesIO()
                    tile.save(output, format=LOCAL_TILE_FORMAT, **LOCAL_TILE_SAVE_OPTIONS)
                    tile_store().put(tile_zoom, x, y, output.getvalue())
                finally:
                    tile.close()
    finally:
        mosaic.close()
    return 1


def copernicus_tile_grid_batches(
    tiles: list[tuple[int, int]],
) -> list[list[tuple[int, int]]]:
    """Partition a rectangular viewport into Process API output-sized tile grids."""

    maximum_tiles = MAXIMUM_PROCESS_OUTPUT_PIXELS // 256
    columns = sorted({x for x, _ in tiles})
    rows = sorted({y for _, y in tiles})
    if columns != list(range(columns[0], columns[-1] + 1)) or rows != list(
        range(rows[0], rows[-1] + 1)
    ):
        raise ValueError("Copernicus viewport tiles must be rectangular and contiguous")
    expected_tiles = {(x, y) for y in rows for x in columns}
    if set(tiles) != expected_tiles:
        raise ValueError("Copernicus viewport tiles must be rectangular and contiguous")
    return [
        [
            (x, y)
            for y in rows[row_start:row_start + maximum_tiles]
            for x in columns[column_start:column_start + maximum_tiles]
        ]
        for row_start in range(0, len(rows), maximum_tiles)
        for column_start in range(0, len(columns), maximum_tiles)
    ]


def download_viewport(
    configuration: CopernicusConfiguration,
    requested_zoom: int | None = None,
    resolution_label: str = "",
) -> str:
    if not configuration.client_id or not configuration.client_secret:
        raise ValueError("Copernicus client ID and client secret are required")
    viewport = viewport_from_configuration_service()
    zoom, tiles = viewport_tiles(viewport, requested_zoom)
    tile_grids = copernicus_tile_grid_batches(tiles)
    request_count = len(tile_grids)
    session = requests.Session()
    token_response = session.post(
        COPERNICUS_TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": configuration.client_id,
            "client_secret": configuration.client_secret,
        },
        timeout=30,
    )
    token_response.raise_for_status()
    access_token = token_response.json().get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise RuntimeError("Copernicus token response did not include an access token")
    headers = {"Authorization": f"Bearer {access_token}"}
    completed_requests = 0
    for index, tile_grid in enumerate(tile_grids, start=1):
        import_state.update(
            f"Downloading map area {index} of {len(tile_grids)} "
            f"({completed_requests + 1} of {request_count} Copernicus requests)."
        )
        completed_requests += download_copernicus_tile_grid(
            session, headers, zoom, tile_grid, configuration.mosaic_period
        )
    tile_store().set_revision()
    cached_raster_tile_bytes.cache_clear()
    message = (
        f"Downloaded {len(tiles)} Copernicus Global Mosaic tiles at zoom {zoom} "
        f"using {request_count} Copernicus requests."
    )
    if resolution_label:
        message = (
            f"Downloaded {len(tiles)} Copernicus Global Mosaic tiles at {resolution_label} "
            f"(zoom {zoom}) using {request_count} Copernicus requests."
        )
    if configuration.rebuild_pyramid_with_download:
        overview_tiles = compose_parent_tiles(COPERNICUS_DIRECTORY, zoom, set(tiles))
        message += f" Updated {overview_tiles} parent overview tiles."
    return message


def download_current_viewport(configuration: CopernicusConfiguration) -> str:
    return download_viewport(configuration)


def download_viewport_at_10m(configuration: CopernicusConfiguration) -> str:
    return download_viewport(
        configuration,
        requested_zoom=COPERNICUS_NATIVE_TILE_ZOOM,
        resolution_label="10m",
    )


def associated_file(source: Path, extensions: tuple[str, ...]) -> Path:
    for extension in extensions:
        candidate = source.with_suffix(extension)
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"associated file is missing for {source}: expected one of {extensions}")


def world_mercator_to_web_mercator(x: float, y: float) -> tuple[float, float]:
    longitude = x / EARTH_RADIUS_METRES * 180 / pi
    eccentricity = 0.08181919084262149
    latitude_radians = pi / 2 - 2 * atan(exp(-y / EARTH_RADIUS_METRES))
    for _ in range(8):
        eccentric_sine = eccentricity * sin(latitude_radians)
        latitude_radians = pi / 2 - 2 * atan(
            exp(-y / EARTH_RADIUS_METRES)
            * ((1 - eccentric_sine) / (1 + eccentric_sine)) ** (eccentricity / 2)
        )
    latitude = latitude_radians * 180 / pi
    return (
        longitude / 180 * WEB_MERCATOR_HALF_WORLD,
        log(tan(pi / 4 + latitude * pi / 360)) / pi * WEB_MERCATOR_HALF_WORLD,
    )


def web_mercator_to_world_mercator(x: float, y: float) -> tuple[float, float]:
    longitude = x / WEB_MERCATOR_HALF_WORLD * 180
    latitude = atan(sinh(y / WEB_MERCATOR_HALF_WORLD * pi)) * 180 / pi
    eccentricity = 0.08181919084262149
    latitude_radians = latitude * pi / 180
    return (
        longitude * pi / 180 * EARTH_RADIUS_METRES,
        EARTH_RADIUS_METRES * log(
            tan(pi / 4 + latitude_radians / 2)
            * ((1 - eccentricity * sin(latitude_radians)) / (1 + eccentricity * sin(latitude_radians))) ** (eccentricity / 2)
        ),
    )


def compose_parent_level(
    directory: Path, child_zoom: int, child_tiles: set[tuple[int, int]]
) -> set[tuple[int, int]]:
    store = tile_store(directory)
    parent_zoom = child_zoom - 1
    parent_tiles = {(x // 2, y // 2) for x, y in child_tiles}
    for parent_x, parent_y in parent_tiles:
            existing_payload = store.get(parent_zoom, parent_x, parent_y)
            if existing_payload is not None:
                with Image.open(BytesIO(existing_payload)) as existing:
                    parent = existing.convert("RGBA")
            else:
                parent = Image.new("RGBA", (256, 256))
            for child_x, child_y in child_tiles:
                if child_x // 2 != parent_x or child_y // 2 != parent_y:
                    continue
                child_payload = store.get(child_zoom, child_x, child_y)
                if child_payload is None:
                    continue
                with Image.open(BytesIO(child_payload)) as source:
                    child = source.convert("RGBA").resize((128, 128), Image.Resampling.LANCZOS)
                parent.alpha_composite(child, ((child_x % 2) * 128, (child_y % 2) * 128))
                child.close()
            output = BytesIO()
            parent.save(output, format=LOCAL_TILE_FORMAT, **LOCAL_TILE_SAVE_OPTIONS)
            store.put(parent_zoom, parent_x, parent_y, output.getvalue())
            parent.close()
    return parent_tiles


def compose_parent_tiles(
    directory: Path, native_zoom: int, native_tiles: set[tuple[int, int]]
) -> int:
    """Build transparent ancestors so imported imagery is visible below its native zoom."""

    child_tiles = native_tiles
    written_tiles = 0
    for child_zoom in range(native_zoom, 0, -1):
        child_tiles = compose_parent_level(directory, child_zoom, child_tiles)
        written_tiles += len(child_tiles)
    return written_tiles


def tile_coordinates(directory: Path, zoom: int) -> set[tuple[int, int]]:
    return tile_store(directory).coordinates(zoom)


def rebuild_overview_tiles(directory: Path) -> int:
    zooms = tile_store(directory).zooms()
    written_tiles = 0
    for zoom in range(max(zooms, default=0), 0, -1):
        child_tiles = tile_coordinates(directory, zoom)
        if child_tiles:
            written_tiles += len(compose_parent_level(directory, zoom, child_tiles))
    return written_tiles


def import_georeferenced_png(filename: str, rebuild_pyramid: bool = True) -> str:
    source = Path(filename).expanduser().resolve()
    if source.suffix.lower() != ".png":
        raise ValueError("satellite source must be a PNG file")
    if not source.is_file():
        raise FileNotFoundError(f"satellite source is missing: {source}")
    projection = associated_file(source, (".prj",))
    world_file = associated_file(source, (".pgw", ".pngw"))
    projection_text = projection.read_text(encoding="utf-8")
    if "Mercator_Auxiliary_Sphere" in projection_text:
        source_projection = "web-mercator"
    elif "WGS_84_World_Mercator" in projection_text:
        source_projection = "world-mercator"
    else:
        raise ValueError("PNG projection must be WGS 84 Web Mercator Auxiliary Sphere or WGS 84 World Mercator")
    values = [float(line) for line in world_file.read_text(encoding="utf-8").splitlines()]
    if len(values) != 6:
        raise ValueError("world file must contain six affine transform values")
    pixel_width, row_rotation, column_rotation, pixel_height, origin_x, origin_y = values
    if row_rotation != 0 or column_rotation != 0 or pixel_width <= 0 or pixel_height >= 0:
        raise ValueError("only north-up PNG files with a valid world file are supported")
    native_zoom = round(log2(2 * WEB_MERCATOR_HALF_WORLD / 256 / pixel_width))
    if not 0 <= native_zoom <= 18:
        raise ValueError("source PNG resolution is outside supported zoom levels")
    Image.MAX_IMAGE_PIXELS = None
    with Image.open(source) as image:
        source_image = image.convert("RGBA")
    source_left = origin_x - pixel_width / 2
    source_top = origin_y - pixel_height / 2
    source_right = source_left + source_image.width * pixel_width
    source_bottom = source_top + source_image.height * pixel_height
    if source_projection == "world-mercator":
        projected_corners = [
            world_mercator_to_web_mercator(source_x, source_y)
            for source_x, source_y in (
                (source_left, source_top),
                (source_left, source_bottom),
                (source_right, source_top),
                (source_right, source_bottom),
            )
        ]
        source_left_web, source_right_web = (
            min(coordinate[0] for coordinate in projected_corners),
            max(coordinate[0] for coordinate in projected_corners),
        )
        source_bottom_web, source_top_web = (
            min(coordinate[1] for coordinate in projected_corners),
            max(coordinate[1] for coordinate in projected_corners),
        )
    else:
        source_left_web, source_right_web = source_left, source_right
        source_bottom_web, source_top_web = source_bottom, source_top
    tiles_per_axis = 2**native_zoom
    minimum_x = max(0, floor((source_left_web + WEB_MERCATOR_HALF_WORLD) / (2 * WEB_MERCATOR_HALF_WORLD) * tiles_per_axis))
    maximum_x = min(tiles_per_axis - 1, ceil((source_right_web + WEB_MERCATOR_HALF_WORLD) / (2 * WEB_MERCATOR_HALF_WORLD) * tiles_per_axis) - 1)
    minimum_y = max(0, floor((WEB_MERCATOR_HALF_WORLD - source_top_web) / (2 * WEB_MERCATOR_HALF_WORLD) * tiles_per_axis))
    maximum_y = min(tiles_per_axis - 1, ceil((WEB_MERCATOR_HALF_WORLD - source_bottom_web) / (2 * WEB_MERCATOR_HALF_WORLD) * tiles_per_axis) - 1)
    tile_span = 2 * WEB_MERCATOR_HALF_WORLD / tiles_per_axis
    store = tile_store()
    written_tiles = 0
    native_tiles = set()
    for y in range(minimum_y, maximum_y + 1):
        for x in range(minimum_x, maximum_x + 1):
            tile_left = -WEB_MERCATOR_HALF_WORLD + x * tile_span
            tile_top = WEB_MERCATOR_HALF_WORLD - y * tile_span
            if source_projection == "world-mercator":
                def source_pixel(target_x: float, target_y: float) -> tuple[float, float]:
                    source_x, source_y = web_mercator_to_world_mercator(target_x, target_y)
                    return (
                        (source_x - source_left) / pixel_width,
                        (source_y - source_top) / pixel_height,
                    )
                patch = source_image.transform(
                    (256, 256),
                    Image.Transform.QUAD,
                    (
                        *source_pixel(tile_left, tile_top),
                        *source_pixel(tile_left, tile_top - tile_span),
                        *source_pixel(tile_left + tile_span, tile_top - tile_span),
                        *source_pixel(tile_left + tile_span, tile_top),
                    ),
                    Image.Resampling.BICUBIC,
                )
            else:
                crop = (
                    (tile_left - source_left) / pixel_width,
                    (tile_top - source_top) / pixel_height,
                    (tile_left + tile_span - source_left) / pixel_width,
                    (tile_top - tile_span - source_top) / pixel_height,
                )
                patch = source_image.crop(crop).resize((256, 256), Image.Resampling.LANCZOS)
            existing_payload = store.get(native_zoom, x, y)
            if existing_payload is not None:
                with Image.open(BytesIO(existing_payload)) as existing:
                    tile = existing.convert("RGBA")
                tile.alpha_composite(patch)
                patch.close()
            else:
                tile = patch
            output = BytesIO()
            tile.save(output, format=LOCAL_TILE_FORMAT, **LOCAL_TILE_SAVE_OPTIONS)
            store.put(native_zoom, x, y, output.getvalue())
            tile.close()
            native_tiles.add((x, y))
            written_tiles += 1
    source_image.close()
    ancestor_tiles = (
        compose_parent_tiles(COPERNICUS_DIRECTORY, native_zoom, native_tiles)
        if rebuild_pyramid
        else 0
    )
    store.set_revision()
    cached_raster_tile_bytes.cache_clear()
    message = f"Imported {written_tiles} satellite tiles at zoom {native_zoom}."
    if rebuild_pyramid:
        message += f" Updated {ancestor_tiles} impacted lower-zoom tiles."
    return message


def child_tile_from_parent(parent: Image.Image, x: int, y: int) -> Image.Image:
    width, height = parent.size
    child_x = x % 2
    child_y = y % 2
    return parent.crop(
        (
            child_x * width / 2,
            child_y * height / 2,
            (child_x + 1) * width / 2,
            (child_y + 1) * height / 2,
        )
    ).resize((256, 256), Image.Resampling.LANCZOS)


def raster_tile_image(directory: Path, zoom: int, x: int, y: int) -> Image.Image | None:
    store = tile_store(directory)
    tile_payload = store.get(zoom, x, y)
    if tile_payload is not None:
        with Image.open(BytesIO(tile_payload)) as source:
            tile = source.convert("RGBA")
        if tile.getchannel("A").getextrema() == (255, 255) or zoom == 0:
            return tile
        parent = raster_tile_image(directory, zoom - 1, x // 2, y // 2)
        if parent is None:
            return tile
        background = child_tile_from_parent(parent, x, y)
        background.alpha_composite(tile)
        tile.close()
        parent.close()
        return background
    if zoom == 0:
        return None
    parent = raster_tile_image(directory, zoom - 1, x // 2, y // 2)
    if parent is None:
        return None
    tile = child_tile_from_parent(parent, x, y)
    parent.close()
    return tile


def raster_tile_bytes(directory: Path, zoom: int, x: int, y: int) -> bytes | None:
    tile = raster_tile_image(directory, zoom, x, y)
    if tile is None:
        return None
    output = BytesIO()
    tile.save(output, format=LOCAL_TILE_FORMAT, **LOCAL_TILE_SAVE_OPTIONS)
    tile.close()
    return output.getvalue()


@lru_cache(maxsize=512)
def cached_raster_tile_bytes(zoom: int, x: int, y: int) -> bytes | None:
    return raster_tile_bytes(COPERNICUS_DIRECTORY, zoom, x, y)


app = FastAPI(title="Kody Copernicus Service", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_methods=["GET", "PUT", "POST"],
    allow_headers=["Content-Type", "If-Match"],
)
configuration_store = ConfigurationStore()
import_state = ImportState()


@app.get("/api/raster/revision")
def get_raster_revision() -> dict[str, str]:
    return {"revision": tile_store().revision()}


@app.get("/api/raster/{zoom}/{x}/{y}")
def get_raster_tile(zoom: int, x: int, y: int) -> Response:
    if not 0 <= zoom <= 18 or not 0 <= x < 2**zoom or not 0 <= y < 2**zoom:
        raise HTTPException(status_code=422, detail="invalid raster tile coordinates")
    tile = cached_raster_tile_bytes(zoom, x, y)
    if tile is not None:
        return Response(content=tile, media_type="image/webp")
    raise HTTPException(status_code=404, detail="local raster tile is unavailable")


@app.get("/api/kody/manifest")
def get_manifest() -> dict[str, object]:
    return {
        "kodyApiVersion": "1",
        "serviceId": "copernicus",
        "displayName": "Copernicus",
        "configuration": {
            "readPath": "/api/config",
            "writePath": "/api/config",
            "fields": [
                {
                    "key": "clientId",
                    "label": "Copernicus client ID",
                    "type": "string",
                    "required": True,
                },
                {
                    "key": "clientSecret",
                    "label": "Copernicus client secret",
                    "type": "password",
                    "required": False,
                    "secretStoredKey": "clientSecretSaved",
                },
                {
                    "key": "mosaicPeriod",
                    "label": "Quarterly mosaic",
                    "type": "select",
                    "optionsPath": "/api/mosaics",
                    "required": True,
                },
                {
                    "key": "rebuildPyramidWithDownload",
                    "label": "Rebuild tile pyramid after download",
                    "type": "boolean",
                },
            ],
            "actions": [
                {
                    "label": "Download current viewport",
                    "path": "/api/actions/download-current-viewport",
                    "statusPath": "/api/actions/download-current-viewport",
                    "confirmationPath": "/api/actions/assess-current-viewport",
                    "method": "POST",
                },
                {
                    "label": "Download Viewport At 10m",
                    "path": "/api/actions/download-viewport-at-10m",
                    "statusPath": "/api/actions/download-viewport-at-10m",
                    "confirmationPath": "/api/actions/assess-viewport-at-10m",
                    "method": "POST",
                },
                {
                    "label": "Rebuild Copernicus tile pyramid",
                    "path": "/api/actions/build-overview-tiles",
                    "statusPath": "/api/actions/build-overview-tiles",
                    "method": "POST",
                },
                {
                    "label": "Clear local data",
                    "path": "/api/actions/clear-local-data",
                    "method": "POST",
                    "confirmationMessage": "Clear all downloaded Copernicus tiles? This cannot be undone.",
                },
            ],
        },
    }


@app.get("/api/config")
def get_configuration() -> dict[str, object]:
    return configuration_store.response()


@app.put("/api/config")
def update_configuration(
    configuration: CopernicusConfiguration,
    if_match: str | None = Header(default=None),
) -> dict[str, object]:
    return configuration_store.update(configuration, if_match)


@app.get("/api/mosaics")
def get_mosaics() -> dict[str, list[dict[str, str]]]:
    try:
        return {"options": copernicus_mosaic_options(configuration_store.configuration)}
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except requests.RequestException as error:
        raise HTTPException(status_code=502, detail=f"could not query Copernicus mosaics: {error}") from error


@app.get("/api/actions/assess-current-viewport")
def assess_current_viewport() -> dict[str, object]:
    return assess_viewport_download()


def assess_viewport_download(requested_zoom: int | None = None) -> dict[str, object]:
    zoom, tiles = viewport_tiles(viewport_from_configuration_service(), requested_zoom)
    request_count = len(copernicus_tile_grid_batches(tiles))
    return {
        "tileCount": len(tiles),
        "requestCount": request_count,
        "zoom": zoom,
        "requiresConfirmation": request_count > MAXIMUM_UNCONFIRMED_DOWNLOAD_TILES,
        "message": (
            f"The current viewport needs {len(tiles)} local map tiles from "
            f"{request_count} Copernicus requests at zoom {zoom}. "
            + (
                "Continue with this download?"
                if request_count > MAXIMUM_UNCONFIRMED_DOWNLOAD_TILES
                else "Download now?"
            )
        ),
    }


@app.get("/api/actions/assess-viewport-at-10m")
def assess_viewport_at_10m() -> dict[str, object]:
    assessment = assess_viewport_download(COPERNICUS_NATIVE_TILE_ZOOM)
    assessment["message"] = (
        f"The current viewport needs {assessment['tileCount']} local 10m map tiles from "
        f"{assessment['requestCount']} Copernicus requests at zoom "
        f"{COPERNICUS_NATIVE_TILE_ZOOM}. "
        + (
            "Continue with this download?"
            if assessment["requiresConfirmation"]
            else "Download now?"
        )
    )
    return assessment


@app.post("/api/actions/download-current-viewport", status_code=202)
def download_current_viewport_action(background_tasks: BackgroundTasks) -> dict[str, str]:
    configuration = configuration_store.configuration
    if not configuration.client_id or not configuration.client_secret:
        raise HTTPException(status_code=422, detail="Copernicus client ID and client secret are required")
    zoom, tiles = viewport_tiles(viewport_from_configuration_service())
    request_count = len(copernicus_tile_grid_batches(tiles))
    import_state.start(
        f"Downloading {len(tiles)} map tiles with {request_count} Copernicus requests at zoom {zoom}."
    )

    def process() -> None:
        try:
            import_state.complete(download_current_viewport(configuration))
        except Exception as error:
            import_state.fail(error)

    background_tasks.add_task(process)
    return {
        "message": (
            f"Downloading {len(tiles)} map tiles with {request_count} Copernicus requests at zoom {zoom}."
        )
    }


@app.get("/api/actions/download-current-viewport")
def get_download_status() -> dict[str, str]:
    return import_state.response()


@app.post("/api/actions/download-viewport-at-10m", status_code=202)
def download_viewport_at_10m_action(
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    configuration = configuration_store.configuration
    if not configuration.client_id or not configuration.client_secret:
        raise HTTPException(status_code=422, detail="Copernicus client ID and client secret are required")
    zoom, tiles = viewport_tiles(
        viewport_from_configuration_service(), COPERNICUS_NATIVE_TILE_ZOOM
    )
    request_count = len(copernicus_tile_grid_batches(tiles))
    import_state.start(
        f"Downloading {len(tiles)} 10m map tiles with {request_count} "
        f"Copernicus requests at zoom {zoom}."
    )

    def process() -> None:
        try:
            import_state.complete(download_viewport_at_10m(configuration))
        except Exception as error:
            import_state.fail(error)

    background_tasks.add_task(process)
    return {
        "message": (
            f"Downloading {len(tiles)} 10m map tiles with {request_count} "
            f"Copernicus requests at zoom {zoom}."
        )
    }


@app.get("/api/actions/download-viewport-at-10m")
def get_10m_download_status() -> dict[str, str]:
    return import_state.response()


@app.post("/api/actions/build-overview-tiles", status_code=202)
def build_overview_tiles(background_tasks: BackgroundTasks) -> dict[str, str]:
    import_state.start("Rebuilding lower-zoom Copernicus tile pyramid.")

    def process() -> None:
        try:
            count = rebuild_overview_tiles(COPERNICUS_DIRECTORY)
            tile_store().set_revision()
            cached_raster_tile_bytes.cache_clear()
            import_state.complete(f"Built {count} Copernicus parent overview tiles.")
        except Exception as error:
            import_state.fail(error)

    background_tasks.add_task(process)
    return {"message": "Rebuilding the Copernicus tile pyramid."}


@app.get("/api/actions/build-overview-tiles")
def get_build_overview_tiles_status() -> dict[str, str]:
    return import_state.response()


@app.post("/api/actions/clear-local-data")
def clear_local_data() -> dict[str, str]:
    import_state.assert_idle()
    shutil.rmtree(COPERNICUS_DIRECTORY, ignore_errors=True)
    initialize_data_directory()
    cached_raster_tile_bytes.cache_clear()
    return {"message": "Cleared local Copernicus data."}
