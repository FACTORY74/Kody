"""Discovery and selection of local, tile-aligned detailed map packages."""

from __future__ import annotations

import json
import logging
import gzip
import shutil
import sqlite3
import struct
from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, model_validator
from shapely import wkb
from shapely.geometry import box, mapping, shape

from .math import Bounds
from .quadtree import IndexedFeature, QuadTreeMap

LOGGER = logging.getLogger(__name__)
VECTOR_LIBRARY_FILE = "vector-library.gpkg"
GPKG_APPLICATION_ID = 0x47504B47


class PackageSource(BaseModel):
    provider: str
    licence: str
    attribution: str


class PackageManifest(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9-]+$", max_length=64)
    source: PackageSource
    coverage_zoom: int = Field(alias="coverageZoom", ge=0, le=18)
    min_zoom: int = Field(alias="minZoom", ge=0, le=18)
    max_zoom: int = Field(alias="maxZoom", ge=0, le=18)
    tiles: list[str] = Field(min_length=1)
    format: str = "geojson"
    mode: str = "replace"
    priority: int = 100
    lods: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_tile_coverage(self) -> "PackageManifest":
        if self.max_zoom < self.min_zoom:
            raise ValueError("minZoom <= maxZoom is required")
        if self.mode not in {"replace", "overlay"}:
            raise ValueError("package mode must be replace or overlay")
        if self.format != "geojson":
            raise ValueError("only geojson map packages are supported")
        for tile in self.tiles:
            try:
                zoom, x, y = (int(part) for part in tile.split("/"))
            except ValueError as error:
                raise ValueError(f"invalid package tile: {tile}") from error
            if zoom != self.coverage_zoom or not 0 <= x < 2**zoom or not 0 <= y < 2**zoom:
                raise ValueError(f"tile is outside coverageZoom: {tile}")
        return self


@dataclass(frozen=True, eq=False)
class MapPackage:
    manifest: PackageManifest
    index: QuadTreeMap | None
    directory: Path

    def owns_tile(self, zoom: int, x: int, y: int) -> bool:
        if self.manifest.mode != "replace" or not self.manifest.min_zoom <= zoom <= 18:
            return False
        return self.intersects_tile(zoom, x, y)

    def intersects_tile(self, zoom: int, x: int, y: int) -> bool:
        shift = zoom - self.manifest.coverage_zoom
        if shift >= 0:
            ancestor = f"{self.manifest.coverage_zoom}/{x >> shift}/{y >> shift}"
            return ancestor in self.manifest.tiles
        return f"{zoom}/{x}/{y}" in {
            f"{zoom}/{coverage_x >> -shift}/{coverage_y >> -shift}"
            for tile in self.manifest.tiles
            for _, coverage_x, coverage_y in [tuple(int(part) for part in tile.split("/"))]
        }

    @lru_cache(maxsize=256)
    def tile_collection(self, zoom: int, x: int, y: int) -> dict[str, object]:
        for lod in sorted((item for item in self.manifest.lods if item <= zoom), reverse=True):
            shift = zoom - lod
            path = self.directory / "tiles" / str(lod) / str(x >> shift) / f"{y >> shift}.geojson.gz"
            if path.is_file():
                with gzip.open(path, "rt", encoding="utf-8") as stream:
                    return json.load(stream)
        return {"type": "FeatureCollection", "features": []}


