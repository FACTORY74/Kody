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

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
import sqlite3
import tempfile
from threading import Event
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from contextlib import closing
from unittest.mock import patch

import shapefile
from PIL import Image
from shapely.geometry import LineString, Polygon, mapping

from kody_vecmap.native_tiles import NativeTileStore
from kody_vecmap.raster_tiles import (
    NaturalEarthRasterCache,
    _label_key,
    cache_revision,
    render_tile,
)
from kody_vecmap.server import (
    ConfigurationStore,
    download_natural_earth_archive,
    ImportState,
    RasterTileRenderQueue,
    VectorConfiguration,
    filter_vector_features,
    get_raster_tile,
    get_manifest,
    import_natural_earth_dataset,
    is_natural_earth_archive,
    natural_earth_archives,
    natural_earth_category,
    natural_earth_display_layer,
    natural_earth_features,
    natural_earth_tile_zooms,
)
from kody_vecmap.vector_theme import load_vector_theme


def write_point_archive(
    path: Path,
    archive_layer: str = "10m_cultural/ne_10m_populated_places",
    name: str = "Example",
    scalerank: int = 0,
) -> None:
    shp_stream = BytesIO()
    shx_stream = BytesIO()
    dbf_stream = BytesIO()
    writer = shapefile.Writer(
        shp=shp_stream,
        shx=shx_stream,
        dbf=dbf_stream,
        shapeType=shapefile.POINT,
    )
    writer.field("NAME", "C")
    writer.field("SCALERANK", "N", decimal=0)
    writer.point(12.5, 47.25)
    writer.record(name, scalerank)
    writer.close()

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for extension, stream in (
            ("shp", shp_stream),
            ("shx", shx_stream),
            ("dbf", dbf_stream),
        ):
            archive.writestr(f"{archive_layer}.{extension}", stream.getvalue())


