import asyncio
import json
import gzip
import os
import sqlite3
import tempfile
import unittest
import zipfile
from argparse import Namespace
from pathlib import Path

from PIL import Image

from kody_vecmap.map_manager import (
    coastline_land_features,
    feature_bounds,
    install_georeferenced_raster,
    install_world_raster,
    minimum_zoom_for_feature,
    osm_scan_status_message,
    osm_layer,
    parse_bounding_box,
    place_minimum_zoom,
    rebuild_lod_pyramid,
    tiles_for_bounds,
)
from kody_vecmap.math import MapMath
from kody_vecmap.server import (
    ConfigurationStore,
    ImportState,
    VectorConfiguration,
    VectorPatchService,
    filter_vector_features,
    get_manifest,
    importable_vector_sources,
)
from kody_vecmap.map_packages import NativeTileStore


class MapManagerCoverageTests(unittest.TestCase):
    def test_osm_scan_status_reports_streamed_item_counts(self) -> None:
        self.assertEqual(
            osm_scan_status_message(12_345, 12_000, 345, 17),
            "Scanning OSM PBF features "
            "(12,345 items: 12,000 nodes, 345 ways; 17 retained; total unavailable).",
        )

    def test_geopackage_store_uses_binary_geometry_and_spatial_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            maps_directory = Path(directory) / "maps"
            store = NativeTileStore(maps_directory)
            store.merge_collection(
                2,
                2,
                1,
                {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "properties": {"layer": "osm-land", "minZoom": 2},
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [[[10, 10], [20, 10], [20, 20], [10, 10]]],
                            },
                        }
                    ],
                },
            )
            connection = sqlite3.connect(store.path)
            try:
                application_id = connection.execute("PRAGMA application_id").fetchone()[0]
                index_name = connection.execute(
                    "SELECT name FROM sqlite_master WHERE name = 'rtree_kody_vector_features'"
                ).fetchone()[0]
            finally:
                connection.close()
            collection, lod = store.tile_collection(2, 2, 1)

        self.assertEqual(application_id, 0x47504B47)
        self.assertEqual(index_name, "rtree_kody_vector_features")
        self.assertEqual(lod, 2)
        self.assertEqual(collection["features"][0]["properties"]["minZoom"], 2)

    def test_placenames_have_readable_detail_levels(self) -> None:
        self.assertEqual(place_minimum_zoom("city"), 8)
        self.assertEqual(place_minimum_zoom("town"), 10)
        self.assertEqual(place_minimum_zoom("village"), 12)
        self.assertEqual(place_minimum_zoom("hamlet"), 14)
        self.assertIsNone(place_minimum_zoom("locality"))

    def test_feature_bounds_supports_line_and_polygon_geometries(self) -> None:
        bounds = feature_bounds(
            [
                {
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[-2, 50], [-1, 51]],
                    }
                },
                {
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[-3, 49], [0, 49], [0, 52], [-3, 49]]],
                    }
                },
            ]
        )

        self.assertEqual(bounds, parse_bounding_box("-3,49,0,52"))

    def test_osm_import_classifies_residential_landuse_as_urban(self) -> None:
        self.assertEqual(osm_layer({"landuse": "residential"}, []), "osm-urban")

    def test_requested_box_expands_to_complete_coverage_tiles(self) -> None:
        requested = parse_bounding_box("-1.8,50.55,-1.0,50.85")
        tiles = tiles_for_bounds(requested, 10)

        self.assertTrue(tiles)
        for x, y in tiles:
            tile_bounds = MapMath.tile_bounds(10, x, y)
            self.assertFalse(
                tile_bounds.east <= requested.west
                or tile_bounds.west >= requested.east
                or tile_bounds.north <= requested.south
                or tile_bounds.south >= requested.north
            )

    def test_closed_coastline_creates_osm_land_polygon(self) -> None:
        coastline = [
            {
                "type": "Feature",
                "properties": {"layer": "osm-coastline"},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]],
                },
            }
        ]

        land = coastline_land_features(coastline, minimum_zoom=8)

        self.assertEqual(len(land), 1)
        self.assertEqual(land[0]["properties"]["layer"], "osm-land")
        self.assertEqual(land[0]["properties"]["minZoom"], 8)

    def test_clockwise_coastline_is_not_land(self) -> None:
        coastline = [
            {
                "type": "Feature",
                "properties": {"layer": "osm-coastline"},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]],
                },
            }
        ]

        self.assertEqual(coastline_land_features(coastline), [])

    def test_first_osm_detail_tier_includes_coastline_forests_and_major_roads(self) -> None:
        self.assertEqual(minimum_zoom_for_feature({"natural": "coastline"}, "osm-coastline"), 8)
        self.assertEqual(minimum_zoom_for_feature({"natural": "wood"}, "osm-forest"), 8)
        self.assertEqual(minimum_zoom_for_feature({"highway": "primary"}, "osm-road"), 8)


