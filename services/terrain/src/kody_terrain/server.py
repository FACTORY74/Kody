"""Offline ArcASCII terrain import and coloured raster tile service."""

from __future__ import annotations

from array import array
import gzip
import json
import hashlib
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from math import asin, asinh, atan, atan2, ceil, cos, floor, isnan, log, pi, sin, sinh, tan
from pathlib import Path
from threading import Lock, RLock, Thread
from typing import Callable, Literal
from urllib.error import URLError
from urllib.request import urlopen

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel, Field, model_validator

SourceProjection = Literal["auto", "geographic", "web-mercator", "world-mercator"]

ROOT_DIRECTORY = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get("KODY_DATA_DIRECTORY", ROOT_DIRECTORY / "data"))
IMPORTS_DIRECTORY = DATA_ROOT / "terrain" / "imports"
TERRAIN_DIRECTORY = DATA_ROOT / "terrain"
CONFIGURATION_SERVICE_URL = "http://127.0.0.1:8001/api/context/viewport"
MAX_MERCATOR_LATITUDE = 85.05112878
WEB_MERCATOR_HALF_WORLD = 20037508.342789244
MAX_TERRAIN_IMAGE_PIXELS = 500_000_000
MAX_LIVE_LOOKUP_SOURCE_BYTES = 128 * 1024 * 1024
RASTER_RENDERING_REVISION = b"terrain-raster-v3-nearest-no-data-coverage"
NUMERIC_TERRAIN_TILE_SIZE = 256
EARTH_RADIUS_METRES = 6_371_008.8
IMAGE_OPEN_LOCK = Lock()
TERRAIN_PALETTE = (
    (-100, (29, 87, 55)),
    (0, (91, 143, 67)),
    (250, (184, 170, 93)),
    (750, (151, 111, 74)),
    (1500, (241, 241, 237)),
)


def packages_directory() -> Path:
    return TERRAIN_DIRECTORY / "packages"


def package_manifest_path(package_directory: Path) -> Path:
    return package_directory / "manifest.json"


def initialize_data_directory() -> None:
    IMPORTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    packages_directory().mkdir(parents=True, exist_ok=True)


initialize_data_directory()


@dataclass(frozen=True)
class ElevationGrid:
    ncols: int
    nrows: int
    west: float
    south: float
    cellsize: float
    nodata: float | None
    values: tuple[float, ...]
    projection: Literal["geographic", "web-mercator", "world-mercator"] = "geographic"

    @property
    def east(self) -> float:
        return self.west + self.ncols * self.cellsize

    @property
    def north(self) -> float:
        return self.south + self.nrows * self.cellsize

    def cropped(self, west: float, south: float, east: float, north: float) -> "ElevationGrid":
        if self.projection == "web-mercator":
            west, south = lon_lat_to_mercator(west, south)
            east, north = lon_lat_to_mercator(east, north)
        elif self.projection == "world-mercator":
            west, south = lon_lat_to_world_mercator(west, south)
            east, north = lon_lat_to_world_mercator(east, north)
        left = max(0, floor((west - self.west) / self.cellsize))
        right = min(self.ncols, ceil((east - self.west) / self.cellsize))
        top = max(0, floor((self.north - north) / self.cellsize))
        bottom = min(self.nrows, ceil((self.north - south) / self.cellsize))
        if left >= right or top >= bottom:
            raise ValueError("the local terrain source does not cover the current viewport")
        values = tuple(
            self.values[row * self.ncols + column]
            for row in range(top, bottom)
            for column in range(left, right)
        )
        return ElevationGrid(
            right - left,
            bottom - top,
            self.west + left * self.cellsize,
            self.north - bottom * self.cellsize,
            self.cellsize,
            self.nodata,
            values,
            self.projection,
        )


class TerrainConfiguration(BaseModel):
    source_filename: str = Field(default="", alias="sourceFilename")
    source_projection: SourceProjection = Field(default="auto", alias="sourceProjection")
    show_palette: bool = Field(default=False, alias="showPalette")
    circle_increments: int = Field(default=72, alias="circleIncrements", ge=4, le=360)
    distance_increment_metres: int = Field(
        default=250, alias="distanceIncrementMetres", ge=10, le=10_000
    )
    distance_limit_metres: int = Field(
        default=20_000, alias="distanceLimitMetres", ge=10, le=500_000
    )
    observer_height_metres: float = Field(
        default=2, alias="observerHeightMetres", ge=0, le=100
    )

    @model_validator(mode="after")
    def validate_los_distances(self) -> "TerrainConfiguration":
        if self.distance_limit_metres < self.distance_increment_metres:
            raise ValueError("distanceLimitMetres must be at least distanceIncrementMetres")
        return self


class LosRosetteRequest(BaseModel):
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-MAX_MERCATOR_LATITUDE, le=MAX_MERCATOR_LATITUDE)


class ConfigurationStore:
    def __init__(self) -> None:
        self.configuration = TerrainConfiguration()
        self.lock = RLock()

    def revision(self) -> str:
        return str(hash(self.configuration.model_dump_json(by_alias=True)))

    def response(self) -> dict[str, object]:
        with self.lock:
            return {"revision": self.revision(), **self.configuration.model_dump(by_alias=True)}

    def update(self, configuration: TerrainConfiguration, expected_revision: str | None) -> dict[str, object]:
        with self.lock:
            if expected_revision is not None and expected_revision != self.revision():
                raise HTTPException(status_code=409, detail="configuration revision is stale")
            self.configuration = configuration
            return self.response()


class ImportState:
    def __init__(self) -> None:
        self.lock = Lock()
        self.status: Literal["idle", "processing", "succeeded", "failed"] = "idle"
        self.message = ""
        self.progress = 0

    def start(self, message: str) -> None:
        with self.lock:
            if self.status == "processing":
                raise HTTPException(status_code=409, detail="terrain import is already running")
            self.status, self.message, self.progress = "processing", message, 0

    def update(self, message: str, progress: int) -> None:
        with self.lock:
            self.message, self.progress = message, progress

    def complete(self, message: str) -> None:
        with self.lock:
            self.status, self.message, self.progress = "succeeded", message, 100

    def fail(self, error: Exception) -> None:
        with self.lock:
            self.status, self.message = "failed", str(error)

    def response(self) -> dict[str, object]:
        with self.lock:
            return {"status": self.status, "message": self.message, "progress": self.progress}


