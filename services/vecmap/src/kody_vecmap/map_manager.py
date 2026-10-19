"""CLI for building local, offline OSM detail packages."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import gzip
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from datetime import UTC, datetime
from math import ceil, floor, log2
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import shapefile
import osmium
from PIL import Image

from shapely import from_wkb
from shapely.geometry import LineString, Point, Polygon, box, mapping, shape
from shapely.ops import linemerge, snap, unary_union
from shapely.strtree import STRtree

from .map_packages import NativeTileStore, PackageManifest, PackageSource
from .math import Bounds, MapMath

ROOT_DIRECTORY = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get("KODY_DATA_DIRECTORY", ROOT_DIRECTORY / "data"))
MAPS_DIRECTORY = DATA_ROOT / "vecmap" / "maps"
LAND_POLYGONS_FILE = DATA_ROOT / "vecmap" / "imports" / "land-polygons-split-4326.zip"
LAND_POLYGONS_URL = "https://osmdata.openstreetmap.de/download/land-polygons-split-4326.zip"
WEB_MERCATOR_HALF_WORLD = 20037508.342789244
DEFAULT_OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
GEOFABRIK_WATER_LAYER = "gis_osm_water_a_free"
GEOFABRIK_LANDUSE_LAYER = "gis_osm_landuse_a_free"
GEOFABRIK_ROADS_LAYER = "gis_osm_roads_free"
GEOFABRIK_PLACES_LAYER = "gis_osm_places_free_1"
OVERVIEW_ROADS = {
    "motorway",
    "motorway_link",
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
}
GEOFABRIK_ROADS = OVERVIEW_ROADS | {
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
}
GEOFABRIK_URBAN_AREAS = {"residential", "commercial", "industrial", "retail"}
GEOFABRIK_GREEN_AREAS = {
    "forest",
    "meadow",
    "grass",
    "scrub",
    "park",
    "recreation_ground",
    "heath",
}
LARGE_AREA_DEGREES_SQUARED = 0.0003
PLACE_MINIMUM_ZOOMS = {"city": 8, "town": 10, "village": 12, "hamlet": 14}


def parse_coverage_tiles(value: str, coverage_zoom: int) -> list[tuple[int, int]]:
    tiles = []
    for tile in value.split(","):
        try:
            zoom, x, y = (int(part) for part in tile.split("/"))
        except ValueError as error:
            raise argparse.ArgumentTypeError(f"invalid tile: {tile}") from error
        if zoom != coverage_zoom or not 0 <= x < 2**zoom or not 0 <= y < 2**zoom:
            raise argparse.ArgumentTypeError(f"tile is outside coverage zoom {coverage_zoom}: {tile}")
        tiles.append((x, y))
    if not tiles:
        raise argparse.ArgumentTypeError("at least one coverage tile is required")
    return tiles


def parse_bounding_box(value: str) -> Bounds:
    try:
        west, south, east, north = (float(part) for part in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "bounding box must be west,south,east,north in longitude/latitude degrees"
        ) from error
    if not -180 <= west < east <= 180 or not -MapMath.MAX_LATITUDE <= south < north <= MapMath.MAX_LATITUDE:
        raise argparse.ArgumentTypeError(
            "bounding box must be within Web Mercator bounds and must not cross the antimeridian"
        )
    return Bounds(west, south, east, north)


def tiles_for_bounds(bounds: Bounds, coverage_zoom: int) -> list[tuple[int, int]]:
    tiles_per_axis = 2**coverage_zoom
    northwest = MapMath.lon_lat_to_world(bounds.west, bounds.north)
    southeast = MapMath.lon_lat_to_world(bounds.east, bounds.south)
    minimum_x = max(0, floor(northwest[0] * tiles_per_axis))
    maximum_x = min(tiles_per_axis - 1, ceil(southeast[0] * tiles_per_axis) - 1)
    minimum_y = max(0, floor(northwest[1] * tiles_per_axis))
    maximum_y = min(tiles_per_axis - 1, ceil(southeast[1] * tiles_per_axis) - 1)
    return [
        (x, y)
        for y in range(minimum_y, maximum_y + 1)
        for x in range(minimum_x, maximum_x + 1)
    ]


def coverage_bounds(coverage_zoom: int, tiles: list[tuple[int, int]]) -> Bounds:
    bounds = [MapMath.tile_bounds(coverage_zoom, x, y) for x, y in tiles]
    return Bounds(
        min(item.west for item in bounds),
        min(item.south for item in bounds),
        max(item.east for item in bounds),
        max(item.north for item in bounds),
    )


def download_osm_features(
    endpoints: tuple[str, ...],
    bounds: Bounds,
    maximum_zoom: int,
    coastline_margin: float,
    progress: Callable[[str, int], None] | None = None,
) -> tuple[list[dict[str, object]], str]:
    coastline_bounds = expanded_bounds(bounds, coastline_margin)
    query = f"""[out:json][timeout:180];