class PackageRegistry:
    """Loads valid detailed packages and resolves an owner for each map tile."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.packages = self._load_packages()

    def _load_packages(self) -> tuple[MapPackage, ...]:
        if not self.directory.exists():
            return ()
        packages = []
        for manifest_path in self.directory.glob("*/manifest.json"):
            try:
                manifest = PackageManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
                if manifest.lods:
                    packages.append(MapPackage(manifest, None, manifest_path.parent))
                    continue
                data_path = manifest_path.parent / "data.geojson"
                collection = json.loads(data_path.read_text(encoding="utf-8"))
                index = QuadTreeMap()
                for feature in collection.get("features", []):
                    geometry_data = feature.get("geometry")
                    if geometry_data:
                        geometry = shape(geometry_data)
                        if not geometry.is_empty:
                            index.insert(IndexedFeature(feature.get("properties", {}), geometry))
                packages.append(MapPackage(manifest, index, manifest_path.parent))
            except (OSError, ValueError, ValidationError, json.JSONDecodeError) as error:
                LOGGER.warning("Ignoring invalid map package %s: %s", manifest_path.parent, error)
        return tuple(sorted(packages, key=lambda package: (-package.manifest.priority, package.manifest.id)))

    def package_for_tile(self, zoom: int, x: int, y: int) -> MapPackage | None:
        return next((package for package in self.packages if package.owns_tile(zoom, x, y)), None)

    def overlays_for_tile(self, zoom: int, x: int, y: int) -> tuple[MapPackage, ...]:
        return tuple(
            package
            for package in self.packages
            if package.manifest.mode == "overlay"
            and package.manifest.min_zoom <= zoom <= 18
            and package.intersects_tile(zoom, x, y)
        )

    def attributions(self) -> list[str]:
        return sorted({package.manifest.source.attribution for package in self.packages})


class NativeTileStore:
    """GeoPackage-backed Vector feature library queried by display-tile bounds."""

    def __init__(self, maps_directory: Path) -> None:
        self.maps_directory = maps_directory
        self.path = maps_directory / VECTOR_LIBRARY_FILE

    def _connect(self) -> sqlite3.Connection:
        self.maps_directory.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(f"PRAGMA application_id = {GPKG_APPLICATION_ID}")
            connection.execute("PRAGMA user_version = 10300")
            connection.executescript(
                """
            CREATE TABLE IF NOT EXISTS gpkg_spatial_ref_sys (
                srs_name TEXT NOT NULL,
                srs_id INTEGER NOT NULL PRIMARY KEY,
                organization TEXT NOT NULL,
                organization_coordsys_id INTEGER NOT NULL,
                definition TEXT NOT NULL,
                description TEXT
            );
            INSERT OR IGNORE INTO gpkg_spatial_ref_sys
                (srs_name, srs_id, organization, organization_coordsys_id, definition, description)
            VALUES
                ('Undefined Cartesian', -1, 'NONE', -1, 'undefined', 'undefined Cartesian coordinate reference system'),
                ('Undefined geographic', 0, 'NONE', 0, 'undefined', 'undefined geographic coordinate reference system'),
                ('WGS 84 geodetic', 4326, 'EPSG', 4326, 'EPSG:4326', 'longitude/latitude coordinates');
            CREATE TABLE IF NOT EXISTS gpkg_contents (
                table_name TEXT NOT NULL PRIMARY KEY,
                data_type TEXT NOT NULL,
                identifier TEXT UNIQUE,
                description TEXT DEFAULT '',
                last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                min_x DOUBLE,
                min_y DOUBLE,
                max_x DOUBLE,
                max_y DOUBLE,
                srs_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS gpkg_geometry_columns (
                table_name TEXT NOT NULL,
                column_name TEXT NOT NULL,
                geometry_type_name TEXT NOT NULL,
                srs_id INTEGER NOT NULL,
                z TINYINT NOT NULL DEFAULT 0,
                m TINYINT NOT NULL DEFAULT 0,
                PRIMARY KEY (table_name, column_name)
            );
            CREATE TABLE IF NOT EXISTS kody_vector_features (
                id INTEGER PRIMARY KEY,
                source_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL UNIQUE,
                properties_json TEXT NOT NULL,
                min_zoom INTEGER NOT NULL,
                geometry BLOB NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS rtree_kody_vector_features
            USING rtree(id, min_x, max_x, min_y, max_y);
                """
            )
            connection.execute(
                """
            INSERT OR IGNORE INTO gpkg_contents
                (table_name, data_type, identifier, description, srs_id)
            VALUES ('kody_vector_features', 'features', 'Kody Vector features',
                    'Kody offline Vector working library.', 4326)
                """
            )
            connection.execute(
                """
            INSERT OR IGNORE INTO gpkg_geometry_columns
                (table_name, column_name, geometry_type_name, srs_id)
            VALUES ('kody_vector_features', 'geometry', 'GEOMETRY', 4326)
                """
            )
        except Exception:
            connection.close()
            raise
        return connection

    @staticmethod
    def _geopackage_geometry(geometry: object) -> bytes:
        return b"GP\x00\x01" + struct.pack("<i", 4326) + geometry.wkb

    @staticmethod
    def _geometry_from_geopackage(value: bytes) -> object:
        if value[:2] != b"GP" or len(value) < 8:
            raise ValueError("Vector GeoPackage geometry is invalid")
        return wkb.loads(value[8:])

    def tile_collection(
        self, zoom: int, x: int, y: int
    ) -> tuple[dict[str, object], int] | None:
        if not self.path.is_file():
            return None
        from .math import MapMath

        bounds = MapMath.tile_bounds(zoom, x, y, buffer_pixels=2)
        tile_geometry = box(bounds.west, bounds.south, bounds.east, bounds.north)
        with closing(sqlite3.connect(self.path)) as connection:
            rows = connection.execute(
                """
                SELECT feature.properties_json, feature.geometry
                FROM kody_vector_features AS feature
                JOIN rtree_kody_vector_features AS spatial
                  ON spatial.id = feature.id
                WHERE feature.min_zoom <= ?
                  AND spatial.min_x <= ? AND spatial.max_x >= ?
                  AND spatial.min_y <= ? AND spatial.max_y >= ?
                """,
                (zoom, bounds.east, bounds.west, bounds.north, bounds.south),
            ).fetchall()
        if not rows:
            return None
        features = []
        for properties_json, geometry_value in rows:
            geometry = self._geometry_from_geopackage(geometry_value)
            clipped = geometry.intersection(tile_geometry)
            if not clipped.is_empty:
                features.append(
                    {
                        "type": "Feature",
                        "properties": json.loads(properties_json),
                        "geometry": mapping(clipped),
                    }
                )
        return ({"type": "FeatureCollection", "features": features}, zoom)

    def replace_source(self, source_id: str, features: list[dict[str, object]]) -> int:
        connection = self._connect()
        try:
            with connection:
                feature_ids = [
                    row[0]
                    for row in connection.execute(
                        "SELECT id FROM kody_vector_features WHERE source_id = ?",
                        (source_id,),
                    )
                ]
                if feature_ids:
                    connection.executemany(
                        "DELETE FROM rtree_kody_vector_features WHERE id = ?",
                        ((feature_id,) for feature_id in feature_ids),
                    )
                    connection.execute(
                        "DELETE FROM kody_vector_features WHERE source_id = ?",
                        (source_id,),
                    )
                for feature in features:
                    properties = feature.get("properties")
                    geometry_data = feature.get("geometry")
                    if not isinstance(properties, dict) or not isinstance(geometry_data, dict):
                        raise ValueError("Vector feature requires properties and geometry")
                    geometry = shape(geometry_data)
                    if geometry.is_empty:
                        continue
                    properties_json = json.dumps(
                        properties, sort_keys=True, separators=(",", ":")
                    )
                    fingerprint = json.dumps(
                        {"properties": properties, "geometry": geometry_data},
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    connection.execute(
                        """
                    INSERT INTO kody_vector_features
                        (source_id, fingerprint, properties_json, min_zoom, geometry)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(fingerprint) DO UPDATE SET
                        source_id = excluded.source_id,
                        properties_json = excluded.properties_json,
                        min_zoom = excluded.min_zoom,
                        geometry = excluded.geometry
                        """,
                        (
                            source_id,
                            fingerprint,
                            properties_json,
                            int(properties.get("minZoom", 0)),
                            self._geopackage_geometry(geometry),
                        ),
                    )
                    feature_id = connection.execute(
                        "SELECT id FROM kody_vector_features WHERE fingerprint = ?",
                        (fingerprint,),
                    ).fetchone()[0]
                    min_x, min_y, max_x, max_y = geometry.bounds
                    connection.execute(
                        """
                    INSERT OR REPLACE INTO rtree_kody_vector_features
                        (id, min_x, max_x, min_y, max_y)
                    VALUES (?, ?, ?, ?, ?)
                        """,
                        (feature_id, min_x, max_x, min_y, max_y),
                    )
        finally:
            connection.close()
        return len(features)

    def feature_count(self) -> int:
        if not self.path.is_file():
            return 0
        with closing(sqlite3.connect(self.path)) as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM kody_vector_features"
                ).fetchone()[0]
            )

    def merge_collection(
        self, zoom: int, x: int, y: int, collection: dict[str, object]
    ) -> None:
        incoming_features = collection.get("features", [])
        if not isinstance(incoming_features, list):
            raise ValueError("native vector tile features must be a list")
        source_id = "manual-" + json.dumps(
            incoming_features, sort_keys=True, separators=(",", ":")
        )
        self.replace_source(source_id, incoming_features)