class NaturalEarthImportTests(unittest.TestCase):
    def test_downloads_and_validates_public_archive_before_caching(self) -> None:
        class Response(BytesIO):
            def __init__(self, payload: bytes) -> None:
                super().__init__(payload)
                self.headers = {"Content-Length": str(len(payload))}

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *_: object) -> None:
                self.close()

            def geturl(self) -> str:
                return "https://maps.example.test/10m_cultural.zip"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.zip"
            write_point_archive(source)
            updates: list[tuple[str, int]] = []
            with (
                patch("kody_vecmap.server.IMPORTS_DIRECTORY", root / "imports"),
                patch(
                    "kody_vecmap.server.urlopen",
                    return_value=Response(source.read_bytes()),
                ),
            ):
                downloaded = download_natural_earth_archive(
                    "https://maps.example.test/10m_cultural.zip",
                    lambda message, progress: updates.append((message, progress)),
                    10,
                    20,
                )

            self.assertTrue(is_natural_earth_archive(downloaded))
            self.assertEqual(downloaded.read_bytes(), source.read_bytes())
            self.assertEqual(updates[-1][1], 20)

    def test_discovers_nested_natural_earth_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "10m_cultural.zip"
            write_point_archive(archive_path)
            unrelated_path = root / "unrelated.zip"
            with zipfile.ZipFile(unrelated_path, "w") as archive:
                archive.writestr("regions/regions.shp", b"")
                archive.writestr("regions/regions.shx", b"")
                archive.writestr("regions/regions.dbf", b"")

            sources = natural_earth_archives(root)
            self.assertTrue(is_natural_earth_archive(archive_path))
        self.assertEqual([source.name for source in sources], ["10m_cultural.zip"])

    def test_streams_features_and_uses_case_insensitive_name_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "10m_cultural.zip"
            write_point_archive(source, name="Named place", scalerank=3)

            feature = next(natural_earth_features(source))

        self.assertEqual(feature["properties"]["name"], "Named place")
        self.assertEqual(feature["properties"]["sourceLayer"], "ne_10m_populated_places")
        self.assertEqual(feature["properties"]["category"], "places")
        self.assertEqual(feature["properties"]["layer"], "ne-place")
        self.assertEqual(feature["properties"]["minZoom"], 7)
        self.assertEqual(feature["geometry"]["type"], "Point")

    def test_imports_and_replaces_both_configured_archives(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_point_archive(root / "10m_cultural.zip")
            write_point_archive(
                root / "10m_physical.zip",
                archive_layer="10m_physical/ne_10m_land",
                name="Land feature",
            )
            maps_directory = root / "maps"
            state = ImportState(root / "action-state.json")
            state.start()
            with (
                patch(
                    "kody_vecmap.server.configuration_store",
                    SimpleNamespace(
                        configuration=VectorConfiguration(
                            naturalEarthDirectory=str(root),
                            naturalEarthPhysicalUrl="",
                            naturalEarthCulturalUrl="",
                        )
                    ),
                ),
                patch("kody_vecmap.server.MAPS_DIRECTORY", maps_directory),
                patch("kody_vecmap.server.import_state", state),
            ):
                first_result = import_natural_earth_dataset()
                second_result = import_natural_earth_dataset()

            store = NativeTileStore(maps_directory)
            with store.path.open("rb") as database:
                self.assertTrue(database.read(16).startswith(b"SQLite format 3"))
            with closing(sqlite3.connect(store.path)) as connection:
                geometry = connection.execute(
                    "SELECT geometry FROM kody_vector_features LIMIT 1"
                ).fetchone()[0]
                self.assertTrue(geometry.startswith(b"GP"))
            self.assertEqual(store.feature_count(), 2)

        self.assertIn("2 Natural Earth features", first_result)
        self.assertIn("2 Natural Earth features", second_result)

    def test_failed_streamed_replacement_keeps_previous_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = NativeTileStore(Path(directory) / "maps")
            feature = {
                "properties": {"category": "places", "layer": "ne-place"},
                "geometry": {"type": "Point", "coordinates": [0, 0]},
            }
            store.replace_source_prefix("natural-earth", "natural-earth", [feature])

            def failed_features():
                yield feature
                raise ValueError("broken archive")

            with self.assertRaisesRegex(ValueError, "broken archive"):
                store.replace_source_prefix(
                    "natural-earth", "natural-earth", failed_features()
                )

            self.assertEqual(store.feature_count(), 1)

    def test_categories_and_display_layers_cover_natural_earth_families(self) -> None:
        samples = {
            "ne_10m_land": ("physical", "ne-land"),
            "ne_10m_coastline": ("coastline", "ne-coastline"),
            "ne_10m_rivers_lake_centerlines": ("hydrography", "ne-river"),
            "ne_10m_bathymetry_A_10000": ("bathymetry", "ne-bathymetry-10000"),
            "ne_10m_admin_0_countries": ("administrative", "ne-admin0"),
            "ne_10m_admin_2_label_points": ("administrative", "ne-admin2"),
            "ne_10m_populated_places": ("places", "ne-place"),
            "ne_10m_roads": ("transport", "ne-road"),
            "ne_10m_urban_areas": ("places", "ne-urban"),
            "ne_10m_parks_and_protected_lands_area": ("protected", "ne-protected"),
            "ne_10m_time_zones": ("timezones", "ne-timezone"),
            "ne_10m_graticules_10": ("graticules", "ne-graticule"),
        }
        theme = load_vector_theme()

        for source_layer, (category, layer) in samples.items():
            actual_category, _ = natural_earth_category(source_layer)
            self.assertEqual(actual_category, category)
            self.assertEqual(natural_earth_display_layer(source_layer, category), layer)
            self.assertIn(layer, theme.styles)
        self.assertEqual(
            natural_earth_category("ne_10m_bathymetry_A_10000"),
            ("bathymetry", 0),
        )

    def test_categories_filter_tiles_independently(self) -> None:
        collection = {
            "type": "FeatureCollection",
            "features": [
                {"properties": {"category": "physical", "layer": "ne-land"}},
                {"properties": {"category": "transport", "layer": "ne-road"}},
                {"properties": {"category": "places", "layer": "ne-place"}},
                {
                    "properties": {
                        "category": "places",
                        "layer": "ne-place",
                        "sourceLayer": "ne_10m_admin_2_label_points",
                    }
                },
            ],
        }

        filtered = filter_vector_features(
            collection,
            VectorConfiguration(
                showNaturalEarthPhysical=False,
                showNaturalEarthTransport=False,
            ),
        )

        self.assertEqual(
            [feature["properties"]["category"] for feature in filtered["features"]],
            ["places"],
        )

    def test_manifest_exposes_only_natural_earth_imports_and_controls(self) -> None:
        configuration = asyncio.run(get_manifest())["configuration"]
        field_keys = [field["key"] for field in configuration["fields"]]
        action_labels = [action["label"] for action in configuration["actions"]]

        self.assertIn("naturalEarthDirectory", field_keys)
        self.assertIn("naturalEarthTileZooms", field_keys)
        self.assertIn("showNaturalEarthAdministrative", field_keys)
        self.assertNotIn("sourceFilename", field_keys)
        self.assertEqual(action_labels, ["Import Natural Earth dataset", "Clear local data"])


class NativeTileStoreTests(unittest.TestCase):
    def test_label_key_normalizes_duplicate_place_names(self) -> None:
        self.assertEqual(_label_key({"name": "  Atlanta  "}), "atlanta")
        self.assertEqual(_label_key({"name": "ATLANTA"}), "atlanta")
        self.assertIsNone(_label_key({"name": "  "}))

    def test_queries_spatial_index_and_simplifies_for_display_scale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            maps_directory = Path(directory)
            store = NativeTileStore(maps_directory)
            coordinates = [
                [longitude, 20 + (longitude % 2) * 0.005]
                for longitude in range(-80, 81)
            ]
            feature = {
                "properties": {
                    "category": "transport",
                    "layer": "ne-road",
                    "minZoom": 0,
                },
                "geometry": mapping(LineString(coordinates)),
            }
            store.replace_source("natural-earth", [feature])

            result = store.tile_collection(0, 0, 0, buffer_pixels=0)
            self.assertEqual(store.feature_count(), 1)

        self.assertIsNotNone(result)
        collection, lod = result
        self.assertEqual(lod, 0)
        self.assertLess(
            len(collection["features"][0]["geometry"]["coordinates"]),
            len(coordinates),
        )
        self.assertFalse((maps_directory / "tiles").exists())

    def test_root_tile_uses_the_standard_overview_detail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = NativeTileStore(Path(directory))
            coordinates = [
                [longitude / 10, 20 + (longitude % 2) * 0.05]
                for longitude in range(-1_000, 1_001)
            ]
            store.replace_source(
                "natural-earth",
                [
                    {
                        "properties": {
                            "category": "physical",
                            "layer": "ne-coastline",
                            "minZoom": 0,
                        },
                        "geometry": mapping(LineString(coordinates)),
                    }
                ],
            )

            result = store.tile_collection(0, 0, 0, buffer_pixels=0)

        self.assertIsNotNone(result)
        collection, lod = result
        self.assertEqual(lod, 0)
        self.assertGreater(
            len(collection["features"][0]["geometry"]["coordinates"]),
            1_000,
        )

    def test_repairs_invalid_geometry_for_root_tiles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = NativeTileStore(Path(directory))
            store.replace_source(
                "natural-earth",
                [
                    {
                        "properties": {
                            "category": "bathymetry",
                            "layer": "ne-bathymetry-5000",
                            "minZoom": 0,
                        },
                        "geometry": mapping(
                            Polygon(
                                [
                                    (-10, -10),
                                    (10, 10),
                                    (-10, 10),
                                    (10, -10),
                                    (-10, -10),
                                ]
                            )
                        ),
                    }
                ],
            )

            result = store.tile_collection(0, 0, 0, buffer_pixels=0)

        self.assertIsNotNone(result)
        collection, _ = result
        self.assertEqual(len(collection["features"]), 1)

    def test_failed_import_transaction_preserves_existing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = NativeTileStore(Path(directory))
            feature = {
                "properties": {"category": "physical", "layer": "ne-land"},
                "geometry": {"type": "Point", "coordinates": [0, 0]},
            }
            store.replace_source("natural-earth", [feature])

            with self.assertRaisesRegex(ValueError, "requires properties and geometry"):
                store.replace_source("natural-earth", [{"properties": {}}])

            self.assertEqual(store.feature_count(), 1)

    def test_raster_tile_is_a_compact_webp_and_is_revisioned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = NativeTileStore(Path(directory))
            styles = load_vector_theme().styles
            collection = {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"layer": "ne-place", "name": "Example"},
                        "geometry": {"type": "Point", "coordinates": [0, 0]},
                    }
                ],
            }
            payload = render_tile(collection, styles, 0, 0, 0)
            revision = cache_revision(store, {"showNaturalEarthPlaces": True}, styles)
            cache = NaturalEarthRasterCache(Path(directory), revision)
            cache.write_tile(0, 0, 0, payload)

            self.assertEqual(payload[:4], b"RIFF")
            self.assertEqual(payload[8:12], b"WEBP")
            self.assertLess(len(payload), 20_000)
            self.assertEqual(cache.read_tile(0, 0, 0), payload)
            self.assertTrue((Path(directory) / "raster-tiles.sqlite").is_file())

    def test_raster_revision_ignores_geopackage_timestamp_but_tracks_imports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = NativeTileStore(Path(directory))
            feature = {
                "properties": {"category": "physical", "layer": "ne-land"},
                "geometry": {"type": "Point", "coordinates": [0, 0]},
            }
            store.replace_source("natural-earth", [feature])
            styles = load_vector_theme().styles
            revision = cache_revision(store, {}, styles)

            os.utime(store.path, None)

            self.assertEqual(revision, cache_revision(store, {}, styles))
            store.replace_source("natural-earth", [feature])
            self.assertNotEqual(revision, cache_revision(store, {}, styles))

    def test_raster_tile_paints_tuple_coordinates_from_shapely(self) -> None:
        styles = load_vector_theme().styles
        collection = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"layer": "ne-land"},
                    "geometry": mapping(
                        Polygon(
                            [
                                (-180, -85),
                                (0, -85),
                                (0, 85),
                                (-180, 85),
                                (-180, -85),
                            ]
                        )
                    ),
                }
            ],
        }
        payload = render_tile(collection, styles, 0, 0, 0)

        self.assertNotEqual(payload, render_tile({"features": []}, styles, 0, 0, 0))

    def test_raster_tile_paints_deeper_bathymetry_over_shallow_water(self) -> None:
        styles = load_vector_theme().styles
        world = {
            "type": "Polygon",
            "coordinates": [
                [
                    (-180, -85),
                    (180, -85),
                    (180, 85),
                    (-180, 85),
                    (-180, -85),
                ]
            ],
        }
        collection = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"layer": "ne-bathymetry-5000"},
                    "geometry": world,
                },
                {
                    "type": "Feature",
                    "properties": {"layer": "ne-bathymetry-0"},
                    "geometry": world,
                },
            ],
        }

        payload = render_tile(collection, styles, 0, 0, 0)

        with Image.open(BytesIO(payload)) as image:
            self.assertEqual(image.convert("RGB").getpixel((256, 256)), (18, 34, 48))

    def test_raster_tile_paints_land_over_marine_polygons(self) -> None:
        styles = load_vector_theme().styles
        world = {
            "type": "Polygon",
            "coordinates": [
                [
                    (-180, -85),
                    (180, -85),
                    (180, 85),
                    (-180, 85),
                    (-180, -85),
                ]
            ],
        }
        collection = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"layer": "ne-land"},
                    "geometry": world,
                },
                {
                    "type": "Feature",
                    "properties": {"layer": "ne-marine"},
                    "geometry": world,
                },
            ],
        }

        payload = render_tile(collection, styles, 0, 0, 0)

        with Image.open(BytesIO(payload)) as image:
            self.assertEqual(image.convert("RGB").getpixel((256, 256)), (242, 239, 233))

    def test_raster_tile_fills_uncovered_coastal_seams_with_land(self) -> None:
        styles = load_vector_theme().styles
        collection = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"layer": "ne-land"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                (-180, -85),
                                (0, -85),
                                (0, 85),
                                (-180, 85),
                                (-180, -85),
                            ]
                        ],
                    },
                }
            ],
        }

        payload = render_tile(collection, styles, 0, 0, 0)

        with Image.open(BytesIO(payload)) as image:
            rendered = image.convert("RGB")
            self.assertEqual(rendered.getpixel((262, 256)), (242, 239, 233))
            self.assertEqual(rendered.getpixel((280, 256)), (32, 32, 32))

    def test_raster_cache_renders_a_cold_tile_once_and_reuses_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = NaturalEarthRasterCache(Path(directory), "revision")
            render_count = 0

            def render() -> bytes:
                nonlocal render_count
                render_count += 1
                return b"rendered-webp"

            with ThreadPoolExecutor(max_workers=4) as executor:
                paths = list(
                    executor.map(
                        lambda _: cache.get_or_render(3, 4, 2, render),
                        range(4),
                    )
                )

            self.assertEqual(render_count, 1)
            self.assertEqual(paths, [b"rendered-webp"] * 4)
            self.assertEqual(cache.read_tile(3, 4, 2), b"rendered-webp")

    def test_legacy_raster_tiles_are_migrated_into_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            maps_directory = Path(directory)
            legacy_path = maps_directory / "raster-tiles" / "revision" / "3" / "4" / "2.webp"
            legacy_path.parent.mkdir(parents=True)
            legacy_path.write_bytes(b"rendered-webp")

            self.assertEqual(NaturalEarthRasterCache.migrate_legacy_files(maps_directory), 1)

            self.assertFalse(legacy_path.exists())
            self.assertEqual(
                NaturalEarthRasterCache(maps_directory, "revision").read_tile(3, 4, 2),
                b"rendered-webp",
            )

    def test_raster_route_renders_and_persists_a_cold_tile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            maps_directory = Path(directory) / "maps"
            store = NativeTileStore(maps_directory)
            store.replace_source(
                "natural-earth",
                [
                    {
                        "properties": {
                            "category": "places",
                            "layer": "ne-place",
                            "minZoom": 0,
                            "name": "Example",
                        },
                        "geometry": {"type": "Point", "coordinates": [0, 0]},
                    }
                ],
            )
            service = SimpleNamespace(tile_store=store)
            configuration = SimpleNamespace(configuration=VectorConfiguration())

            class ImmediateQueue:
                def enqueue(self, _key: str, render: object) -> bool:
                    render()
                    return True

            with (
                patch("kody_vecmap.server.MAPS_DIRECTORY", maps_directory),
                patch("kody_vecmap.server.patch_service", service),
                patch("kody_vecmap.server.configuration_store", configuration),
                patch("kody_vecmap.server.tile_render_queue", ImmediateQueue()),
            ):
                first = get_raster_tile(0, 0, 0)
                second = get_raster_tile(0, 0, 0)

            self.assertEqual(first.status_code, 202)
            self.assertEqual(second.media_type, "image/webp")
            self.assertEqual(second.body[:4], b"RIFF")
            self.assertEqual(second.body[8:12], b"WEBP")

    def test_tile_render_queue_deduplicates_and_reports_progress(self) -> None:
        queue = RasterTileRenderQueue()
        first_started = Event()
        allow_first = Event()
        second_finished = Event()

        def render_first() -> None:
            first_started.set()
            self.assertTrue(allow_first.wait(1))

        def render_second() -> None:
            second_finished.set()

        self.assertTrue(queue.enqueue("first", render_first))
        self.assertTrue(first_started.wait(1))
        self.assertFalse(queue.enqueue("first", render_first))
        self.assertTrue(queue.enqueue("second", render_second))

        status = queue.response()
        self.assertEqual(status["status"], "processing")
        self.assertEqual(status["processed"], 0)
        self.assertEqual(status["total"], 2)
        self.assertEqual(status["queued"], 1)

        allow_first.set()
        self.assertTrue(second_finished.wait(1))