class LosRosetteState:
    """Owns the one background LOS computation and its current GeoJSON result."""

    def __init__(self) -> None:
        self.lock = Lock()
        self.thread: Thread | None = None
        self.status: Literal["idle", "processing", "succeeded", "failed"] = "idle"
        self.message = ""
        self.progress = 0
        self.collection: dict[str, object] = {"type": "FeatureCollection", "features": []}

    def start(self, request: LosRosetteRequest, configuration: TerrainConfiguration) -> None:
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                raise HTTPException(status_code=409, detail="an LOS Rosette calculation is already running")
            self.status = "processing"
            self.message = "Sampling terrain line of sight."
            self.progress = 0
            self.thread = Thread(
                target=self._create,
                args=(request, configuration),
                name="terrain-los-rosette",
                daemon=True,
            )
            self.thread.start()

    def _create(self, request: LosRosetteRequest, configuration: TerrainConfiguration) -> None:
        try:
            collection = create_los_rosette(
                request.longitude,
                request.latitude,
                configuration,
                self.update,
            )
        except Exception as error:
            with self.lock:
                self.status = "failed"
                self.message = str(error)
            return
        with self.lock:
            self.collection = collection
            self.status = "succeeded"
            self.message = "LOS Rosette created."
            self.progress = 100

    def update(self, progress: int) -> None:
        with self.lock:
            self.progress = progress

    def clear(self) -> None:
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                raise HTTPException(status_code=409, detail="an LOS Rosette calculation is already running")
            self.collection = {"type": "FeatureCollection", "features": []}
            self.status = "idle"
            self.message = "LOS Rosette cleared."
            self.progress = 0

    def response(self) -> dict[str, object]:
        with self.lock:
            return {"status": self.status, "message": self.message, "progress": self.progress}

    def result(self) -> dict[str, object]:
        with self.lock:
            return self.collection


def import_source(filename: str) -> Path:
    if not filename:
        raise ValueError("sourceFilename is required; place an .asc or .grd file in the terrain imports directory")
    IMPORTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    configured_source = Path(filename).expanduser()
    source = (
        configured_source.resolve()
        if configured_source.is_absolute()
        else (IMPORTS_DIRECTORY / configured_source).resolve()
    )
    if (
        source.suffix.lower() not in {".asc", ".grd", ".tif", ".tiff"}
        or (
            not configured_source.is_absolute()
            and source.parent != IMPORTS_DIRECTORY.resolve()
        )
    ):
        raise ValueError(
            "sourceFilename must name an ArcASCII or GeoTIFF file directly in the "
            "terrain imports directory, or an existing absolute path"
        )
    if not source.is_file():
        raise FileNotFoundError(f"terrain source is missing: {source}")
    return source


def parse_arc_ascii(
    source: Path, source_projection: SourceProjection = "auto"
) -> ElevationGrid:
    headers: dict[str, str] = {}
    data_lines: list[str] = []
    header_names = {"ncols", "nrows", "xllcorner", "xllcenter", "yllcorner", "yllcenter", "cellsize", "nodata_value"}
    for line in source.read_text(encoding="utf-8-sig").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].lower() in header_names and not data_lines:
            headers[parts[0].lower()] = parts[1]
        elif line.strip():
            data_lines.append(line)
    required = {"ncols", "nrows", "cellsize"}
    if not required <= headers.keys() or not ({"xllcorner", "xllcenter"} & headers.keys()) or not ({"yllcorner", "yllcenter"} & headers.keys()):
        raise ValueError("ArcASCII header requires ncols, nrows, cellsize, and xllcorner/xllcenter with yllcorner/yllcenter")
    ncols, nrows = int(headers["ncols"]), int(headers["nrows"])
    cellsize = float(headers["cellsize"])
    if ncols <= 0 or nrows <= 0 or cellsize <= 0:
        raise ValueError("ArcASCII ncols, nrows, and cellsize must be positive")
    west = float(headers.get("xllcorner", headers.get("xllcenter", "0")))
    south = float(headers.get("yllcorner", headers.get("yllcenter", "0")))
    if "xllcenter" in headers:
        west -= cellsize / 2
    if "yllcenter" in headers:
        south -= cellsize / 2
    east, north = west + ncols * cellsize, south + nrows * cellsize
    has_geographic_bounds = (
        -180 <= west <= 180
        and -180 <= east <= 180
        and -90 <= south <= 90
        and -90 <= north <= 90
    )
    has_projected_bounds = all(
        -WEB_MERCATOR_HALF_WORLD <= coordinate <= WEB_MERCATOR_HALF_WORLD
        for coordinate in (west, east, south, north)
    )
    if source_projection == "auto":
        if has_geographic_bounds:
            projection: Literal["geographic", "web-mercator", "world-mercator"] = "geographic"
        elif has_projected_bounds:
            projection = "web-mercator"
        else:
            raise ValueError(
                "ArcASCII coordinates must be geographic WGS 84 degrees or projected Mercator metres"
            )
    elif source_projection == "geographic":
        if not has_geographic_bounds:
            raise ValueError("ArcASCII geographic coordinates must be WGS 84 longitude and latitude degrees")
        projection = source_projection
    else:
        if not has_projected_bounds:
            raise ValueError("ArcASCII projected coordinates must be within the supported Mercator extent")
        projection = source_projection
    values = tuple(float(value) for line in data_lines for value in line.split())
    if len(values) != ncols * nrows:
        raise ValueError(f"ArcASCII grid has {len(values)} values; expected {ncols * nrows}")
    return ElevationGrid(ncols, nrows, west, south, cellsize, float(headers["nodata_value"]) if "nodata_value" in headers else None, values, projection)