(
  way["highway"]({bounds.south},{bounds.west},{bounds.north},{bounds.east});
  way["building"]({bounds.south},{bounds.west},{bounds.north},{bounds.east});
  way["natural"="coastline"]({coastline_bounds.south},{coastline_bounds.west},{coastline_bounds.north},{coastline_bounds.east});
  way["natural"="wood"]({bounds.south},{bounds.west},{bounds.north},{bounds.east});
  way["landuse"="forest"]({bounds.south},{bounds.west},{bounds.north},{bounds.east});
);
out tags geom;"""
    payload = None
    errors = []
    for attempt, endpoint in enumerate(endpoints, start=1):
        if progress:
            progress(f"Requesting OSM data from source {attempt} of {len(endpoints)}.", 15)
        request = Request(
            f"{endpoint}?{urlencode({'data': query})}",
            headers={"User-Agent": "Kody-map-manager/0.1 (offline package preparation)"},
        )
        try:
            print(f"Downloading OSM data from {endpoint}", file=sys.stderr)
            with urlopen(request, timeout=240) as response:
                payload = json.load(response)
            source_url = endpoint
            break
        except (HTTPError, URLError, TimeoutError) as error:
            errors.append(f"{endpoint}: {error}")
            print(f"OSM source unavailable: {error}", file=sys.stderr)
    if payload is None:
        raise RuntimeError("all configured OSM download endpoints failed: " + "; ".join(errors))

    if progress:
        progress("Parsing downloaded OSM features.", 45)
    features = []
    for element in payload.get("elements", []):
        coordinates = [(item["lon"], item["lat"]) for item in element.get("geometry", [])]
        tags = element.get("tags", {})
        if len(coordinates) < 2:
            continue
        layer = osm_layer(tags, coordinates)
        if layer is None:
            continue
        is_area = layer in {"osm-building", "osm-forest"} and coordinates[0] == coordinates[-1]
        if layer == "osm-forest" and not is_area:
            continue
        minimum_zoom = minimum_zoom_for_feature(tags, layer)
        if minimum_zoom > maximum_zoom:
            continue
        geometry_data = {
            "type": "Polygon" if is_area else "LineString",
            "coordinates": [coordinates] if is_area else coordinates,
        }
        geometry = shape(geometry_data).simplify(simplification_tolerance(minimum_zoom), preserve_topology=True)
        if geometry.is_empty:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "layer": layer,
                    "name": tags.get("name"),
                    "highway": tags.get("highway"),
                    "building": tags.get("building"),
                    "minZoom": minimum_zoom,
                },
                "geometry": mapping(geometry),
            }
        )
    if progress:
        progress(f"Prepared {len(features)} OSM features.", 60)
    return features, source_url


def expanded_bounds(bounds: Bounds, margin_degrees: float) -> Bounds:
    return Bounds(
        max(-180, bounds.west - margin_degrees),
        max(-MapMath.MAX_LATITUDE, bounds.south - margin_degrees),
        min(180, bounds.east + margin_degrees),
        min(MapMath.MAX_LATITUDE, bounds.north + margin_degrees),
    )


def coastline_land_features(
    features: list[dict[str, object]], minimum_zoom: int = 10
) -> list[dict[str, object]]:
    coastline_lines = [
        shape(feature["geometry"])
        for feature in features
        if feature["properties"]["layer"] == "osm-coastline"
    ]
    if not coastline_lines:
        return []
    merged = unary_union(coastline_lines)
    snapped = snap(merged, merged, 0.00001)
    stitched = linemerge(snapped) if snapped.geom_type == "MultiLineString" else snapped
    lines = list(stitched.geoms) if stitched.geom_type == "MultiLineString" else [stitched]
    polygons = [
        Polygon(line)
        for line in lines
        if line.geom_type == "LineString" and line.is_ring and signed_area(list(line.coords)) > 0
    ]
    return [
        {
            "type": "Feature",
            "properties": {"layer": "osm-land", "minZoom": minimum_zoom},
            "geometry": mapping(polygon),
        }
        for polygon in polygons
        if not polygon.is_empty
    ]


def signed_area(coordinates: list[tuple[float, float]]) -> float:
    return sum(
        left[0] * right[1] - right[0] * left[1]
        for left, right in zip(coordinates, coordinates[1:])
    ) / 2


def clip_forests_to_land(
    features: list[dict[str, object]], land_features: list[dict[str, object]]
) -> list[dict[str, object]]:
    land = unary_union([shape(feature["geometry"]) for feature in land_features])
    clipped_features = []
    for feature in features:
        if feature["properties"].get("layer") != "osm-forest":
            clipped_features.append(feature)
            continue
        forest = shape(feature["geometry"])
        if not forest.is_valid:
            continue
        clipped = forest.intersection(land)
        if not clipped.is_empty and clipped.is_valid:
            clipped_features.append({**feature, "geometry": mapping(clipped)})
    return clipped_features


def download_land_polygons(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(".tmp")
    request = Request(url, headers={"User-Agent": "Kody-map-manager/0.1"})
    with urlopen(request, timeout=600) as response, temporary_path.open("wb") as stream:
        shutil.copyfileobj(response, stream)
    temporary_path.replace(path)


def cached_land_polygons() -> Path:
    if not LAND_POLYGONS_FILE.is_file():
        download_land_polygons(LAND_POLYGONS_URL, LAND_POLYGONS_FILE)
    return LAND_POLYGONS_FILE


def land_features_from_polygons(
    path: Path, bounds: Bounds, minimum_zoom: int
) -> list[dict[str, object]]:
    if not path.is_file():
        raise RuntimeError(
            f"OSM land polygon source is missing: {path}; run prepare-land-polygons first"
        )
    features = []
    with zipfile.ZipFile(path) as archive:
        shapefile_name = next(name for name in archive.namelist() if name.endswith(".shp"))
        shapefile_stem = shapefile_name.removesuffix(".shp")
        with ExitStack() as streams:
            reader = shapefile.Reader(
                shp=streams.enter_context(archive.open(f"{shapefile_stem}.shp")),
                shx=streams.enter_context(archive.open(f"{shapefile_stem}.shx")),
                dbf=streams.enter_context(archive.open(f"{shapefile_stem}.dbf")),
            )
            for item in reader.iterShapeRecords(bbox=[bounds.west, bounds.south, bounds.east, bounds.north]):
                geometry = shape(item.shape.__geo_interface__)
                clipped = geometry.intersection(box(bounds.west, bounds.south, bounds.east, bounds.north))
                if not clipped.is_empty:
                    features.append(
                        {
                            "type": "Feature",
                            "properties": {"layer": "osm-land", "minZoom": minimum_zoom},
                            "geometry": mapping(clipped),
                        }
                    )
    if not features:
        raise RuntimeError("global OSM land polygons did not cover the requested package")
    return features


def osm_layer(tags: dict[str, str], coordinates: list[tuple[float, float]]) -> str | None:
    if "highway" in tags:
        return "osm-road"
    if "building" in tags:
        return "osm-building"
    if tags.get("natural") == "coastline":
        return "osm-coastline"
    if tags.get("natural") == "wood" or tags.get("landuse") == "forest":
        return "osm-forest"
    if tags.get("landuse") in GEOFABRIK_URBAN_AREAS:
        return "osm-urban"
    return None


def minimum_zoom_for_feature(tags: dict[str, str], layer: str) -> int:
    if layer == "osm-building":
        return 15
    if layer in {"osm-forest", "osm-coastline"}:
        return 8
    highway = tags.get("highway", "")
    if highway in OVERVIEW_ROADS:
        return 8
    if highway in {"secondary", "tertiary"}:
        return 12
    if highway in {"residential", "unclassified"}:
        return 14
    return 16


def place_minimum_zoom(place: str) -> int | None:
    return PLACE_MINIMUM_ZOOMS.get(place)


def geofabrik_geometry(value: bytes) -> object:
    if len(value) < 8 or value[:2] != b"GP":
        raise ValueError("GeoPackage geometry is invalid")
    envelope = (value[3] >> 1) & 0b111
    envelope_sizes = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
    if envelope not in envelope_sizes:
        raise ValueError("GeoPackage geometry has an unsupported envelope")
    return from_wkb(value[8 + envelope_sizes[envelope]:])


def geofabrik_features(connection: sqlite3.Connection, maximum_zoom: int) -> list[dict[str, object]]:
    features = []
    layers = [
        (
            GEOFABRIK_WATER_LAYER,
            "fclass",
            lambda _: ("osm-water", 8, None),
        ),
        (
            GEOFABRIK_LANDUSE_LAYER,
            "fclass",
            lambda value: ("landuse", 0, value)
            if value in GEOFABRIK_URBAN_AREAS | GEOFABRIK_GREEN_AREAS else None,
        ),
        (
            GEOFABRIK_ROADS_LAYER,
            "fclass",
            lambda value: ("osm-road", minimum_zoom_for_feature({"highway": value}, "osm-road"), value)
            if value in GEOFABRIK_ROADS else None,
        ),
        (
            GEOFABRIK_PLACES_LAYER,
            "fclass",
            lambda value: ("osm-place", place_minimum_zoom(value), None)
            if place_minimum_zoom(value) is not None else None,
        ),
    ]
    available_tables = {
        row[0]
        for row in connection.execute(
            "SELECT table_name FROM gpkg_contents"
        )
    }
    for table, classification_column, classify in layers:
        if table not in available_tables:
            continue
        rows = connection.execute(
            f'SELECT geom, {classification_column}, name FROM "{table}"'
        )
        for geometry_data, classification, name in rows:
            result = classify(classification)
            if result is None:
                continue
            layer, minimum_zoom, highway = result
            if not isinstance(geometry_data, bytes):
                continue
            geometry = geofabrik_geometry(geometry_data)
            if geometry.is_empty or not geometry.is_valid:
                continue
            if layer == "landuse":
                large_area = geometry.area >= LARGE_AREA_DEGREES_SQUARED
                if classification in GEOFABRIK_URBAN_AREAS:
                    layer = "osm-urban"
                else:
                    layer = "osm-green"
                minimum_zoom = 8 if large_area else 12
            if minimum_zoom > maximum_zoom:
                continue
            tolerance = (
                0.0001
                if layer in {"osm-water", "osm-green", "osm-urban"}
                else simplification_tolerance(minimum_zoom)
            )
            geometry = geometry.simplify(tolerance, preserve_topology=True)
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "layer": layer,
                        "name": name,
                        "highway": highway,
                        "minZoom": minimum_zoom,
                    },
                    "geometry": mapping(geometry),
                }
            )
    return features


def geofabrik_bounds(connection: sqlite3.Connection) -> Bounds:
    rows = connection.execute(
        "SELECT min_x, min_y, max_x, max_y FROM gpkg_contents "
        "WHERE table_name IN (?, ?, ?)",
        (GEOFABRIK_WATER_LAYER, GEOFABRIK_LANDUSE_LAYER, GEOFABRIK_ROADS_LAYER),
    ).fetchall()
    if not rows or any(value is None for row in rows for value in row):
        raise ValueError("GeoPackage does not define the required layer extents")
    return Bounds(
        min(row[0] for row in rows),
        min(row[1] for row in rows),
        max(row[2] for row in rows),
        max(row[3] for row in rows),
    )


def install_geofabrik_gpkg(arguments: argparse.Namespace) -> None:
    source = arguments.source
    progress = getattr(arguments, "progress", None)
    if not source.is_file():
        raise FileNotFoundError(f"GeoPackage source is missing: {source}")
    if progress:
        progress("Inspecting GeoPackage source.", 5)
    with tempfile.TemporaryDirectory() as temporary:
        temporary_directory = Path(temporary)
        if source.suffix.lower() == ".zip":
            if progress:
                progress("Extracting GeoPackage archive.", 15)
            with zipfile.ZipFile(source) as archive:
                entries = [
                    entry for entry in archive.infolist()
                    if not entry.is_dir() and Path(entry.filename).suffix.lower() == ".gpkg"
                ]
                if len(entries) != 1 or Path(entries[0].filename).name != entries[0].filename:
                    raise ValueError("GeoPackage ZIP must contain exactly one top-level .gpkg file")
                package_path = temporary_directory / entries[0].filename
                with archive.open(entries[0]) as input_stream, package_path.open("wb") as output_stream:
                    shutil.copyfileobj(input_stream, output_stream)
        elif source.suffix.lower() == ".gpkg":
            package_path = source
        else:
            raise ValueError("GeoPackage source must be a .gpkg or .zip file")
        if progress:
            progress("Reading water, land-use, woodland, and roads.", 35)
        connection = sqlite3.connect(package_path)
        try:
            bounds = geofabrik_bounds(connection)
            features = geofabrik_features(connection, arguments.max_zoom)
        finally:
            connection.close()
    if progress:
        progress("Loading supporting land polygons.", 55)
    land_features = land_features_from_polygons(
        cached_land_polygons(), bounds, arguments.min_zoom
    )
    features.extend(land_features)
    if not features:
        raise ValueError("GeoPackage did not contain supported water, woodland, or major-road features")
    if progress:
        progress("Preparing native Vector tile coverage.", 65)
    tiles = tiles_for_bounds(bounds, arguments.coverage_zoom)
    install_package(
        arguments.id,
        arguments.coverage_zoom,
        arguments.min_zoom,
        arguments.max_zoom,
        tiles,
        features,
        "Geofabrik OpenStreetMap extract",
        source.name,
        land_features,
        f"{source.name}; {LAND_POLYGONS_URL}",
        "replace",
        arguments.replace,
        progress,
    )
    print(
        f"Installed {len(features)} features from {source} in "
        f"{MAPS_DIRECTORY / arguments.id}"
    )


def osm_scan_status_message(
    item_count: int, node_count: int, way_count: int, retained_count: int
) -> str:
    return (
        "Scanning OSM PBF features "
        f"({item_count:,} items: {node_count:,} nodes, {way_count:,} ways; "
        f"{retained_count:,} retained; total unavailable)."
    )


def install_osm_pbf(arguments: argparse.Namespace) -> None:
    features: list[dict[str, object]] = []
    progress = getattr(arguments, "progress", None)

    class FeatureHandler(osmium.SimpleHandler):
        def __init__(self) -> None:
            super().__init__()
            self.item_count = 0
            self.node_count = 0
            self.way_count = 0
            self.next_progress_report = 25_000

        def report_progress(self) -> None:
            if progress and self.item_count >= self.next_progress_report:
                progress(osm_scan_status_message(
                    self.item_count, self.node_count, self.way_count, len(features)
                ), 5)
                self.next_progress_report += 25_000

        def way(self, way: object) -> None:
            self.item_count += 1
            self.way_count += 1
            self.report_progress()
            tags = dict(way.tags)
            highway = tags.get("highway", "")
            layer = osm_layer(tags, [])
            if layer == "osm-road" and highway in GEOFABRIK_ROADS:
                minimum_zoom = minimum_zoom_for_feature(
                    {"highway": highway}, layer
                )
            elif layer == "osm-forest":
                minimum_zoom = 8
            elif layer == "osm-urban":
                minimum_zoom = 8
            else:
                return
            try:
                coordinates = [(node.location.lon, node.location.lat) for node in way.nodes]
            except osmium.InvalidLocationError:
                return
            if minimum_zoom > arguments.max_zoom or len(coordinates) < 2:
                return
            if layer == "osm-road":
                geometry = LineString(coordinates).simplify(
                    simplification_tolerance(minimum_zoom), preserve_topology=True
                )
            else:
                if len(coordinates) < 4 or coordinates[0] != coordinates[-1]:
                    return
                geometry = Polygon(coordinates)
                if not geometry.is_valid:
                    return
                if layer == "osm-urban" and geometry.area < LARGE_AREA_DEGREES_SQUARED:
                    minimum_zoom = 12
                geometry = geometry.simplify(0.0001, preserve_topology=True)
            if not geometry.is_empty:
                features.append({
                    "type": "Feature",
                    "properties": {
                        "layer": layer,
                        "name": tags.get("name"),
                        "highway": highway if layer == "osm-road" else None,
                        "minZoom": minimum_zoom,
                    },
                    "geometry": mapping(geometry),
                })

        def node(self, node: object) -> None:
            self.item_count += 1
            self.node_count += 1
            self.report_progress()
            tags = dict(node.tags)
            place = tags.get("place", "")
            minimum_zoom = place_minimum_zoom(place)
            name = tags.get("name")
            if minimum_zoom is None or not name or minimum_zoom > arguments.max_zoom:
                return
            try:
                location = node.location
                longitude, latitude = location.lon, location.lat
            except osmium.InvalidLocationError:
                return
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "layer": "osm-place",
                        "name": name,
                        "place": place,
                        "minZoom": minimum_zoom,
                    },
                    "geometry": mapping(Point(longitude, latitude)),
                }
            )

    if progress:
        progress("Scanning OSM PBF features.", 5)
    FeatureHandler().apply_file(str(arguments.source), locations=True)
    if not features:
        raise ValueError("PBF did not contain supported major roads, urban areas, or woodland")
    if progress:
        progress(f"Preparing {len(features):,} retained OSM features.", 50)
    bounds = feature_bounds(features)
    land_features = land_features_from_polygons(
        cached_land_polygons(), bounds, arguments.min_zoom
    )
    features.extend(land_features)
    if progress:
        progress("Loading supporting land polygons.", 60)
    tiles = tiles_for_bounds(bounds, arguments.coverage_zoom)
    install_package(
        arguments.id, arguments.coverage_zoom, arguments.min_zoom, arguments.max_zoom,
        tiles, features, "OpenStreetMap PBF extract", arguments.source.name, land_features,
        f"{arguments.source.name}; {LAND_POLYGONS_URL}", "replace", arguments.replace,
        progress,
    )
    print(
        f"Installed {len(features)} major-road, urban-area, and woodland features "
        f"from {arguments.source} in {MAPS_DIRECTORY / arguments.id}"
    )


def simplification_tolerance(minimum_zoom: int) -> float:
    return {
        8: 0.002,
        10: 0.004,
        12: 0.001,
        14: 0.0003,
        15: 0.00015,
        16: 0.00008,
    }[minimum_zoom]


def feature_bounds(features: list[dict[str, object]]) -> Bounds:
    bounds = [shape(feature["geometry"]).bounds for feature in features]
    return Bounds(
        min(item[0] for item in bounds),
        min(item[1] for item in bounds),
        max(item[2] for item in bounds),
        max(item[3] for item in bounds),
    )


def clip_features(features: list[dict[str, object]], bounds: Bounds) -> list[dict[str, object]]:
    coverage = box(bounds.west, bounds.south, bounds.east, bounds.north)
    clipped_features = []
    for feature in features:
        geometry = shape(feature["geometry"])
        clipped = geometry.intersection(coverage)
        if not clipped.is_empty:
            clipped_features.append({**feature, "geometry": mapping(clipped)})
    return clipped_features


def install_package(
    package_id: str,
    coverage_zoom: int,
    min_zoom: int,
    max_zoom: int,
    tiles: list[tuple[int, int]],
    osm_features: list[dict[str, object]],
    source_provider: str,
    source_url: str,
    land_features: list[dict[str, object]],
    land_source: str,
    mode: str,
    replace: bool,
    progress: Callable[[str, int], None] | None = None,
) -> Path:
    if progress:
        progress(f"Writing {len(osm_features):,} features to the GeoPackage library.", 75)
    store = NativeTileStore(MAPS_DIRECTORY)
    store.replace_source(package_id, osm_features)
    if progress:
        progress("Finalizing GeoPackage spatial index.", 95)
    return store.path


def write_preparation_snapshot(
    package_directory: Path, land_features: list[dict[str, object]]
) -> None:
    source_directory = package_directory / "sources"
    source_directory.mkdir()
    with gzip.open(source_directory / "land.geojson.gz", "wt", encoding="utf-8") as stream:
        json.dump(
            {"type": "FeatureCollection", "features": land_features},
            stream,
            separators=(",", ":"),
        )


def write_lod_tiles(
    package_directory: Path,
    features: list[dict[str, object]],
    coverage_zoom: int,
    coverage_tiles: list[tuple[int, int]],
    lods: list[int],
    progress: Callable[[str, int], None] | None = None,
) -> int:
    geometries = [shape(feature["geometry"]) for feature in features]
    tree = STRtree(geometries)
    written_tiles = 0
    for lod_index, lod in enumerate(lods, start=1):
        if progress:
            progress(
                f"Writing native LOD {lod} ({lod_index} of {len(lods)}).",
                70 + round(lod_index / len(lods) * 25),
            )
        if lod < coverage_zoom:
            shift = coverage_zoom - lod
            tile_coordinates = {
                (coverage_x >> shift, coverage_y >> shift)
                for coverage_x, coverage_y in coverage_tiles
            }
        else:
            scale = 2 ** (lod - coverage_zoom)
            tile_coordinates = {
                (x, y)
                for coverage_x, coverage_y in coverage_tiles
                for y in range(coverage_y * scale, (coverage_y + 1) * scale)
                for x in range(coverage_x * scale, (coverage_x + 1) * scale)
            }
        for x, y in tile_coordinates:
            bounds = MapMath.tile_bounds(lod, x, y, buffer_pixels=2)
            tile_geometry = box(bounds.west, bounds.south, bounds.east, bounds.north)
            tile_features = []
            for index in tree.query(tile_geometry):
                feature = features[int(index)]
                if feature["properties"].get("minZoom", 0) > lod:
                    continue
                clipped = geometries[int(index)].intersection(tile_geometry)
                if not clipped.is_empty:
                    tile_features.append({**feature, "geometry": mapping(clipped)})
            if tile_features:
                path = package_directory / "tiles" / str(lod) / str(x)
                path.mkdir(parents=True, exist_ok=True)
                with gzip.open(path / f"{y}.geojson.gz", "wt", encoding="utf-8") as stream:
                    json.dump({"type": "FeatureCollection", "features": tile_features}, stream, separators=(",", ":"))
                written_tiles += 1
    return written_tiles


def rebuild_lod_pyramid(package_directory: Path) -> int:
    """Fill each missing local vector LOD from a package's most detailed tiles."""

    manifest_path = package_directory / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"map package manifest is missing: {manifest_path}")
    manifest_document = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = PackageManifest.model_validate(manifest_document)
    if not manifest.lods:
        return 0

    source_lod = max(manifest.lods)
    source_directory = package_directory / "tiles" / str(source_lod)
    if not source_directory.is_dir():
        raise FileNotFoundError(
            f"map package {manifest.id} has no tiles for its most detailed LOD {source_lod}"
        )
    features = []
    for path in source_directory.glob("*/*.geojson.gz"):
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            features.extend(json.load(stream).get("features", []))

    lods = list(range(manifest.min_zoom, manifest.max_zoom + 1))
    missing_lods = [lod for lod in lods if lod not in manifest.lods]
    if not missing_lods:
        return 0
    coverage_tiles = [tuple(int(part) for part in tile.split("/")[1:]) for tile in manifest.tiles]
    written_tiles = write_lod_tiles(
        package_directory,
        features,
        manifest.coverage_zoom,
        coverage_tiles,
        missing_lods,
    )
    manifest_document["lods"] = lods
    manifest_document["pyramidRebuiltAt"] = datetime.now(UTC).isoformat()
    temporary_path = manifest_path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(manifest_document, indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(manifest_path)
    return written_tiles


def rebuild_all_lod_pyramids() -> tuple[int, int]:
    """Verify the GeoPackage-backed Vector library."""
    store = NativeTileStore(MAPS_DIRECTORY)
    feature_count = store.feature_count()
    return (1 if feature_count else 0), feature_count


def build_native_overview_lods(store: NativeTileStore, minimum_zoom: int = 0) -> int:
    """Create low-zoom overview tiles from the first detailed native LOD."""

    zoom_directories = [
        directory
        for directory in store.directory.iterdir()
        if directory.is_dir() and directory.name.isdigit() and int(directory.name) >= 8
    ] if store.directory.is_dir() else []
    if not zoom_directories:
        return 0
    source_zoom = min(int(directory.name) for directory in zoom_directories)
    source_directory = store.directory / str(source_zoom)
    coverage_tiles = []
    features = []
    for tile_path in source_directory.glob("*/*.geojson.gz"):
        coverage_tiles.append((int(tile_path.parent.name), int(tile_path.name.removesuffix(".geojson.gz"))))
        with gzip.open(tile_path, "rt", encoding="utf-8") as stream:
            for feature in json.load(stream).get("features", []):
                properties = feature.get("properties", {})
                features.append(
                    {
                        **feature,
                        "properties": {**properties, "minZoom": minimum_zoom},
                    }
                )
    if not coverage_tiles or not features:
        return 0
    with tempfile.TemporaryDirectory(dir=MAPS_DIRECTORY, prefix=".overview-") as temporary:
        temporary_directory = Path(temporary)
        written_tiles = write_lod_tiles(
            temporary_directory,
            features,
            source_zoom,
            coverage_tiles,
            list(range(minimum_zoom, source_zoom)),
        )
        store.merge_tile_directory(temporary_directory / "tiles")
    return written_tiles


def install_osm(arguments: argparse.Namespace) -> None:
    if not arguments.id.replace("-", "").isalnum() or arguments.id.startswith("-") or arguments.id.endswith("-"):
        raise ValueError("package id must contain lowercase letters, numbers, and interior hyphens only")
    tiles = (
        tiles_for_bounds(parse_bounding_box(arguments.bbox), arguments.coverage_zoom)
        if arguments.bbox
        else parse_coverage_tiles(arguments.tiles, arguments.coverage_zoom)
    )
    bounds = coverage_bounds(arguments.coverage_zoom, tiles)
    sample_tile = MapMath.tile_bounds(arguments.coverage_zoom, *tiles[0])
    coastline_margin = max(sample_tile.east - sample_tile.west, sample_tile.north - sample_tile.south)
    endpoints = (arguments.endpoint,) if arguments.endpoint else DEFAULT_OVERPASS_ENDPOINTS
    progress = getattr(arguments, "progress", None)
    features, source_url = download_osm_features(
        endpoints, bounds, arguments.max_zoom, coastline_margin, progress
    )
    land_source_path = arguments.land_polygons
    if arguments.regional_coastline:
        land_features = coastline_land_features(features, arguments.min_zoom)
    elif arguments.land_polygons_url:
        with tempfile.TemporaryDirectory() as temporary:
            land_source_path = Path(temporary) / "land-polygons.zip"
            download_land_polygons(arguments.land_polygons_url, land_source_path)
            land_features = land_features_from_polygons(
                land_source_path, bounds, arguments.min_zoom
            )
    else:
        land_features = land_features_from_polygons(
            land_source_path, bounds, arguments.min_zoom
        )
    features = clip_forests_to_land(features, land_features) + land_features
    if progress:
        progress("Building local vector tiles and LOD packages.", 75)
    destination = install_package(
        arguments.id,
        arguments.coverage_zoom,
        arguments.min_zoom,
        arguments.max_zoom,
        tiles,
        features,
        "OpenStreetMap via Overpass API",
        source_url,
        land_features,
        (
            "regional OSM coastline linework"
            if arguments.regional_coastline
            else arguments.land_polygons_url or str(land_source_path)
        ),
        "overlay" if arguments.regional_coastline else "replace",
        arguments.replace,
    )
    if progress:
        progress("Finalizing local vector package.", 95)
    print(
        f"Installed {len(features)} OSM features in {destination}; "
        f"coverage tiles: {', '.join(f'{arguments.coverage_zoom}/{x}/{y}' for x, y in tiles)}"
    )


def rebuild_lod(arguments: argparse.Namespace) -> None:
    raise ValueError(
        "native Vector tiles do not retain per-import identities; import the source again to refresh its coverage"
    )


def prepare_land_polygons(arguments: argparse.Namespace) -> None:
    if arguments.output.is_file():
        print(f"Global OSM land polygons already exist: {arguments.output}")
        return
    download_land_polygons(arguments.url, arguments.output)
    print(f"Downloaded OSM land polygons: {arguments.output}")


def list_packages(_: argparse.Namespace) -> None:
    tile_directory = NativeTileStore(MAPS_DIRECTORY).directory
    if not tile_directory.is_dir():
        return
    tile_count = sum(1 for _ in tile_directory.glob("*/*/*.geojson.gz"))
    print(f"{tile_count} native Vector tile(s) in {tile_directory}")


def remove_package(arguments: argparse.Namespace) -> None:
    raise ValueError(
        "native Vector tiles do not retain per-import identities and cannot remove one import by id"
    )


def install_world_raster(arguments: argparse.Namespace) -> None:
    source = arguments.source
    if not source.is_file():
        raise FileNotFoundError(f"world raster source is missing: {source}")
    Image.MAX_IMAGE_PIXELS = None
    with Image.open(source) as image:
        width, height = image.size
        if abs(width - height) / max(width, height) > 0.01:
            raise ValueError("world raster must be a square Web Mercator image within 1% tolerance")
        maximum_zoom = arguments.max_zoom
        if maximum_zoom is None:
            maximum_zoom = round(log2(min(width, height) / 256))
        if not 0 <= maximum_zoom <= 18:
            raise ValueError("max zoom must be between 0 and 18")
        target_size = 256 * 2**maximum_zoom
        source_image = image.convert("RGB")
        if source_image.size != (target_size, target_size):
            source_image = source_image.resize(
                (target_size, target_size), Image.Resampling.LANCZOS
            )

    destination = arguments.output
    if destination.exists() and any(destination.iterdir()) and not arguments.replace:
        raise FileExistsError(
            f"raster tile directory already exists: {destination}; pass --replace to update it"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".satellite-") as temporary:
        temporary_directory = Path(temporary)
        for zoom in range(maximum_zoom + 1):
            tiles_per_axis = 2**zoom
            level_size = 256 * tiles_per_axis
            level_image = source_image if level_size == target_size else source_image.resize(
                (level_size, level_size), Image.Resampling.LANCZOS
            )
            for y in range(tiles_per_axis):
                for x in range(tiles_per_axis):
                    tile_directory = temporary_directory / str(zoom) / str(x)
                    tile_directory.mkdir(parents=True, exist_ok=True)
                    level_image.crop(
                        (x * 256, y * 256, (x + 1) * 256, (y + 1) * 256)
                    ).save(tile_directory / f"{y}.png", optimize=True)
            if level_image is not source_image:
                level_image.close()
        backup = destination.parent / f".{destination.name}-previous"
        if backup.exists():
            shutil.rmtree(backup)
        if destination.exists():
            destination.replace(backup)
        try:
            temporary_directory.replace(destination)
        except OSError:
            if backup.exists():
                backup.replace(destination)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    source_image.close()
    update_raster_revision(destination)
    print(f"Installed Web Mercator raster tiles through zoom {maximum_zoom} in {destination}")


def update_raster_revision(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    revision = directory / ".revision"
    temporary = revision.with_suffix(".tmp")
    temporary.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
    temporary.replace(revision)


def read_world_file(path: Path) -> tuple[float, float, float, float, float, float]:
    if not path.is_file():
        raise FileNotFoundError(f"world file is missing: {path}")
    values = [float(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if len(values) != 6:
        raise ValueError("world file must contain six affine transform values")
    pixel_width, row_rotation, column_rotation, pixel_height, origin_x, origin_y = values
    if row_rotation != 0 or column_rotation != 0 or pixel_width <= 0 or pixel_height >= 0:
        raise ValueError("only north-up rasters with positive X and negative Y pixel sizes are supported")
    return pixel_width, row_rotation, column_rotation, pixel_height, origin_x, origin_y


def mercator_world_coordinate(value: float) -> float:
    return (value + WEB_MERCATOR_HALF_WORLD) / (2 * WEB_MERCATOR_HALF_WORLD)


def raster_zoom_for_resolution(metres_per_pixel: float) -> int:
    return round(log2(2 * WEB_MERCATOR_HALF_WORLD / 256 / metres_per_pixel))


def install_georeferenced_raster(arguments: argparse.Namespace) -> None:
    source = arguments.source
    projection = arguments.projection
    if not source.is_file():
        raise FileNotFoundError(f"raster source is missing: {source}")
    if not projection.is_file():
        raise FileNotFoundError(f"projection file is missing: {projection}")
    if "Mercator_Auxiliary_Sphere" not in projection.read_text(encoding="utf-8"):
        raise ValueError("only WGS 84 Web Mercator Auxiliary Sphere rasters are supported")
    pixel_width, _, _, pixel_height, origin_x, origin_y = read_world_file(arguments.world_file)
    native_zoom = raster_zoom_for_resolution(pixel_width)
    maximum_zoom = native_zoom if arguments.max_zoom is None else arguments.max_zoom
    minimum_zoom = maximum_zoom if arguments.min_zoom is None else arguments.min_zoom
    if not 0 <= minimum_zoom <= maximum_zoom <= 18:
        raise ValueError("0 <= min zoom <= max zoom <= 18 is required")

    Image.MAX_IMAGE_PIXELS = None
    with Image.open(source) as image:
        source_image = image.convert("RGBA")
    width, height = source_image.size
    source_left = origin_x - pixel_width / 2
    source_top = origin_y - pixel_height / 2
    source_right = source_left + width * pixel_width
    source_bottom = source_top + height * pixel_height

    output = arguments.output
    written_tiles = 0
    for zoom in range(minimum_zoom, maximum_zoom + 1):
        tiles_per_axis = 2**zoom
        minimum_x = max(0, floor(mercator_world_coordinate(source_left) * tiles_per_axis))
        maximum_x = min(tiles_per_axis - 1, ceil(mercator_world_coordinate(source_right) * tiles_per_axis) - 1)
        minimum_y = max(0, floor((WEB_MERCATOR_HALF_WORLD - source_top) / (2 * WEB_MERCATOR_HALF_WORLD) * tiles_per_axis))
        maximum_y = min(tiles_per_axis - 1, ceil((WEB_MERCATOR_HALF_WORLD - source_bottom) / (2 * WEB_MERCATOR_HALF_WORLD) * tiles_per_axis) - 1)
        tile_span = 2 * WEB_MERCATOR_HALF_WORLD / tiles_per_axis
        for y in range(minimum_y, maximum_y + 1):
            tile_top = WEB_MERCATOR_HALF_WORLD - y * tile_span
            tile_bottom = tile_top - tile_span
            for x in range(minimum_x, maximum_x + 1):
                tile_left = -WEB_MERCATOR_HALF_WORLD + x * tile_span
                tile_right = tile_left + tile_span
                left = max(tile_left, source_left)
                right = min(tile_right, source_right)
                top = min(tile_top, source_top)
                bottom = max(tile_bottom, source_bottom)
                if left >= right or bottom >= top:
                    continue
                source_box = (
                    (left - source_left) / pixel_width,
                    (source_top - top) / -pixel_height,
                    (right - source_left) / pixel_width,
                    (source_top - bottom) / -pixel_height,
                )
                output_box = (
                    round((left - tile_left) / tile_span * 256),
                    round((tile_top - top) / tile_span * 256),
                    round((right - tile_left) / tile_span * 256),
                    round((tile_top - bottom) / tile_span * 256),
                )
                tile = Image.new("RGBA", (256, 256))
                fragment = source_image.crop(source_box).resize(
                    (output_box[2] - output_box[0], output_box[3] - output_box[1]),
                    Image.Resampling.LANCZOS,
                )
                tile.alpha_composite(fragment, (output_box[0], output_box[1]))
                destination = output / str(zoom) / str(x) / f"{y}.png"
                if destination.exists() and not arguments.replace:
                    raise FileExistsError(
                        f"raster tile already exists: {destination}; pass --replace to update it"
                    )
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_suffix(".tmp")
                tile.save(temporary, format="PNG", optimize=True)
                temporary.replace(destination)
                written_tiles += 1
    source_image.close()
    update_raster_revision(output)
    print(
        f"Installed {written_tiles} Web Mercator raster tiles at zooms "
        f"{minimum_zoom}-{maximum_zoom} in {output}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build local Kody map packages.")
    commands = parser.add_subparsers(dest="command", required=True)
    install_osm_parser = commands.add_parser("install-osm", help="Download and install OSM roads and buildings.")
    install_osm_parser.add_argument("--id", required=True)
    install_osm_parser.add_argument("--coverage-zoom", required=True, type=int, choices=range(0, 19))
    coverage = install_osm_parser.add_mutually_exclusive_group(required=True)
    coverage.add_argument(
        "--bbox",
        help="Requested west,south,east,north area; expanded to complete coverage-zoom tiles",
    )
    coverage.add_argument(
        "--tiles",
        help="Advanced override: comma-separated coverage tiles, such as 10/511/341",
    )
    install_osm_parser.add_argument("--min-zoom", type=int, default=None)
    install_osm_parser.add_argument("--max-zoom", type=int, default=16, choices=range(0, 19))
    install_osm_parser.add_argument("--endpoint", help="Use one specific Overpass-compatible endpoint")
    install_osm_parser.add_argument(
        "--land-polygons",
        type=Path,
        help="Path to the OSM land-polygon ZIP for this package area",
    )
    install_osm_parser.add_argument(
        "--land-polygons-url",
        help="Download the OSM land-polygon ZIP for this package area before preparation",
    )
    install_osm_parser.add_argument(
        "--regional-coastline",
        action="store_true",
        help="Build an additive package from closed regional OSM coastline rings",
    )
    install_osm_parser.add_argument("--replace", action="store_true")
    install_osm_parser.set_defaults(handler=install_osm)
    install_geofabrik_parser = commands.add_parser(
        "install-geofabrik-gpkg",
        help="Install selected water, woodland, and major-road layers from a Geofabrik GeoPackage.",
    )
    install_geofabrik_parser.add_argument("--id", required=True)
    install_geofabrik_parser.add_argument("--source", type=Path, required=True)
    install_geofabrik_parser.add_argument("--coverage-zoom", type=int, default=11, choices=range(0, 19))
    install_geofabrik_parser.add_argument("--min-zoom", type=int, default=8, choices=range(0, 19))
    install_geofabrik_parser.add_argument("--max-zoom", type=int, default=14, choices=range(0, 19))
    install_geofabrik_parser.add_argument("--replace", action="store_true")
    install_geofabrik_parser.set_defaults(handler=install_geofabrik_gpkg)
    install_pbf_parser = commands.add_parser(
        "install-osm-pbf",
        help="Stream major roads from an OSM PBF and build a local coastline-aware LOD package.",
    )
    install_pbf_parser.add_argument("--id", required=True)
    install_pbf_parser.add_argument("--source", type=Path, required=True)
    install_pbf_parser.add_argument("--coverage-zoom", type=int, default=11, choices=range(0, 19))
    install_pbf_parser.add_argument("--min-zoom", type=int, default=8, choices=range(0, 19))
    install_pbf_parser.add_argument("--max-zoom", type=int, default=14, choices=range(0, 19))
    install_pbf_parser.add_argument("--replace", action="store_true")
    install_pbf_parser.set_defaults(handler=install_osm_pbf)
    rebuild_lod_parser = commands.add_parser(
        "rebuild-lod", help="Rebuild an installed package's local OSM LOD without downloading."
    )
    rebuild_lod_parser.add_argument("--id", required=True)
    rebuild_lod_parser.set_defaults(handler=rebuild_lod)
    land_polygons_parser = commands.add_parser(
        "prepare-land-polygons", help="Download an OSM land-polygon source for one package area."
    )
    land_polygons_parser.add_argument("--url", required=True)
    land_polygons_parser.add_argument("--output", type=Path, required=True)
    land_polygons_parser.set_defaults(handler=prepare_land_polygons)
    list_packages_parser = commands.add_parser("list", help="List installed detailed map packages.")
    list_packages_parser.set_defaults(handler=list_packages)
    remove_package_parser = commands.add_parser("remove", help="Remove one detailed map package.")
    remove_package_parser.add_argument("--id", required=True)
    remove_package_parser.set_defaults(handler=remove_package)
    raster_parser = commands.add_parser(
        "install-world-raster", help="Slice a square Web Mercator world bitmap into local XYZ tiles."
    )
    raster_parser.add_argument("--source", type=Path, required=True)
    raster_parser.add_argument("--output", type=Path, required=True)
    raster_parser.add_argument("--max-zoom", type=int)
    raster_parser.add_argument("--replace", action="store_true")
    raster_parser.set_defaults(handler=install_world_raster)
    georeferenced_raster_parser = commands.add_parser(
        "install-georeferenced-raster",
        help="Import a north-up Web Mercator raster with PNG world and projection files.",
    )
    georeferenced_raster_parser.add_argument("--source", type=Path, required=True)
    georeferenced_raster_parser.add_argument("--world-file", type=Path, required=True)
    georeferenced_raster_parser.add_argument("--projection", type=Path, required=True)
    georeferenced_raster_parser.add_argument("--min-zoom", type=int)
    georeferenced_raster_parser.add_argument("--max-zoom", type=int)
    georeferenced_raster_parser.add_argument("--output", type=Path, required=True)
    georeferenced_raster_parser.add_argument("--replace", action="store_true")
    georeferenced_raster_parser.set_defaults(handler=install_georeferenced_raster)
    return parser


def main() -> None:
    parser = build_parser()
    arguments = parser.parse_args()
    if arguments.command == "install-osm":
        arguments.min_zoom = arguments.coverage_zoom if arguments.min_zoom is None else arguments.min_zoom
        if arguments.max_zoom < arguments.min_zoom:
            parser.error("min-zoom <= max-zoom is required")
        if not arguments.regional_coastline and arguments.min_zoom < arguments.coverage_zoom:
            parser.error("replace packages require coverage-zoom <= min-zoom")
        if arguments.regional_coastline:
            if arguments.land_polygons or arguments.land_polygons_url:
                parser.error("--regional-coastline cannot be combined with a land-polygon source")
        elif not bool(arguments.land_polygons) == bool(arguments.land_polygons_url):
            parser.error("specify exactly one of --land-polygons or --land-polygons-url")
    elif arguments.command == "rebuild-lod":
        if not arguments.id.replace("-", "").isalnum() or arguments.id.startswith("-") or arguments.id.endswith("-"):
            parser.error("package id must contain lowercase letters, numbers, and interior hyphens only")
    elif arguments.command == "remove":
        if not arguments.id.replace("-", "").isalnum() or arguments.id.startswith("-") or arguments.id.endswith("-"):
            parser.error("package id must contain lowercase letters, numbers, and interior hyphens only")
    arguments.handler(arguments)


if __name__ == "__main__":
    main()