class VectorConfigurationTests(unittest.TestCase):
    def test_natural_earth_tile_zooms_are_normalized_and_bounded(self) -> None:
        configuration = VectorConfiguration(naturalEarthTileZooms="12, 4, 4, 0")

        self.assertEqual(configuration.natural_earth_tile_zooms, "0, 4, 12")
        self.assertEqual(natural_earth_tile_zooms("0, 2, 12"), (0, 2, 12))
        with self.assertRaisesRegex(ValueError, "between 0 and 12"):
            natural_earth_tile_zooms("13")

    def test_natural_earth_switches_persist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vecmap.json"
            store = ConfigurationStore(path)
            store.update(
                VectorConfiguration(
                    showNaturalEarthTransport=False,
                    showNaturalEarthPlaces=False,
                ),
                store.revision(),
            )

            result = ConfigurationStore(path).response()

        self.assertFalse(result["showNaturalEarthTransport"])
        self.assertFalse(result["showNaturalEarthPlaces"])

    def test_action_state_is_saved_for_worker_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "action-state.json"
            state = ImportState(path)
            state.start()
            state.update("Reading Natural Earth layers.", 42)

            persisted_state = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(persisted_state["status"], "running")
        self.assertEqual(persisted_state["progress"], 42)


if __name__ == "__main__":
    unittest.main()