def parse_arc_ascii_header(
    source: Path, source_projection: SourceProjection
) -> ElevationGrid:
    headers: dict[str, str] = {}
    header_names = {
        "ncols",
        "nrows",
        "xllcorner",
        "xllcenter",
        "yllcorner",
        "yllcenter",
        "cellsize",
        "nodata_value",
    }
    with source.open(encoding="utf-8-sig") as input_file:
        for line in input_file:
            parts = line.split()
            if len(parts) >= 2 and parts[0].lower() in header_names:
                headers[parts[0].lower()] = parts[1]
            elif line.strip():
                break
    required = {"ncols", "nrows", "cellsize"}
    if not required <= headers.keys() or not ({"xllcorner", "xllcenter"} & headers.keys()) or not ({"yllcorner", "yllcenter"} & headers.keys()):
        raise ValueError("ArcASCII header requires ncols, nrows, cellsize, and xllcorner/xllcenter with yllcorner/yllcenter")
    ncols, nrows = int(headers["ncols"]), int(headers["nrows"])
    cellsize = float(headers["cellsize"])
    west = float(headers.get("xllcorner", headers.get("xllcenter", "0")))
    south = float(headers.get("yllcorner", headers.get("yllcenter", "0")))
    if "xllcenter" in headers:
        west -= cellsize / 2
    if "yllcenter" in headers:
        south -= cellsize / 2
    if source_projection == "geographic":
        projection: Literal["geographic", "web-mercator", "world-mercator"] = "geographic"
    elif source_projection in {"web-mercator", "world-mercator"}:
        projection = source_projection
    else:
        raise ValueError("ArcASCII numeric terrain chunks require an explicit resolved projection")
    return ElevationGrid(
        ncols,
        nrows,
        west,
        south,
        cellsize,
        float(headers["nodata_value"]) if "nodata_value" in headers else None,
        (),
        projection,
    )


def parse_geotiff(source: Path) -> ElevationGrid:
    with IMAGE_OPEN_LOCK:
        previous_limit = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = MAX_TERRAIN_IMAGE_PIXELS
        try:
            with Image.open(source) as image:
                if image.width * image.height > MAX_TERRAIN_IMAGE_PIXELS:
                    raise ValueError(f"GeoTIFF has {image.width * image.height} pixels; terrain imports are limited to {MAX_TERRAIN_IMAGE_PIXELS}")
                scale = image.tag_v2.get(33550)
                tie_point = image.tag_v2.get(33922)
                geo_keys = image.tag_v2.get(34735)
                if not scale or not tie_point or not geo_keys or len(scale) < 2 or len(tie_point) < 6:
                    raise ValueError("GeoTIFF requires pixel scale, tie point, and CRS metadata")
                key_values = dict(zip(geo_keys[4::4], geo_keys[7::4]))
                epsg = key_values.get(3072)
                if epsg not in {3857, 3395}:
                    raise ValueError("GeoTIFF CRS must be EPSG:3857 or EPSG:3395")
                nodata = image.tag_v2.get(42113)
                return ElevationGrid(
                    image.width,
                    image.height,
                    float(tie_point[3]) - float(tie_point[0]) * float(scale[0]),
                    float(tie_point[4]) - image.height * float(scale[1]) + float(tie_point[1]) * float(scale[1]),
                    float(scale[0]),
                    float(nodata) if nodata is not None else None,
                    tuple(float(value) for value in image.getdata()),
                    "web-mercator" if epsg == 3857 else "world-mercator",
                )
        finally:
            Image.MAX_IMAGE_PIXELS = previous_limit


def lon_to_tile_x(longitude: float, zoom: int) -> float:
    return (longitude + 180) / 360 * 2**zoom


def lat_to_tile_y(latitude: float, zoom: int) -> float:
    latitude = max(-MAX_MERCATOR_LATITUDE, min(MAX_MERCATOR_LATITUDE, latitude))
    return (1 - asinh(tan(latitude * pi / 180)) / pi) / 2 * 2**zoom


def tile_lon_lat(zoom: int, x: int, y: int, pixel_x: int, pixel_y: int) -> tuple[float, float]:
    size = 2**zoom * 256
    longitude = (x * 256 + pixel_x + 0.5) / size * 360 - 180
    mercator = pi * (1 - 2 * (y * 256 + pixel_y + 0.5) / size)
    return longitude, atan(sinh(mercator)) * 180 / pi


def lon_lat_to_mercator(longitude: float, latitude: float) -> tuple[float, float]:
    latitude = max(-MAX_MERCATOR_LATITUDE, min(MAX_MERCATOR_LATITUDE, latitude))
    return (
        longitude / 180 * WEB_MERCATOR_HALF_WORLD,
        log(tan(pi / 4 + latitude * pi / 360)) / pi * WEB_MERCATOR_HALF_WORLD,
    )


def lon_lat_to_world_mercator(longitude: float, latitude: float) -> tuple[float, float]:
    latitude = max(-MAX_MERCATOR_LATITUDE, min(MAX_MERCATOR_LATITUDE, latitude))
    eccentricity = 0.08181919084262149
    latitude_radians = latitude * pi / 180
    return (
        longitude * pi / 180 * 6378137,
        6378137 * log(
            tan(pi / 4 + latitude_radians / 2)
            * ((1 - eccentricity * sin(latitude_radians)) / (1 + eccentricity * sin(latitude_radians))) ** (eccentricity / 2)
        ),
    )


def terrain_colour(value: float) -> tuple[int, int, int, int]:
    if value <= TERRAIN_PALETTE[0][0]:
        red, green, blue = TERRAIN_PALETTE[0][1]
        return red, green, blue, 255
    if value >= TERRAIN_PALETTE[-1][0]:
        red, green, blue = TERRAIN_PALETTE[-1][1]
        return red, green, blue, 255
    for (lower_height, start), (upper_height, end) in zip(TERRAIN_PALETTE, TERRAIN_PALETTE[1:]):
        if lower_height <= value <= upper_height:
            fraction = (value - lower_height) / (upper_height - lower_height)
            break
    else:
        raise RuntimeError("terrain palette does not cover the elevation")
    red, green, blue = (
        round(start[index] + (end[index] - start[index]) * fraction)
        for index in range(3)
    )
    return red, green, blue, 255