class MapPackageSelectionTests(unittest.TestCase):
    def test_geopackage_store_does_not_create_tile_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            maps_directory = Path(directory)
            store = NativeTileStore(maps_directory)
            store.merge_collection(2, 2, 1, {"type": "FeatureCollection", "features": []})

            self.assertTrue(store.path.is_file())
            self.assertFalse((maps_directory / "tiles").exists())

    def test_failed_vector_operation_clears_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "action-state.json"
            state = ImportState(path)
            state.start()
            state.update("Importing map data.", 65)
            previous_worker = os.environ.get("KODY_VECTOR_WORKER")
            os.environ["KODY_VECTOR_WORKER"] = "1"
            try:
                self.assertEqual(
                    ImportState(path).response(),
                    {
                        "status": "running",
                        "message": "Importing map data.",
                        "progress": 65,
                    },
                )
            finally:
                if previous_worker is None:
                    del os.environ["KODY_VECTOR_WORKER"]
                else:
                    os.environ["KODY_VECTOR_WORKER"] = previous_worker
            state.fail(ValueError("import aborted"))
            self.assertEqual(
                state.response(),
                {"status": "failed", "message": "import aborted", "progress": 0},
            )

    def test_service_restart_cancels_stale_vector_operation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "action-state.json"
            state = ImportState(path)
            state.start()
            previous_worker = os.environ.pop("KODY_VECTOR_WORKER", None)
            try:
                restarted_state = ImportState(path)
            finally:
                if previous_worker is not None:
                    os.environ["KODY_VECTOR_WORKER"] = previous_worker

            self.assertEqual(
                restarted_state.response(),
                {
                    "status": "failed",
                    "message": "Vector action was cancelled because the service restarted.",
                    "progress": 0,
                },
            )

    def test_vector_import_selection_accepts_land_polygon_archive_before_map_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for filename in (
                "britain.osm.pbf",
                "region-free.gpkg.zip",
                "region-free.gpkg",
            ):
                (root / filename).touch()
            with zipfile.ZipFile(root / "land-polygons-split-4326.zip", "w") as archive:
                for extension in (".shp", ".shx", ".dbf"):
                    archive.writestr(f"land-polygons/land_polygons{extension}", "")

            sources = importable_vector_sources(root)

        self.assertEqual(
            [source.name for source in sources],
            [
                "land-polygons-split-4326.zip",
                "britain.osm.pbf",
                "region-free.gpkg",
                "region-free.gpkg.zip",
            ],
        )

    def test_vector_layer_toggles_persist_to_its_configuration_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vecmap.json"
            store = ConfigurationStore(path)
            store.update(
                VectorConfiguration(
                    showMajorRoads=False,
                    showUrbanAreas=False,
                    showWoodland=False,
                ),
                store.revision(),
            )

            reloaded = ConfigurationStore(path).response()

        self.assertFalse(reloaded["showMajorRoads"])
        self.assertFalse(reloaded["showUrbanAreas"])
        self.assertFalse(reloaded["showWoodland"])

    def test_vector_layer_toggles_filter_imported_content(self) -> None:
        collection = {
            "type": "FeatureCollection",
            "features": [
                {"properties": {"layer": "osm-road"}},
                {"properties": {"layer": "osm-urban"}},
                {"properties": {"layer": "osm-forest"}},
                {"properties": {"layer": "osm-place"}},
                {"properties": {"layer": "osm-water"}},
            ],
        }

        filtered = filter_vector_features(
            collection,
            VectorConfiguration(
                showMajorRoads=False,
                showUrbanAreas=False,
                showWoodland=False,
                showPlaceNames=False,
            ),
        )

        self.assertEqual(
            [feature["properties"]["layer"] for feature in filtered["features"]],
            ["osm-water"],
        )

    def test_vector_manifest_omits_screen_import(self) -> None:
        configuration = asyncio.run(get_manifest())["configuration"]
        self.assertEqual(
            [field["key"] for field in configuration["fields"][:3]],
            ["showMajorRoads", "showUrbanAreas", "showWoodland"],
        )
        self.assertIn("revision", configuration["initialConfiguration"])
        self.assertNotIn(
            "Import screen area",
            [action["label"] for action in configuration["actions"]],
        )

    def test_geopackage_store_merges_features_at_the_same_tile_location(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            maps_directory = root / "maps"
            store = NativeTileStore(maps_directory)
            store.merge_collection(
                2,
                2,
                1,
                {
                    "type": "FeatureCollection",
                    "features": [{
                        "properties": {"layer": "osm-road"},
                        "geometry": {"type": "LineString", "coordinates": [[10, 10], [20, 20]]},
                    }],
                },
            )
            store.merge_collection(
                2,
                2,
                1,
                {
                    "type": "FeatureCollection",
                    "features": [{
                        "properties": {"layer": "osm-forest"},
                        "geometry": {"type": "Polygon", "coordinates": [[[12, 12], [18, 12], [18, 18], [12, 12]]]},
                    }],
                },
            )

            self.assertEqual(
                [feature["properties"]["layer"] for feature in store.tile_collection(2, 2, 1)[0]["features"]],
                ["osm-road", "osm-forest"],
            )

    def test_geopackage_store_queries_geometry_at_requested_zoom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = NativeTileStore(Path(directory))
            store.merge_collection(
                15,
                16_000,
                16_000,
                {
                    "type": "FeatureCollection",
                    "features": [{
                        "properties": {"layer": "osm-road", "minZoom": 14},
                        "geometry": {"type": "LineString", "coordinates": [[-4.215, 4.205], [-4.21, 4.21]]},
                    }],
                },
            )

            collection, lod = store.tile_collection(15, 16_000, 16_000)

        self.assertEqual(lod, 15)
        self.assertEqual(collection["features"][0]["properties"]["layer"], "osm-road")

    def test_rebuild_lod_pyramid_fills_intermediate_zoom_levels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            package_directory = Path(directory) / "detail"
            package_directory.mkdir()
            (package_directory / "manifest.json").write_text(
                json.dumps(
                    {
                        "id": "detail",
                        "source": {"provider": "test", "licence": "ODbL-1.0", "attribution": "test"},
                        "coverageZoom": 2,
                        "minZoom": 1,
                        "maxZoom": 3,
                        "tiles": ["2/2/2"],
                        "format": "geojson",
                        "lods": [1, 3],
                    }
                ),
                encoding="utf-8",
            )
            source_directory = package_directory / "tiles" / "3" / "4"
            source_directory.mkdir(parents=True)
            with gzip.open(source_directory / "4.geojson.gz", "wt", encoding="utf-8") as stream:
                json.dump(
                    {
                        "type": "FeatureCollection",
                        "features": [
                            {
                                "type": "Feature",
                                "properties": {"layer": "osm-road", "minZoom": 1},
                                "geometry": {
                                    "type": "LineString",
                                    "coordinates": [[0, 0], [1, 1]],
                                },
                            }
                        ],
                    },
                    stream,
                )

            rebuild_lod_pyramid(package_directory)

            manifest = json.loads((package_directory / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["lods"], [1, 2, 3])
            self.assertTrue((package_directory / "tiles" / "2" / "2" / "2.geojson.gz").is_file())

    def test_native_tile_supersedes_base_for_its_exact_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base_file = root / "earth.geojson"
            maps_directory = root / "maps"
            base_file.write_text(
                json.dumps(
                    {
                        "type": "FeatureCollection",
                        "features": [
                            {
                                "type": "Feature",
                                "properties": {"layer": "earth"},
                                "geometry": {"type": "Point", "coordinates": [-10, 10]},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            NativeTileStore(maps_directory).merge_collection(
                2,
                2,
                2,
                {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "properties": {"layer": "osm-road"},
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [[10, -10], [20, -20]],
                            },
                        }
                    ],
                },
            )

            service = VectorPatchService([base_file], maps_directory)

            detailed = service.patch(2, 2, 2)
            fallback = service.patch(2, 0, 0)

            self.assertEqual(detailed["source"]["id"], "native-vector-tiles")
            self.assertEqual(detailed["features"][0]["properties"]["layer"], "osm-road")
            self.assertEqual(fallback["source"]["id"], "earth-base")


class RasterTileTests(unittest.TestCase):
    def test_world_raster_creates_xyz_tiles_through_requested_zoom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "world.png"
            output = root / "satellite"
            Image.new("RGB", (512, 512), "#123456").save(source)

            install_world_raster(
                Namespace(source=source, output=output, max_zoom=1, replace=False)
            )

            tiles = list(output.rglob("*.png"))
            self.assertEqual(len(tiles), 5)
            self.assertTrue((output / "0" / "0" / "0.png").is_file())
            self.assertTrue((output / "1" / "1" / "1.png").is_file())

    def test_georeferenced_raster_creates_intersecting_xyz_tile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "region.png"
            world_file = root / "region.pgw"
            projection = root / "region.prj"
            output = root / "satellite"
            Image.new("RGBA", (256, 256), "red").save(source)
            world_file.write_text(
                "156543.03392804097\n0\n0\n-156543.03392804097\n"
                "-19959236.825825222\n19959236.825825222\n",
                encoding="utf-8",
            )
            projection.write_text("Mercator_Auxiliary_Sphere", encoding="utf-8")

            install_georeferenced_raster(
                Namespace(
                    source=source,
                    world_file=world_file,
                    projection=projection,
                    min_zoom=0,
                    max_zoom=0,
                    output=output,
                    replace=False,
                )
            )

            self.assertTrue((output / "0" / "0" / "0.png").is_file())


if __name__ == "__main__":
    unittest.main()
