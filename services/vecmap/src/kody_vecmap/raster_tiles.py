"""Cached Natural Earth WebP display tiles."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from io import BytesIO
import os
from pathlib import Path
from threading import Lock, get_ident

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from .math import MapMath
from .native_tiles import NativeTileStore
from .vector_theme import VectorStyle

MAXIMUM_ZOOM = 12
TILE_PIXELS = 512
RENDERER_REVISION = 10
COASTAL_SEAM_RADIUS_PIXELS = 16


def cache_revision(
    store: NativeTileStore,
    configuration: dict[str, object],
    styles: dict[str, VectorStyle],
) -> str:
    source = {
        "renderer": RENDERER_REVISION,
        "database": store.data_revision(),
        "configuration": configuration,
        "styles": {
            name: style.model_dump(by_alias=True)
            for name, style in sorted(styles.items())
        },
    }
    return hashlib.sha256(
        json.dumps(source, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]


class NaturalEarthRasterCache:
    _locks_guard = Lock()
    _tile_locks: dict[Path, Lock] = {}

    def __init__(self, maps_directory: Path, revision: str) -> None:
        self.root = maps_directory / "raster-tiles" / revision

    def tile_path(self, zoom: int, x: int, y: int) -> Path:
        return self.root / str(zoom) / str(x) / f"{y}.webp"

    def write_tile(self, zoom: int, x: int, y: int, payload: bytes) -> None:
        path = self.tile_path(zoom, x, y)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_name(
            f".{path.name}.{os.getpid()}.{get_ident()}.tmp"
        )
        try:
            temporary_path.write_bytes(payload)
            temporary_path.replace(path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def get_or_render(
        self,
        zoom: int,
        x: int,
        y: int,
        renderer: Callable[[], bytes],
    ) -> Path:
        path = self.tile_path(zoom, x, y)
        if path.is_file():
            return path
        with self._locks_guard:
            lock = self._tile_locks.setdefault(path, Lock())
        with lock:
            if not path.is_file():
                self.write_tile(zoom, x, y, renderer())
        return path


def _tile_point(longitude: float, latitude: float, zoom: int, x: int, y: int) -> tuple[float, float]:
    world_x, world_y = MapMath.lon_lat_to_world(longitude, latitude)
    scale = TILE_PIXELS * (2**zoom)
    return world_x * scale - x * TILE_PIXELS, world_y * scale - y * TILE_PIXELS


def _draw_geometry(
    draw: ImageDraw.ImageDraw,
    geometry: dict[str, object],
    style: VectorStyle,
    zoom: int,
    x: int,
    y: int,
) -> None:
    geometry_type = geometry["type"]
    coordinates = geometry.get("coordinates")
    if geometry_type == "Point":
        if not isinstance(coordinates, (list, tuple)):
            return
        point = _tile_point(float(coordinates[0]), float(coordinates[1]), zoom, x, y)
        radius = (style.point_radius or 0) * 2
        if radius and style.fill:
            draw.ellipse(
                (point[0] - radius, point[1] - radius, point[0] + radius, point[1] + radius),
                fill=style.fill,
                outline=style.stroke,
                width=max(1, round((style.line_width or 0.5) * 2)),
            )
        return
    if geometry_type == "GeometryCollection":
        for item in geometry.get("geometries", []):
            if isinstance(item, dict):
                _draw_geometry(draw, item, style, zoom, x, y)
        return

    if geometry_type == "LineString":
        lines = [coordinates]
    elif geometry_type == "MultiLineString":
        lines = coordinates
    elif geometry_type == "Polygon":
        lines = [coordinates]
    elif geometry_type == "MultiPolygon":
        lines = coordinates
    else:
        return

    def points(line: list[object] | tuple[object, ...]) -> list[tuple[float, float]]:
        return [
            _tile_point(float(point[0]), float(point[1]), zoom, x, y)
            for point in line
            if isinstance(point, (list, tuple)) and len(point) >= 2
        ]

    width = max(1, round((style.line_width or 0.5) * 2))
    if geometry_type in {"Polygon", "MultiPolygon"}:
        polygons = lines
        for polygon in polygons:
            if not isinstance(polygon, (list, tuple)) or not polygon:
                continue
            exterior = points(polygon[0])
            if len(exterior) >= 3 and style.fill:
                draw.polygon(exterior, fill=style.fill)
            if len(exterior) >= 2 and style.stroke:
                draw.line(exterior, fill=style.stroke, width=width, joint="curve")
            for hole in polygon[1:]:
                ring = points(hole)
                if len(ring) >= 2 and style.stroke:
                    draw.line(ring, fill=style.stroke, width=width, joint="curve")
        return
    for line in lines:
        if isinstance(line, (list, tuple)):
            rendered = points(line)
            if len(rendered) >= 2 and style.stroke:
                draw.line(rendered, fill=style.stroke, width=width, joint="curve")


def _feature_style(
    properties: dict[str, object], styles: dict[str, VectorStyle]
) -> tuple[VectorStyle, int] | None:
    layer = properties.get("layer")
    if not isinstance(layer, str):
        return None
    depth = 0
    if layer == "ne-bathymetry":
        source_layer = properties.get("sourceLayer")
        if isinstance(source_layer, str):
            match = re.search(r"_bathymetry_[a-z]_(\d+)$", source_layer.casefold())
            if match:
                depth = int(match.group(1))
                layer = f"ne-bathymetry-{depth}"
    elif layer.startswith("ne-bathymetry-"):
        try:
            depth = int(layer.rsplit("-", maxsplit=1)[1])
        except ValueError:
            pass
    style = styles.get(layer)
    return (style, depth) if style else None


def _label_key(properties: dict[str, object]) -> str | None:
    name = properties.get("name")
    if not isinstance(name, str):
        return None
    normalized = " ".join(name.split()).casefold()
    return normalized or None


def render_tile(
    collection: dict[str, object],
    styles: dict[str, VectorStyle],
    zoom: int,
    x: int,
    y: int,
) -> bytes:
    features = [
        (feature, style, depth)
        for feature in collection.get("features", [])
        if isinstance(feature, dict)
        and isinstance(feature.get("properties"), dict)
        and isinstance(feature.get("geometry"), dict)
        for style_and_depth in [_feature_style(feature["properties"], styles)]
        if style_and_depth is not None
        for style, depth in [style_and_depth]
    ]
    water = styles.get("ne-ocean")
    image = Image.new(
        "RGBA",
        (TILE_PIXELS, TILE_PIXELS),
        water.fill if water and water.fill else "#202020",
    )
    draw = ImageDraw.Draw(image)
    features.sort(key=lambda item: (item[1].z_index, item[2]))
    font = ImageFont.load_default()
    land_mask = Image.new("L", (TILE_PIXELS, TILE_PIXELS))
    land_draw = ImageDraw.Draw(land_mask)
    land_style: VectorStyle | None = None
    label_features: list[tuple[dict[str, object], VectorStyle]] = []
    rendered_label_keys: set[str] = set()
    for feature, style, _ in features:
        properties = feature["properties"]
        geometry = feature["geometry"]
        _draw_geometry(draw, geometry, style, zoom, x, y)
        if properties.get("layer") == "ne-land":
            _draw_geometry(land_draw, geometry, style, zoom, x, y)
            land_style = style
        if (
            style.label
            and geometry.get("type") == "Point"
        ):
            label_features.append((feature, style))
    if land_style and land_style.fill:
        background = Image.new("RGBA", (1, 1), water.fill if water and water.fill else "#202020").getpixel((0, 0))
        land_fill = Image.new("RGBA", (1, 1), land_style.fill).getpixel((0, 0))
        seam_mask = land_mask.filter(ImageFilter.MaxFilter(COASTAL_SEAM_RADIUS_PIXELS * 2 + 1))
        pixels = image.load()
        seam_pixels = seam_mask.load()
        for pixel_y in range(TILE_PIXELS):
            for pixel_x in range(TILE_PIXELS):
                if seam_pixels[pixel_x, pixel_y] and pixels[pixel_x, pixel_y] == background:
                    pixels[pixel_x, pixel_y] = land_fill
    for feature, style in label_features:
        properties = feature["properties"]
        label_key = _label_key(properties)
        if label_key is None or label_key in rendered_label_keys:
            continue
        rendered_label_keys.add(label_key)
        geometry = feature["geometry"]
        coordinates = geometry["coordinates"]
        point = _tile_point(float(coordinates[0]), float(coordinates[1]), zoom, x, y)
        offset_x = (style.label_offset_x or 0) * 2
        offset_y = (style.label_offset_y or 0) * 2
        draw.text(
            (point[0] + offset_x, point[1] + offset_y),
            str(properties["name"]),
            font=font,
            fill=style.label_fill or style.fill or "black",
            stroke_width=round((style.label_stroke_width or 0) * 2),
            stroke_fill=style.label_stroke,
            anchor="lm",
        )
    payload = BytesIO()
    image.save(payload, format="WEBP", lossless=True, method=6)
    return payload.getvalue()