def render_tile(
    grid: ElevationGrid, zoom: int, x: int, y: int
) -> Image.Image:
    image = Image.new("RGBA", (256, 256))
    pixels = image.load()
    assert pixels is not None
    for pixel_y in range(256):
        for pixel_x in range(256):
            longitude, latitude = tile_lon_lat(zoom, x, y, pixel_x, pixel_y)
            source_x, source_y = (
                lon_lat_to_mercator(longitude, latitude)
                if grid.projection == "web-mercator"
                else lon_lat_to_world_mercator(longitude, latitude)
                if grid.projection == "world-mercator"
                else (longitude, latitude)
            )
            column = floor((source_x - grid.west) / grid.cellsize)
            row = floor((grid.north - source_y) / grid.cellsize)
            if not 0 <= column < grid.ncols or not 0 <= row < grid.nrows:
                continue
            value = grid.values[row * grid.ncols + column]
            if grid.nodata is None or value != grid.nodata:
                pixels[pixel_x, pixel_y] = terrain_colour(value)
    return image


def downsample_terrain_tile(image: Image.Image) -> Image.Image:
    source = image.convert("RGBA")
    reduced = Image.new("RGBA", (128, 128))
    source_pixels = source.load()
    reduced_pixels = reduced.load()
    assert source_pixels is not None and reduced_pixels is not None
    for row in range(128):
        for column in range(128):
            for source_row, source_column in (
                (row * 2, column * 2),
                (row * 2, column * 2 + 1),
                (row * 2 + 1, column * 2),
                (row * 2 + 1, column * 2 + 1),
            ):
                pixel = source_pixels[source_column, source_row]
                if pixel[3]:
                    reduced_pixels[column, row] = pixel
                    break
    source.close()
    return reduced


