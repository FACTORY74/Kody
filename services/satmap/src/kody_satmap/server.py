"""Raster satellite imagery API for the Kody map."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from io import BytesIO
from math import atan, ceil, exp, floor, log, log2, pi, sin, sinh, tan
from pathlib import Path
import os
from threading import Lock, RLock
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel, Field

ROOT_DIRECTORY = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get("KODY_DATA_DIRECTORY", ROOT_DIRECTORY / "data"))
SATELLITE_DIRECTORY = DATA_ROOT / "satmap" / "satellite"
WEB_MERCATOR_HALF_WORLD = 20037508.342789244
EARTH_RADIUS_METRES = 6378137


def initialize_data_directory() -> None:
    SATELLITE_DIRECTORY.mkdir(parents=True, exist_ok=True)


initialize_data_directory()


class SatelliteConfiguration(BaseModel):
    source_filename: str = Field(default="", alias="sourceFilename")
    rebuild_pyramid_with_import: bool = Field(default=True, alias="rebuildPyramidWithImport")


class ConfigurationStore:
    def __init__(self) -> None:
        self.configuration = SatelliteConfiguration()
        self.lock = RLock()

    def revision(self) -> str:
        return str(hash(self.configuration.model_dump_json(by_alias=True)))

    def response(self) -> dict[str, object]:
        with self.lock:
            return {"revision": self.revision(), **self.configuration.model_dump(by_alias=True)}

    def update(
        self, configuration: SatelliteConfiguration, expected_revision: str | None
    ) -> dict[str, object]:
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

    def fail(self, error: Exception) -> None:
        with self.lock:
            self.status = "failed"
            self.message = str(error)

    def response(self) -> dict[str, str]:
        with self.lock:
            return {"status": self.status, "message": self.message}


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
    parent_zoom = child_zoom - 1
    parent_tiles = {(x // 2, y // 2) for x, y in child_tiles}
    for parent_x, parent_y in parent_tiles:
            destination = directory / str(parent_zoom) / str(parent_x) / f"{parent_y}.png"
            if destination.is_file():
                with Image.open(destination) as existing:
                    parent = existing.convert("RGBA")
            else:
                parent = Image.new("RGBA", (256, 256))
            for child_x, child_y in child_tiles:
                if child_x // 2 != parent_x or child_y // 2 != parent_y:
                    continue
                child_path = directory / str(child_zoom) / str(child_x) / f"{child_y}.png"
                with Image.open(child_path) as source:
                    child = source.convert("RGBA").resize((128, 128), Image.Resampling.LANCZOS)
                parent.alpha_composite(child, ((child_x % 2) * 128, (child_y % 2) * 128))
                child.close()
            destination.parent.mkdir(parents=True, exist_ok=True)
            parent.save(destination)
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
    zoom_directory = directory / str(zoom)
    if not zoom_directory.is_dir():
        return set()
    coordinates = set()
    for path in zoom_directory.glob("*/*"):
        if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        try:
            coordinates.add((int(path.parent.name), int(path.stem)))
        except ValueError:
            continue
    return coordinates


def rebuild_overview_tiles(directory: Path) -> int:
    zooms = [
        int(path.name)
        for path in directory.iterdir()
        if path.is_dir() and path.name.isdigit() and 0 <= int(path.name) <= 18
    ]
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
    written_tiles = 0
    native_tiles = set()
    for y in range(minimum_y, maximum_y + 1):
        for x in range(minimum_x, maximum_x + 1):
            tile_left = -WEB_MERCATOR_HALF_WORLD + x * tile_span
            tile_top = WEB_MERCATOR_HALF_WORLD - y * tile_span
            destination = SATELLITE_DIRECTORY / str(native_zoom) / str(x) / f"{y}.png"
            destination.parent.mkdir(parents=True, exist_ok=True)
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
            if destination.is_file():
                with Image.open(destination) as existing:
                    tile = existing.convert("RGBA")
                tile.alpha_composite(patch)
                patch.close()
            else:
                tile = patch
            tile.save(destination)
            tile.close()
            native_tiles.add((x, y))
            written_tiles += 1
    source_image.close()
    ancestor_tiles = (
        compose_parent_tiles(SATELLITE_DIRECTORY, native_zoom, native_tiles)
        if rebuild_pyramid
        else 0
    )
    marker = SATELLITE_DIRECTORY / ".revision"
    marker.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
    cached_raster_tile_bytes.cache_clear()
    message = f"Imported {written_tiles} satellite tiles at zoom {native_zoom}."
    if rebuild_pyramid:
        message += f" Updated {ancestor_tiles} impacted lower-zoom tiles."
    return message


def raster_tile_path(directory: Path, zoom: int, x: int, y: int) -> Path | None:
    for extension in (".png", ".jpg", ".jpeg", ".webp"):
        path = directory / str(zoom) / str(x) / f"{y}{extension}"
        if path.is_file():
            return path
    return None


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
    path = raster_tile_path(directory, zoom, x, y)
    if path is not None:
        with Image.open(path) as source:
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
    tile.save(output, format="PNG")
    tile.close()
    return output.getvalue()


@lru_cache(maxsize=512)
def cached_raster_tile_bytes(zoom: int, x: int, y: int) -> bytes | None:
    return raster_tile_bytes(SATELLITE_DIRECTORY, zoom, x, y)


app = FastAPI(title="Kody Satellite Map Service", version="0.1.0")
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
    marker = SATELLITE_DIRECTORY / ".revision"
    return {"revision": marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""}


@app.get("/api/raster/{zoom}/{x}/{y}")
def get_raster_tile(zoom: int, x: int, y: int) -> Response:
    if not 0 <= zoom <= 18 or not 0 <= x < 2**zoom or not 0 <= y < 2**zoom:
        raise HTTPException(status_code=422, detail="invalid raster tile coordinates")
    tile = cached_raster_tile_bytes(zoom, x, y)
    if tile is not None:
        return Response(content=tile, media_type="image/png")
    raise HTTPException(status_code=404, detail="local raster tile is unavailable")


@app.get("/api/kody/manifest")
def get_manifest() -> dict[str, object]:
    return {
        "kodyApiVersion": "1",
        "serviceId": "satellite-map",
        "displayName": "Satellite map",
        "configuration": {
            "readPath": "/api/config",
            "writePath": "/api/config",
            "fields": [
                {"key": "sourceFilename", "label": "PNG filename", "type": "string"},
                {
                    "key": "rebuildPyramidWithImport",
                    "label": "Rebuild impacted overview tiles",
                    "type": "boolean",
                },
            ],
            "actions": [{
                "label": "Process image",
                "path": "/api/actions/process-image",
                "statusPath": "/api/actions/process-image",
                "method": "POST",
            }, {
                "label": "Rebuild satellite pyramid",
                "path": "/api/actions/build-overview-tiles",
                "statusPath": "/api/actions/process-image",
                "method": "POST",
            }],
        },
    }


@app.get("/api/config")
def get_configuration() -> dict[str, object]:
    return configuration_store.response()


@app.put("/api/config")
def update_configuration(
    configuration: SatelliteConfiguration,
    if_match: str | None = Header(default=None),
) -> dict[str, object]:
    return configuration_store.update(configuration, if_match)


@app.post("/api/actions/process-image", status_code=202)
def process_image(background_tasks: BackgroundTasks) -> dict[str, str]:
    configuration = configuration_store.configuration
    filename = configuration.source_filename
    if not filename:
        raise HTTPException(status_code=422, detail="PNG filename is required")
    import_state.start("Processing satellite image.")

    def process() -> None:
        try:
            import_state.complete(
                import_georeferenced_png(filename, configuration.rebuild_pyramid_with_import)
            )
        except Exception as error:
            import_state.fail(error)

    background_tasks.add_task(process)
    return {"message": "Satellite image processing started."}


@app.get("/api/actions/process-image")
def get_image_processing_status() -> dict[str, str]:
    return import_state.response()


@app.post("/api/actions/build-overview-tiles", status_code=202)
def build_overview_tiles(background_tasks: BackgroundTasks) -> dict[str, str]:
    import_state.start("Rebuilding lower-zoom satellite pyramid.")

    def process() -> None:
        try:
            count = rebuild_overview_tiles(SATELLITE_DIRECTORY)
            marker = SATELLITE_DIRECTORY / ".revision"
            marker.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
            cached_raster_tile_bytes.cache_clear()
            import_state.complete(f"Built {count} lower-zoom overview tiles.")
        except Exception as error:
            import_state.fail(error)

    background_tasks.add_task(process)
    return {"message": "Building lower-zoom satellite overview tiles."}
