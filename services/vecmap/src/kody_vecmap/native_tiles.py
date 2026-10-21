"""GeoPackage-backed Natural Earth feature storage and display-tile queries."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
from contextlib import closing
from collections.abc import Iterable
from pathlib import Path

from shapely import make_valid, wkb
from shapely.geometry import box, mapping, shape

VECTOR_LIBRARY_FILE = "vector-library.gpkg"
GPKG_APPLICATION_ID = 0x47504B47
OVERVIEW_MAXIMUM_ZOOM = 8
OVERVIEW_TOLERANCE = 0.3515625
BASE_OVERVIEW_TOLERANCE = 0.03
WORLD_OVERVIEW_MAXIMUM_ZOOM = 1
WORLD_OVERVIEW_TOLERANCE = 5.0


class NativeTileStore:
    """GeoPackage-backed Vector feature library queried by display-tile bounds."""

    def __init__(self, maps_directory: Path, path: Path | None = None) -> None:
        self.maps_directory = maps_directory
        self.path           = path or maps_directory / VECTOR_LIBRARY_FILE

    def _connect(self) -> sqlite3.Connection:
        self.maps_directory.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
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
                geometry BLOB NOT NULL,
                overview_geometry BLOB,
                world_geometry BLOB
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
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(kody_vector_features)"
                )
            }
            if "overview_geometry" not in columns:
                connection.execute(
                    "ALTER TABLE kody_vector_features ADD COLUMN overview_geometry BLOB"
                )
            if "world_geometry" not in columns:
                connection.execute(
                    "ALTER TABLE kody_vector_features ADD COLUMN world_geometry BLOB"
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
        self,
        zoom: int,
        x: int,
        y: int,
        buffer_pixels: int = 2,
        detail_zoom: int | None = None,
    ) -> tuple[dict[str, object], int] | None:
        if not self.path.is_file():
            return None
        from .math import MapMath

        bounds = MapMath.tile_bounds(zoom, x, y, buffer_pixels=buffer_pixels)
        requested_detail_zoom = zoom if detail_zoom is None else detail_zoom
        tile_geometry = box(bounds.west, bounds.south, bounds.east, bounds.north)
        with closing(sqlite3.connect(self.path)) as connection:
            rows = connection.execute(
                """
                SELECT
                    feature.properties_json,
                    CASE
                        WHEN ? <= ? AND feature.world_geometry IS NOT NULL
                        THEN feature.world_geometry
                        WHEN ? <= ? AND feature.overview_geometry IS NOT NULL
                        THEN feature.overview_geometry
                        ELSE feature.geometry
                    END
                FROM kody_vector_features AS feature
                JOIN rtree_kody_vector_features AS spatial
                  ON spatial.id = feature.id
                WHERE feature.min_zoom <= ?
                  AND spatial.min_x <= ? AND spatial.max_x >= ?
                  AND spatial.min_y <= ? AND spatial.max_y >= ?
                """,
                (
                    requested_detail_zoom,
                    WORLD_OVERVIEW_MAXIMUM_ZOOM,
                    requested_detail_zoom,
                    OVERVIEW_MAXIMUM_ZOOM,
                    requested_detail_zoom,
                    bounds.east,
                    bounds.west,
                    bounds.north,
                    bounds.south,
                ),
            ).fetchall()
        features = []
        for properties_json, geometry_value in rows:
            geometry = self._geometry_from_geopackage(geometry_value)
            if requested_detail_zoom > OVERVIEW_MAXIMUM_ZOOM:
                geometry = geometry.simplify(
                    OVERVIEW_TOLERANCE / (2**requested_detail_zoom),
                    preserve_topology=True,
                )
            clipped = geometry.intersection(tile_geometry)
            if not clipped.is_empty:
                features.append(
                    {
                        "type": "Feature",
                        "properties": json.loads(properties_json),
                        "geometry": mapping(clipped),
                    }
                )
        if not features:
            return None
        return ({"type": "FeatureCollection", "features": features}, requested_detail_zoom)

    def replace_source(
        self, source_id: str, features: Iterable[dict[str, object]]
    ) -> int:
        return self.replace_source_prefix(source_id, source_id, features)

    def replace_source_prefix(
        self,
        source_prefix: str,
        source_id: str,
        features: Iterable[dict[str, object]],
    ) -> int:
        connection = self._connect()
        try:
            with connection:
                source_ids = connection.execute(
                    """
                    SELECT DISTINCT source_id
                    FROM kody_vector_features
                    WHERE source_id LIKE ?
                    """,
                    (f"{source_prefix}%",),
                ).fetchall()
                for (previous_source_id,) in source_ids:
                    self._delete_source(connection, previous_source_id)
                count = self._write_features(connection, source_id, features)
        finally:
            connection.close()
        return count

    @staticmethod
    def _delete_source(connection: sqlite3.Connection, source_id: str) -> None:
        connection.execute(
            """
            DELETE FROM rtree_kody_vector_features
            WHERE id IN (
                SELECT id FROM kody_vector_features WHERE source_id = ?
            )
            """,
            (source_id,),
        )
        connection.execute(
            "DELETE FROM kody_vector_features WHERE source_id = ?",
            (source_id,),
        )

    def _write_features(
        self,
        connection: sqlite3.Connection,
        source_id: str,
        features: Iterable[dict[str, object]],
    ) -> int:
        count = 0
        for feature in features:
            properties = feature.get("properties")
            geometry_data = feature.get("geometry")
            if not isinstance(properties, dict) or not isinstance(geometry_data, dict):
                raise ValueError("Vector feature requires properties and geometry")
            geometry = shape(geometry_data)
            if geometry.is_empty:
                continue
            if not geometry.is_valid:
                geometry = make_valid(geometry)
                if geometry.is_empty:
                    continue
            properties_json = json.dumps(
                properties, sort_keys=True, separators=(",", ":")
            )
            minimum_zoom = int(properties.get("minZoom", 0))
            overview_geometry = (
                self._geopackage_geometry(
                    geometry.simplify(
                        (
                            BASE_OVERVIEW_TOLERANCE
                            if minimum_zoom == 0
                            else OVERVIEW_TOLERANCE / (2**minimum_zoom)
                        ),
                        preserve_topology=True,
                    )
                )
                if minimum_zoom <= OVERVIEW_MAXIMUM_ZOOM
                else None
            )
            world_geometry = (
                self._geopackage_geometry(
                    geometry.simplify(WORLD_OVERVIEW_TOLERANCE, preserve_topology=True)
                )
                if minimum_zoom == 0
                else None
            )
            fingerprint = hashlib.sha256(
                properties_json.encode("utf-8") + b"\x00" + geometry.wkb
            ).hexdigest()
            connection.execute(
                """
                INSERT INTO kody_vector_features
                    (
                        source_id, fingerprint, properties_json, min_zoom, geometry,
                        overview_geometry, world_geometry
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fingerprint) DO UPDATE SET
                    source_id = excluded.source_id,
                    properties_json = excluded.properties_json,
                    min_zoom = excluded.min_zoom,
                    geometry = excluded.geometry,
                    overview_geometry = excluded.overview_geometry,
                    world_geometry = excluded.world_geometry
                """,
                (
                    source_id,
                    fingerprint,
                    properties_json,
                    minimum_zoom,
                    self._geopackage_geometry(geometry),
                    overview_geometry,
                    world_geometry,
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
            count += 1
        return count

    def build_world_overview(self, force: bool = False) -> None:
        connection = self._connect()
        try:
            with connection:
                rows = connection.execute(
                    """
                    SELECT id, geometry
                    FROM kody_vector_features
                    WHERE min_zoom = 0
                    """
                    + ("" if force else " AND world_geometry IS NULL") +
                    """
                    """
                ).fetchall()
                for feature_id, geometry_value in rows:
                    geometry = self._geometry_from_geopackage(geometry_value)
                    connection.execute(
                        """
                        UPDATE kody_vector_features
                        SET world_geometry = ?
                        WHERE id = ?
                        """,
                        (
                            self._geopackage_geometry(
                                geometry.simplify(
                                    WORLD_OVERVIEW_TOLERANCE,
                                    preserve_topology=True,
                                )
                            ),
                            feature_id,
                        ),
                    )
        finally:
            connection.close()

    def build_base_overview(self, force: bool = False) -> None:
        connection = self._connect()
        try:
            with connection:
                rows = connection.execute(
                    """
                    SELECT id, geometry
                    FROM kody_vector_features
                    WHERE min_zoom = 0
                    """
                    + ("" if force else " AND overview_geometry IS NULL")
                ).fetchall()
                for feature_id, geometry_value in rows:
                    geometry = self._geometry_from_geopackage(geometry_value)
                    connection.execute(
                        """
                        UPDATE kody_vector_features
                        SET overview_geometry = ?
                        WHERE id = ?
                        """,
                        (
                            self._geopackage_geometry(
                                geometry.simplify(
                                    BASE_OVERVIEW_TOLERANCE,
                                    preserve_topology=True,
                                )
                            ),
                            feature_id,
                        ),
                    )
        finally:
            connection.close()

    def feature_count(self) -> int:
        if not self.path.is_file():
            return 0
        with closing(sqlite3.connect(self.path)) as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM kody_vector_features"
                ).fetchone()[0]
            )