def compose_parent_level(directory: Path, child_zoom: int, tiles: set[tuple[int, int]]) -> set[tuple[int, int]]:
    parents = {(x // 2, y // 2) for x, y in tiles}
    for parent_x, parent_y in parents:
        destination = directory / str(child_zoom - 1) / str(parent_x) / f"{parent_y}.png"
        parent = Image.new("RGBA", (256, 256))
        for child_x, child_y in tiles:
            if child_x // 2 == parent_x and child_y // 2 == parent_y:
                with Image.open(directory / str(child_zoom) / str(child_x) / f"{child_y}.png") as child:
                    reduced_child = downsample_terrain_tile(child)
                    parent.alpha_composite(reduced_child, ((child_x % 2) * 128, (child_y % 2) * 128))
                    reduced_child.close()
        destination.parent.mkdir(parents=True, exist_ok=True)
        parent.save(destination)
        parent.close()
    return parents


def geographic_bounds(grid: ElevationGrid) -> tuple[float, float, float, float]:
    world_span = 2 * WEB_MERCATOR_HALF_WORLD if grid.projection != "geographic" else 360
    if grid.projection == "web-mercator":
        west, south = grid.west / WEB_MERCATOR_HALF_WORLD * 180, atan(sinh(grid.south / WEB_MERCATOR_HALF_WORLD * pi)) * 180 / pi
        east, north = grid.east / WEB_MERCATOR_HALF_WORLD * 180, atan(sinh(grid.north / WEB_MERCATOR_HALF_WORLD * pi)) * 180 / pi
    elif grid.projection == "world-mercator":
        def inverse_world_mercator(x: float, y: float) -> tuple[float, float]:
            longitude = x / 6378137 * 180 / pi
            latitude = atan(sinh(y / 6378137)) * 180 / pi
            for _ in range(8):
                _, projected_y = lon_lat_to_world_mercator(longitude, latitude)
                latitude += (y - projected_y) / 111320
            return longitude, latitude
        west, south = inverse_world_mercator(grid.west, grid.south)
        east, north = inverse_world_mercator(grid.east, grid.north)
    else:
        west, south, east, north = grid.west, grid.south, grid.east, grid.north
    return west, south, east, north


def native_tile_zoom(grid: ElevationGrid) -> int:
    world_span = 2 * WEB_MERCATOR_HALF_WORLD if grid.projection != "geographic" else 360
    return max(0, min(18, round(log(world_span / (grid.cellsize * 256), 2))))


def generate_tiles(grid: ElevationGrid, directory: Path, state: ImportState | None = None) -> int:
    if directory.exists():
        shutil.rmtree(directory)
    zoom = native_tile_zoom(grid)
    west, south, east, north = geographic_bounds(grid)
    min_x, max_x = max(0, floor(lon_to_tile_x(west, zoom))), min(2**zoom - 1, ceil(lon_to_tile_x(east, zoom)) - 1)
    min_y, max_y = max(0, floor(lat_to_tile_y(north, zoom))), min(2**zoom - 1, ceil(lat_to_tile_y(south, zoom)) - 1)
    coordinates = [(x, y) for y in range(min_y, max_y + 1) for x in range(min_x, max_x + 1)]
    valid = [value for value in grid.values if grid.nodata is None or value != grid.nodata]
    if not valid:
        raise ValueError("terrain source contains no elevation values")
    for index, (x, y) in enumerate(coordinates, start=1):
        tile = render_tile(grid, zoom, x, y)
        destination = directory / str(zoom) / str(x) / f"{y}.png"
        destination.parent.mkdir(parents=True, exist_ok=True)
        tile.save(destination)
        tile.close()
        if state:
            state.update(f"Generating terrain tiles ({index}/{len(coordinates)}).", 20 + round(60 * index / len(coordinates)))
    children = set(coordinates)
    for overview_index, child_zoom in enumerate(range(zoom, 0, -1), start=1):
        children = compose_parent_level(directory, child_zoom, children)
        if state:
            state.update(
                f"Generating terrain overview tiles (z{child_zoom - 1}).",
                80 + round(20 * overview_index / zoom),
            )
    return len(coordinates)


def write_arc_ascii(grid: ElevationGrid, destination: Path) -> None:
    nodata = grid.nodata if grid.nodata is not None else -9999
    rows = (
        " ".join(str(value) for value in grid.values[row * grid.ncols:(row + 1) * grid.ncols])
        for row in range(grid.nrows)
    )
    destination.write_text(
        "\n".join((
            f"ncols {grid.ncols}",
            f"nrows {grid.nrows}",
            f"xllcorner {grid.west}",
            f"yllcorner {grid.south}",
            f"cellsize {grid.cellsize}",
            f"NODATA_value {nodata}",
            *rows,
            "",
        )),
        encoding="utf-8",
    )


def import_terrain(
    filename: str,
    viewport: dict[str, float] | None = None,
    state: ImportState | None = None,
    source_projection: SourceProjection = "auto",
) -> str:
    source = import_source(filename)
    if state:
        state.update("Parsing ArcASCII terrain grid.", 10)
    grid = (
        parse_geotiff(source)
        if source.suffix.lower() in {".tif", ".tiff"}
        else parse_arc_ascii(source, source_projection)
    )
    if viewport is not None:
        grid = grid.cropped(viewport["west"], viewport["south"], viewport["east"], viewport["north"])
    imported_at = datetime.now(UTC).isoformat()
    source_digest = hashlib.sha256(f"{source.resolve()}:{imported_at}".encode()).hexdigest()[:12]
    package_directory = packages_directory() / f"{source.stem.lower()}-{source_digest}"
    package_directory.mkdir(parents=True, exist_ok=False)
    write_arc_ascii(grid, package_directory / "source.asc")
    numeric_tile_count = write_numeric_tiles(grid, package_directory, state)
    west, south, east, north = geographic_bounds(grid)
    native_resolution_metres = grid.cellsize if grid.projection != "geographic" else grid.cellsize * 111320
    manifest = {
        "id": package_directory.name,
        "sourceFilename": source.name,
        "sourceProjection": grid.projection,
        "importedAt": imported_at,
        "bounds": {"west": west, "south": south, "east": east, "north": north},
        "nativeResolutionMetres": native_resolution_metres,
        "grid": {"ncols": grid.ncols, "nrows": grid.nrows, "cellsize": grid.cellsize, "projection": grid.projection},
        "numericTileSize": NUMERIC_TERRAIN_TILE_SIZE,
    }
    count = generate_tiles(grid, package_directory / "tiles", state)
    package_manifest_path(package_directory).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    cached_raster_tile_bytes.cache_clear()
    return (
        f"Imported {grid.ncols}×{grid.nrows} terrain cells as package {package_directory.name}, "
        f"with {numeric_tile_count} numeric chunks and {count} native tiles with overview pyramid."
    )


def build_missing_numeric_tiles(state: ImportState | None = None) -> str:
    packages = terrain_packages()
    rebuilt = 0
    for index, package in enumerate(packages, start=1):
        directory = Path(package["directory"])
        numeric_directory = directory / "numeric-tiles"
        if numeric_directory.is_dir() and any(numeric_directory.rglob("*.f32.gz")):
            continue
        grid_data = package.get("grid")
        if not isinstance(grid_data, dict):
            raise RuntimeError(f"terrain package {package['id']} has no grid metadata")
        projection = grid_data.get("projection")
        if projection not in {"geographic", "web-mercator", "world-mercator"}:
            raise RuntimeError(f"terrain package {package['id']} has an unsupported projection")
        if state:
            state.update(
                f"Reading numeric terrain source for {package['id']} ({index}/{len(packages)}).",
                5 + round(75 * (index - 1) / max(1, len(packages))),
            )
        grid = package_elevation_grid(directory, projection)
        write_numeric_tiles(grid, directory, state)
        manifest_path = package_manifest_path(directory)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["numericTileSize"] = NUMERIC_TERRAIN_TILE_SIZE
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        rebuilt += 1
    return f"Built numeric terrain chunks for {rebuilt} package(s)."


def rebuild_terrain_display_tiles(state: ImportState | None = None) -> str:
    packages = terrain_packages()
    rebuilt = 0
    for index, package in enumerate(packages, start=1):
        directory = Path(package["directory"])
        grid_data = package.get("grid")
        if not isinstance(grid_data, dict):
            raise RuntimeError(f"terrain package {package['id']} has no grid metadata")
        projection = grid_data.get("projection")
        if projection not in {"geographic", "web-mercator", "world-mercator"}:
            raise RuntimeError(f"terrain package {package['id']} has an unsupported projection")
        if state:
            state.update(
                f"Reading terrain source for display rebuild ({index}/{len(packages)}).",
                5 + round(75 * (index - 1) / max(1, len(packages))),
            )
        grid = package_elevation_grid(directory, projection)
        generate_tiles(grid, directory / "tiles", state)
        rebuilt += 1
    cached_raster_tile_bytes.cache_clear()
    return f"Rebuilt terrain display tiles for {rebuilt} package(s)."


def fetch_viewport() -> dict[str, float]:
    try:
        with urlopen(CONFIGURATION_SERVICE_URL, timeout=5) as response:
            return json.loads(response.read())
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError(f"current shared viewport is unavailable: {error}") from error


def terrain_packages() -> list[dict[str, object]]:
    packages: list[dict[str, object]] = []
    directory = packages_directory()
    for manifest_path in directory.glob("*/manifest.json") if directory.is_dir() else []:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest.get("nativeResolutionMetres"), (int, float)):
                raise ValueError("nativeResolutionMetres is missing")
            manifest["directory"] = manifest_path.parent
            packages.append(manifest)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError(f"invalid terrain package manifest {manifest_path}: {error}") from error
    return sorted(packages, key=lambda package: float(package["nativeResolutionMetres"]), reverse=True)


@lru_cache(maxsize=8)
def package_elevation_grid(directory: Path, projection: str) -> ElevationGrid:
    grid = parse_arc_ascii(directory / "source.asc")
    return ElevationGrid(
        grid.ncols,
        grid.nrows,
        grid.west,
        grid.south,
        grid.cellsize,
        grid.nodata,
        grid.values,
        projection,
    )


def numeric_tile_path(directory: Path, row_tile: int, column_tile: int) -> Path:
    return directory / "numeric-tiles" / str(row_tile) / f"{column_tile}.f32.gz"


def write_numeric_tiles(
    grid: ElevationGrid, directory: Path, state: ImportState | None = None
) -> int:
    numeric_directory = directory / "numeric-tiles"
    if numeric_directory.exists():
        shutil.rmtree(numeric_directory)
    row_tiles = ceil(grid.nrows / NUMERIC_TERRAIN_TILE_SIZE)
    column_tiles = ceil(grid.ncols / NUMERIC_TERRAIN_TILE_SIZE)
    tile_count = row_tiles * column_tiles
    for row_tile in range(row_tiles):
        for column_tile in range(column_tiles):
            values = array("f", [float("nan")]) * (
                NUMERIC_TERRAIN_TILE_SIZE * NUMERIC_TERRAIN_TILE_SIZE
            )
            row_start = row_tile * NUMERIC_TERRAIN_TILE_SIZE
            column_start = column_tile * NUMERIC_TERRAIN_TILE_SIZE
            for local_row in range(min(NUMERIC_TERRAIN_TILE_SIZE, grid.nrows - row_start)):
                source_start = (row_start + local_row) * grid.ncols + column_start
                source_values = grid.values[
                    source_start:source_start
                    + min(NUMERIC_TERRAIN_TILE_SIZE, grid.ncols - column_start)
                ]
                for local_column, value in enumerate(source_values):
                    if grid.nodata is None or value != grid.nodata:
                        values[local_row * NUMERIC_TERRAIN_TILE_SIZE + local_column] = value
            destination = numeric_tile_path(directory, row_tile, column_tile)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(destination, "wb", compresslevel=1) as output:
                output.write(values.tobytes())
            if state:
                completed = row_tile * column_tiles + column_tile + 1
                state.update(
                    f"Writing numeric terrain chunks ({completed}/{tile_count}).",
                    10 + round(10 * completed / tile_count),
                )
    return tile_count


def numeric_tile_elevation(
    package: dict[str, object], source_x: float, source_y: float
) -> float | None:
    grid_data = package.get("grid")
    if not isinstance(grid_data, dict):
        raise RuntimeError(f"terrain package {package['id']} has no grid metadata")
    ncols, nrows = int(grid_data["ncols"]), int(grid_data["nrows"])
    cellsize = float(grid_data["cellsize"])
    directory = Path(package["directory"])
    source = directory / "source.asc"
    source_header = parse_arc_ascii_header(source, str(grid_data["projection"]))
    west, north = source_header.west, source_header.north
    column = floor((source_x - west) / cellsize)
    row = floor((north - source_y) / cellsize)
    if not 0 <= column < ncols or not 0 <= row < nrows:
        return None
    path = numeric_tile_path(
        directory, row // NUMERIC_TERRAIN_TILE_SIZE, column // NUMERIC_TERRAIN_TILE_SIZE
    )
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rb") as input_file:
            values = array("f")
            values.frombytes(input_file.read())
    except (OSError, EOFError) as error:
        raise RuntimeError(f"numeric terrain chunk is unreadable: {path}") from error
    index = (
        (row % NUMERIC_TERRAIN_TILE_SIZE) * NUMERIC_TERRAIN_TILE_SIZE
        + column % NUMERIC_TERRAIN_TILE_SIZE
    )
    if len(values) != NUMERIC_TERRAIN_TILE_SIZE * NUMERIC_TERRAIN_TILE_SIZE:
        raise RuntimeError(f"numeric terrain chunk has an invalid size: {path}")
    return None if isnan(values[index]) else values[index]


def terrain_elevation(longitude: float, latitude: float) -> tuple[float, str] | None:
    for package in reversed(terrain_packages()):
        bounds = package.get("bounds")
        if not isinstance(bounds, dict):
            raise RuntimeError(f"terrain package {package['id']} has no bounds metadata")
        try:
            covers_point = (
                float(bounds["west"]) <= longitude <= float(bounds["east"])
                and float(bounds["south"]) <= latitude <= float(bounds["north"])
            )
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError(f"terrain package {package['id']} has invalid bounds metadata") from error
        if not covers_point:
            continue
        grid_data = package.get("grid")
        if not isinstance(grid_data, dict):
            raise RuntimeError(f"terrain package {package['id']} has no grid metadata")
        projection = grid_data.get("projection")
        if projection not in {"geographic", "web-mercator", "world-mercator"}:
            raise RuntimeError(f"terrain package {package['id']} has an unsupported projection")
        directory = Path(package["directory"])
        source = directory / "source.asc"
        source_x, source_y = (
            lon_lat_to_mercator(longitude, latitude)
            if projection == "web-mercator"
            else lon_lat_to_world_mercator(longitude, latitude)
            if projection == "world-mercator"
            else (longitude, latitude)
        )
        numeric_value = numeric_tile_elevation(package, source_x, source_y)
        if numeric_value is not None:
            return numeric_value, str(package["id"])
        if source.stat().st_size > MAX_LIVE_LOOKUP_SOURCE_BYTES:
            continue
        grid = package_elevation_grid(directory, projection)
        source_x, source_y = (
            lon_lat_to_mercator(longitude, latitude)
            if grid.projection == "web-mercator"
            else lon_lat_to_world_mercator(longitude, latitude)
            if grid.projection == "world-mercator"
            else (longitude, latitude)
        )
        column = floor((source_x - grid.west) / grid.cellsize)
        row = floor((grid.north - source_y) / grid.cellsize)
        if not 0 <= column < grid.ncols or not 0 <= row < grid.nrows:
            continue
        value = grid.values[row * grid.ncols + column]
        if grid.nodata is None or value != grid.nodata:
            return value, str(package["id"])
    return None


def destination_point(
    longitude: float, latitude: float, bearing_degrees: float, distance_metres: float
) -> tuple[float, float]:
    bearing = bearing_degrees * pi / 180
    angular_distance = distance_metres / EARTH_RADIUS_METRES
    start_latitude = latitude * pi / 180
    start_longitude = longitude * pi / 180
    destination_latitude = asin(
        sin(start_latitude) * cos(angular_distance)
        + cos(start_latitude) * sin(angular_distance) * cos(bearing)
    )
    destination_longitude = start_longitude + atan2(
        sin(bearing) * sin(angular_distance) * cos(start_latitude),
        cos(angular_distance) - sin(start_latitude) * sin(destination_latitude),
    )
    return (
        (destination_longitude * 180 / pi + 540) % 360 - 180,
        destination_latitude * 180 / pi,
    )


def create_los_rosette(
    longitude: float,
    latitude: float,
    configuration: TerrainConfiguration,
    update_progress: Callable[[int], None] | None = None,
) -> dict[str, object]:
    origin = terrain_elevation(longitude, latitude)
    if origin is None:
        raise ValueError("the selected point is outside locally available terrain")
    origin_elevation, _ = origin
    sightline_elevation = origin_elevation + configuration.observer_height_metres
    rays: list[list[list[float]]] = []
    missing_terrain_samples = 0
    for bearing_index in range(configuration.circle_increments):
        bearing = bearing_index * 360 / configuration.circle_increments
        ray = [[longitude, latitude]]
        for distance in range(
            configuration.distance_increment_metres,
            configuration.distance_limit_metres + 1,
            configuration.distance_increment_metres,
        ):
            sample_longitude, sample_latitude = destination_point(
                longitude, latitude, bearing, distance
            )
            elevation = terrain_elevation(sample_longitude, sample_latitude)
            if elevation is None:
                missing_terrain_samples += 1
            ray.append([sample_longitude, sample_latitude])
            if elevation is not None and elevation[0] > sightline_elevation:
                break
        if len(ray) > 1:
            rays.append(ray)
        if update_progress:
            update_progress(round((bearing_index + 1) * 100 / configuration.circle_increments))
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {
                "kind": "los-rosette",
                "name": "LOS Rosette",
                "label": False,
                "color": "#ffbf69",
                "lineStyle": "dotted",
                "originElevationMetres": origin_elevation,
                "observerHeightMetres": configuration.observer_height_metres,
                "missingTerrainSamples": missing_terrain_samples,
            },
            "geometry": {"type": "MultiLineString", "coordinates": rays},
        }],
    }


def terrain_tile_image(directory: Path, zoom: int, x: int, y: int) -> Image.Image | None:
    path = directory / "tiles" / str(zoom) / str(x) / f"{y}.png"
    if path.is_file():
        with Image.open(path) as source:
            return source.convert("RGBA")
    if zoom == 0:
        return None
    parent = terrain_tile_image(directory, zoom - 1, x // 2, y // 2)
    if parent is None:
        return None
    child_x = x % 2
    child_y = y % 2
    child = parent.crop(
        (
            child_x * 128,
            child_y * 128,
            (child_x + 1) * 128,
            (child_y + 1) * 128,
        )
    ).resize((256, 256), Image.Resampling.NEAREST)
    parent.close()
    return child


def raster_tile_bytes(zoom: int, x: int, y: int) -> bytes | None:
    composite: Image.Image | None = None
    for package in terrain_packages():
        detail = terrain_tile_image(Path(package["directory"]), zoom, x, y)
        if detail is None:
            continue
        if composite is None:
            composite = Image.new("RGBA", (256, 256))
        coverage = detail.getchannel("A").point(lambda alpha: 255 if alpha else 0)
        detail.putalpha(255)
        composite.paste(detail, mask=coverage)
        detail.close()
    if composite is None:
        return None
    from io import BytesIO
    output = BytesIO()
    composite.save(output, "PNG")
    composite.close()
    return output.getvalue()


@lru_cache(maxsize=512)
def cached_raster_tile_bytes(zoom: int, x: int, y: int) -> bytes | None:
    return raster_tile_bytes(zoom, x, y)


app = FastAPI(title="Kody Terrain Service", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"], allow_methods=["GET", "PUT", "POST", "DELETE"], allow_headers=["Content-Type", "If-Match"])
configuration_store = ConfigurationStore()
import_state = ImportState()
los_rosette_state = LosRosetteState()


@app.get("/api/raster/revision")
def get_raster_revision() -> dict[str, str]:
    manifests = [
        manifest_path.read_bytes()
        for manifest_path in sorted(packages_directory().glob("*/manifest.json"))
    ] if packages_directory().is_dir() else []
    revision_inputs = [RASTER_RENDERING_REVISION, *manifests]
    return {"revision": hashlib.sha256(b"\0".join(revision_inputs)).hexdigest() if manifests else ""}


@app.get("/api/raster/{zoom}/{x}/{y}")
def get_raster_tile(zoom: int, x: int, y: int) -> Response:
    if not 0 <= zoom <= 18 or not 0 <= x < 2**zoom or not 0 <= y < 2**zoom:
        raise HTTPException(status_code=422, detail="invalid raster tile coordinates")
    tile = cached_raster_tile_bytes(zoom, x, y)
    if tile is None:
        raise HTTPException(status_code=404, detail="local terrain tile is unavailable")
    return Response(content=tile, media_type="image/png")


@app.get("/api/elevation")
def get_elevation(longitude: float, latitude: float) -> dict[str, object]:
    if not -180 <= longitude <= 180 or not -MAX_MERCATOR_LATITUDE <= latitude <= MAX_MERCATOR_LATITUDE:
        raise HTTPException(status_code=422, detail="invalid longitude or latitude")
    elevation = terrain_elevation(longitude, latitude)
    if elevation is None:
        return {"heightMetres": None}
    height, package_id = elevation
    return {"heightMetres": height, "packageId": package_id}


@app.get("/api/kody/manifest")
def get_manifest() -> dict[str, object]:
    return {"kodyApiVersion": "1", "serviceId": "terrain", "displayName": "Terrain", "configuration": {
        "readPath": "/api/config", "writePath": "/api/config",
        "fields": [
            {"key": "sourceFilename", "label": "Terrain filename in imports", "type": "string", "required": False},
            {
                "key": "sourceProjection",
                "label": "ArcASCII projection (auto, geographic, web-mercator, or world-mercator)",
                "type": "string",
                "required": False,
            },
            {"key": "showPalette", "label": "Show elevation palette at viewport bottom", "type": "boolean"},
            {"key": "circleIncrements", "label": "LOS bearings", "type": "number", "minimum": 4, "maximum": 360, "step": 1},
            {"key": "distanceIncrementMetres", "label": "LOS sample distance (metres)", "type": "number", "minimum": 10, "maximum": 10000, "step": 10},
            {"key": "distanceLimitMetres", "label": "LOS distance limit (metres)", "type": "number", "minimum": 10, "maximum": 500000, "step": 10},
            {"key": "observerHeightMetres", "label": "LOS observer height (metres)", "type": "number", "minimum": 0, "maximum": 100, "step": 0.1},
        ],
        "actions": [
            {"label": "Import terrain file", "path": "/api/actions/import-terrain", "statusPath": "/api/actions/import-terrain", "method": "POST"},
            {"label": "Import viewport from local file", "path": "/api/actions/import-viewport", "statusPath": "/api/actions/import-terrain", "method": "POST"},
            {"label": "Build numeric terrain chunks", "path": "/api/actions/build-numeric-tiles", "statusPath": "/api/actions/import-terrain", "method": "POST"},
            {"label": "Rebuild terrain display tiles", "path": "/api/actions/rebuild-display-tiles", "statusPath": "/api/actions/import-terrain", "method": "POST"},
        ],
    }, "contextMenuActions": [
        {
            "id": "create-los-rosette",
            "label": "Create LOS Rosette",
            "path": "/api/actions/los-rosette",
            "statusPath": "/api/actions/los-rosette",
            "resultPath": "/api/los-rosette",
            "method": "POST",
        },
        {
            "id": "clear-los-rosette",
            "label": "Clear LOS Rosette",
            "path": "/api/los-rosette",
            "resultPath": "/api/los-rosette",
            "method": "DELETE",
        },
    ]}


@app.get("/api/palette")
def get_palette() -> dict[str, object]:
    return {
        "stops": [
            {"heightMetres": height, "colour": f"rgb({red}, {green}, {blue})"}
            for height, (red, green, blue) in TERRAIN_PALETTE
        ],
    }


@app.get("/api/config")
def get_configuration() -> dict[str, object]:
    return configuration_store.response()


@app.put("/api/config")
def update_configuration(configuration: TerrainConfiguration, if_match: str | None = Header(default=None)) -> dict[str, object]:
    return configuration_store.update(configuration, if_match)


def run_import(background_tasks: BackgroundTasks, viewport_only: bool) -> dict[str, str]:
    configuration = configuration_store.configuration
    filename = configuration.source_filename
    if not filename:
        raise HTTPException(status_code=422, detail="No local sourceFilename supplied. Download for viewport requires an ArcASCII file in imports; remote acquisition is unavailable without an API key.")
    import_state.start("Preparing terrain import.")
    def process() -> None:
        try:
            import_state.complete(
                import_terrain(
                    filename,
                    fetch_viewport() if viewport_only else None,
                    import_state,
                    configuration.source_projection,
                )
            )
        except Exception as error:
            import_state.fail(error)
    background_tasks.add_task(process)
    return {"message": "Terrain import started."}


@app.post("/api/actions/import-terrain", status_code=202)
def import_terrain_action(background_tasks: BackgroundTasks) -> dict[str, str]:
    return run_import(background_tasks, False)


@app.post("/api/actions/import-viewport", status_code=202)
def import_viewport_action(background_tasks: BackgroundTasks) -> dict[str, str]:
    return run_import(background_tasks, True)


@app.post("/api/actions/build-numeric-tiles", status_code=202)
def build_numeric_tiles_action(background_tasks: BackgroundTasks) -> dict[str, str]:
    import_state.start("Preparing numeric terrain chunk build.")

    def process() -> None:
        try:
            import_state.complete(build_missing_numeric_tiles(import_state))
        except Exception as error:
            import_state.fail(error)

    background_tasks.add_task(process)
    return {"message": "Numeric terrain chunk build started."}


@app.post("/api/actions/rebuild-display-tiles", status_code=202)
def rebuild_display_tiles_action(background_tasks: BackgroundTasks) -> dict[str, str]:
    import_state.start("Preparing terrain display tile rebuild.")

    def process() -> None:
        try:
            import_state.complete(rebuild_terrain_display_tiles(import_state))
        except Exception as error:
            import_state.fail(error)

    background_tasks.add_task(process)
    return {"message": "Terrain display tile rebuild started."}


@app.get("/api/actions/import-terrain")
def get_import_status() -> dict[str, object]:
    return import_state.response()


@app.post("/api/actions/los-rosette", status_code=202)
def create_los_rosette_action(request: LosRosetteRequest) -> dict[str, str]:
    los_rosette_state.start(request, configuration_store.configuration)
    return {"message": "LOS Rosette calculation started."}


@app.get("/api/actions/los-rosette")
def get_los_rosette_status() -> dict[str, object]:
    return los_rosette_state.response()


@app.get("/api/los-rosette")
def get_los_rosette() -> dict[str, object]:
    return los_rosette_state.result()


@app.delete("/api/los-rosette")
def clear_los_rosette() -> dict[str, object]:
    los_rosette_state.clear()
    return los_rosette_state.result()
