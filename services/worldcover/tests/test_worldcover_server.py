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

import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
import numpy
from PIL import Image
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds

from io import BytesIO

from kody_worldcover.server import (
    MAX_NATIVE_ZOOM,
    TileRenderStatus,
    Viewport,
    WorldCoverConfiguration,
    app,
    build_tile_pyramid,
    cached_native_source,
    cached_native_source_store,
    cached_raster,
    configuration_store,
    latitude_tile,
    longitude_tile,
    manifest,
    native_source_store,
    parse_generation_zoom_levels,
    put_config,
    download_native_viewport,
    download_native_source,
    invalidate_rendered_tiles_for_sources,
    raster_tile_bytes,
    source_coordinate,
    source_coordinates_for_bounds,
    source_filename,
    tile_bounds,
    tile_store,
    viewport_tiles,
    web_mercator_bounds,
    write_tile,
)


class WorldCoverTests(unittest.TestCase):
    def test_tile_status_endpoint_exposes_queue_counts(self) -> None:
        response = TestClient(app).get("/api/tiles/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json()),
            {"status", "message", "progress", "processed", "total", "queued", "retained"},
        )

    def tearDown(self) -> None:
        cached_raster.cache_clear()
        cached_native_source.cache_clear()
        cached_native_source_store.cache_clear()

    def test_source_names_use_three_degree_origins(self) -> None:
        self.assertEqual(source_coordinate(-1.2, 51.3), (-3, 51))
        self.assertEqual(source_filename(-3, 51), "ESA_WorldCover_10m_2021_v200_N51W003_Map.tif")
        self.assertEqual(
            source_coordinates_for_bounds(-3.1, 50.9, -2.9, 51.1),
            {(-6, 48), (-6, 51), (-3, 48), (-3, 51)},
        )

    def test_download_zoom_matches_viewport_without_exceeding_source_resolution(self) -> None:
        viewport = Viewport(west=-1.1, south=51.2, east=-0.9, north=51.4, zoom=16)
        zoom, tiles = viewport_tiles(viewport)
        self.assertEqual(zoom, MAX_NATIVE_ZOOM)
        self.assertGreater(len(tiles), 0)

    def test_web_tile_bounds_contain_their_centre(self) -> None:
        west, south, east, north = tile_bounds(8, 127, 85)
        self.assertLess(west, east)
        self.assertLess(south, north)
        mercator_west, mercator_south, mercator_east, mercator_north = web_mercator_bounds(8, 127, 85)
        self.assertLess(mercator_west, mercator_east)
        self.assertLess(mercator_south, mercator_north)

    def test_build_tile_pyramid_writes_parent_tiles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("kody_worldcover.server.WORLDCOVER_TILE_DIRECTORY", Path(directory)):
                for x in range(2):
                    for y in range(2):
                        write_tile(2, x, y, Image.new("RGBA", (256, 256), (x * 100, y * 100, 0, 255)))

                self.assertEqual(build_tile_pyramid(2, {(0, 0), (1, 0), (0, 1), (1, 1)}), 2)
                parent = tile_store().get(1, 0, 0)
                root = tile_store().get(0, 0, 0)
                self.assertIsNotNone(parent)
                self.assertIsNotNone(root)
                self.assertEqual(parent[:4], b"RIFF")
                self.assertEqual(parent[8:12], b"WEBP")

    def test_missing_child_is_served_from_the_parent_quadtree_tile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("kody_worldcover.server.WORLDCOVER_TILE_DIRECTORY", Path(directory)):
                write_tile(0, 0, 0, Image.new("RGBA", (256, 256), (10, 20, 30, 255)))
                payload = raster_tile_bytes(3, 5, 2)

                self.assertIsNotNone(payload)
                with Image.open(BytesIO(payload)) as tile:
                    self.assertEqual(tile.size, (256, 256))
                    self.assertEqual(tile.getpixel((128, 128))[:3], (10, 20, 30))
                self.assertIsNone(tile_store().get(3, 5, 2))

    def test_generation_zoom_levels_are_sorted_and_unique(self) -> None:
        self.assertEqual(parse_generation_zoom_levels("14, 6, 6, 10"), (6, 10, 14))
        with self.assertRaisesRegex(ValueError, "between 0 and"):
            parse_generation_zoom_levels("15")

    def test_missing_tile_renders_from_a_stored_native_geotiff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_directory = Path(directory)
            source_tiff = self.native_geotiff()
            with (
                patch("kody_worldcover.server.WORLDCOVER_TILE_DIRECTORY", data_directory / "tiles"),
                patch("kody_worldcover.server.WORLDCOVER_NATIVE_DATABASE", data_directory / "native-sources.sqlite"),
                patch.object(
                    configuration_store,
                    "configuration",
                    WorldCoverConfiguration(generationZoomLevels="7"),
                ),
            ):
                native_source_store().put((0, 0), source_tiff)
                zoom = 7
                x, y = longitude_tile(1.5, zoom), latitude_tile(1.5, zoom)

                payload = cached_raster(zoom, x, y)

                self.assertIsNotNone(payload)
                self.assertIsNotNone(tile_store().get(zoom, x, y))
                with Image.open(BytesIO(payload)) as tile:
                    rgba = tile.convert("RGBA")
                try:
                    self.assertEqual(rgba.getpixel((128, 128))[3], 255)
                finally:
                    rgba.close()

    def test_tile_http_request_renders_and_serves_a_missing_configured_tile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_directory = Path(directory)
            with (
                patch("kody_worldcover.server.WORLDCOVER_TILE_DIRECTORY", data_directory / "tiles"),
                patch("kody_worldcover.server.WORLDCOVER_NATIVE_DATABASE", data_directory / "native-sources.sqlite"),
                patch.object(
                    configuration_store,
                    "configuration",
                    WorldCoverConfiguration(generationZoomLevels="7"),
                ),
            ):
                native_source_store().put((0, 0), self.native_geotiff())
                zoom = 7
                x, y = longitude_tile(1.5, zoom), latitude_tile(1.5, zoom)

                response = TestClient(app).get(f"/api/tiles/{zoom}/{x}/{y}")

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["content-type"], "image/webp")
                self.assertIsNotNone(tile_store().get(zoom, x, y))
                with Image.open(BytesIO(response.content)) as tile:
                    self.assertEqual(tile.size, (256, 256))

    def test_configured_tile_without_native_source_does_not_serve_coarse_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_directory = Path(directory)
            with (
                patch("kody_worldcover.server.WORLDCOVER_TILE_DIRECTORY", data_directory / "tiles"),
                patch("kody_worldcover.server.WORLDCOVER_NATIVE_DATABASE", data_directory / "native-sources.sqlite"),
                patch.object(
                    configuration_store,
                    "configuration",
                    WorldCoverConfiguration(generationZoomLevels="14"),
                ),
            ):
                write_tile(
                    10,
                    508,
                    343,
                    Image.new("RGBA", (256, 256), (255, 0, 0, 255)),
                )

                response = TestClient(app).get("/api/tiles/14/8140/5495")

                self.assertEqual(response.status_code, 404)
                self.assertIn("download the current viewport first", response.json()["detail"])
                self.assertIsNotNone(tile_store().get(10, 508, 343))

    def test_configured_tile_is_served_without_parent_compositing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch("kody_worldcover.server.WORLDCOVER_TILE_DIRECTORY", Path(directory)),
                patch.object(
                    configuration_store,
                    "configuration",
                    WorldCoverConfiguration(generationZoomLevels="7"),
                ),
            ):
                write_tile(6, 31, 21, Image.new("RGBA", (256, 256), (255, 0, 0, 255)))
                child = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
                child.putpixel((128, 128), (0, 255, 0, 255))
                write_tile(7, 63, 42, child)
                expected = tile_store().get(7, 63, 42)

                payload = cached_raster(7, 63, 42)

                self.assertEqual(payload, expected)
                with Image.open(BytesIO(payload)) as tile:
                    rgba = tile.convert("RGBA")
                try:
                    self.assertEqual(rgba.getpixel((0, 0))[3], 0)
                    self.assertEqual(rgba.getpixel((128, 128))[:3], (0, 255, 0))
                finally:
                    rgba.close()

    def test_native_source_cache_is_invalidated_after_store_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "native-sources.sqlite"
            with patch("kody_worldcover.server.WORLDCOVER_NATIVE_DATABASE", database_path):
                store = native_source_store()
                store.put((0, 0), b"first-source")
                self.assertEqual(store.get((0, 0)), b"first-source")

                store.put((0, 0), b"updated-source")

                self.assertEqual(store.get((0, 0)), b"updated-source")

    def test_tile_render_status_tracks_waiting_and_active_requests(self) -> None:
        status = TileRenderStatus()
        status.enqueue()
        status.enqueue()
        status.start()

        self.assertEqual(
            status.response(),
            {
                "status": "processing",
                "message": "Rendering 1 WorldCover tile; 1 queued.",
                "progress": 0,
                "processed": 0,
                "total": 2,
                "queued": 1,
                "retained": 1,
            },
        )

        status.finish()
        self.assertEqual(
            status.response(),
            {
                "status": "processing",
                "message": "Rendering 0 WorldCover tiles; 1 queued.",
                "progress": 50,
                "processed": 1,
                "total": 2,
                "queued": 1,
                "retained": 1,
            },
        )

    def test_saving_generation_levels_only_updates_configuration(self) -> None:
        with (
            patch("kody_worldcover.server.configuration_store.update", return_value={"revision": "new"}) as update,
            patch(
                "kody_worldcover.server.render_tile",
            ) as render_tile_mock,
        ):
            updated = put_config(
                WorldCoverConfiguration(generationZoomLevels="6, 8, 10"),
                if_match="old",
            )

        self.assertEqual(updated["revision"], "new")
        update.assert_called_once()
        render_tile_mock.assert_not_called()

    def test_unconfigured_zoom_does_not_generate_a_tile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_directory = Path(directory)
            with (
                patch("kody_worldcover.server.WORLDCOVER_TILE_DIRECTORY", data_directory / "tiles"),
                patch("kody_worldcover.server.WORLDCOVER_NATIVE_DATABASE", data_directory / "native-sources.sqlite"),
                patch.object(
                    configuration_store,
                    "configuration",
                    WorldCoverConfiguration(generationZoomLevels="6, 8, 10"),
                ),
                patch("kody_worldcover.server.render_tile") as render_tile_mock,
            ):
                self.assertIsNone(cached_raster(7, 63, 42))

            render_tile_mock.assert_not_called()

    def test_manifest_keeps_native_download_separate_from_rendering(self) -> None:
        configuration = manifest()["configuration"]
        actions = configuration["actions"]
        download = next(action for action in actions if action["label"] == "Download current viewport")

        self.assertEqual(download["path"], "/api/actions/download-current-viewport")
        self.assertFalse(download["saveConfiguration"])
        self.assertEqual(configuration["activityStatusPath"], "/api/tiles/status")

    def test_native_download_stage_does_not_render_map_tiles(self) -> None:
        viewport = Viewport(west=-3, south=51, east=-2, north=52, zoom=8)
        with (
            patch("kody_worldcover.server.native_source_coordinates", return_value=[(-3, 51)]),
            patch("kody_worldcover.server.native_source_store") as source_store,
            patch("kody_worldcover.server.download_native_source") as download_source,
            patch("kody_worldcover.server.render_tile") as render_tile_mock,
            patch("kody_worldcover.server.invalidate_rendered_tiles_for_sources") as invalidate_tiles,
            patch("kody_worldcover.server.tile_store") as rendered_tile_store,
        ):
            source_store.return_value.get.return_value = None
            source_store.return_value.is_unavailable.return_value = False

            message = download_native_viewport(viewport)

        self.assertIn("Stored 1 native", message)
        download_source.assert_called_once_with((-3, 51))
        render_tile_mock.assert_not_called()
        invalidate_tiles.assert_called_once_with([(-3, 51)])
        rendered_tile_store.return_value.set_revision.assert_called_once_with()

    def test_new_native_source_invalidates_only_overlapping_cached_tiles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tile_directory = Path(directory) / "tiles"
            payload = BytesIO()
            Image.new("RGBA", (256, 256), "red").save(payload, format="WEBP")
            with (
                patch("kody_worldcover.server.WORLDCOVER_TILE_DIRECTORY", tile_directory),
                patch.object(
                    configuration_store,
                    "configuration",
                    WorldCoverConfiguration(generationZoomLevels="8"),
                ),
            ):
                store = tile_store()
                for x, y in ((128, 84), (127, 85), (128, 85), (128, 86), (128, 89)):
                    store.put(8, x, y, payload.getvalue())

                invalidated = invalidate_rendered_tiles_for_sources([(0, 48)])

                self.assertEqual(invalidated, 2)
                self.assertIsNotNone(store.get(8, 128, 84))
                self.assertIsNotNone(store.get(8, 127, 85))
                self.assertIsNone(store.get(8, 128, 85))
                self.assertIsNone(store.get(8, 128, 86))
                self.assertIsNotNone(store.get(8, 128, 89))

    def test_native_source_404_is_recorded_as_unavailable_not_failed(self) -> None:
        missing_source = HTTPError("https://example.test/worldcover.tif", 404, "Not Found", {}, None)
        with (
            patch("kody_worldcover.server.urlopen", side_effect=missing_source),
            patch("kody_worldcover.server.native_source_store") as source_store,
        ):
            result = download_native_source((-30, 63))

        self.assertIsNone(result)
        source_store.return_value.mark_unavailable.assert_called_once_with((-30, 63))

    def test_native_download_continues_after_an_unavailable_source(self) -> None:
        viewport = Viewport(west=-6, south=60, east=0, north=66, zoom=5)
        with (
            patch(
                "kody_worldcover.server.native_source_coordinates",
                return_value=[(-30, 63), (-27, 63)],
            ),
            patch("kody_worldcover.server.native_source_store") as source_store,
            patch(
                "kody_worldcover.server.download_native_source",
                side_effect=[None, b"native-geotiff"],
            ) as download_source,
            patch("kody_worldcover.server.invalidate_rendered_tiles_for_sources") as invalidate_tiles,
            patch("kody_worldcover.server.tile_store") as rendered_tile_store,
        ):
            source_store.return_value.get.return_value = None
            source_store.return_value.is_unavailable.return_value = False

            message = download_native_viewport(viewport)

        self.assertIn("Stored 1 native", message)
        self.assertIn("1 source areas had no ESA tile", message)
        self.assertEqual(download_source.call_count, 2)
        invalidate_tiles.assert_called_once_with([(-27, 63)])
        rendered_tile_store.return_value.set_revision.assert_called_once_with()

    def test_native_source_non_404_http_error_still_fails_explicitly(self) -> None:
        server_error = HTTPError("https://example.test/worldcover.tif", 503, "Unavailable", {}, None)
        with (
            patch("kody_worldcover.server.urlopen", side_effect=server_error),
            patch("kody_worldcover.server.native_source_store") as source_store,
        ):
            with self.assertRaises(HTTPException) as error:
                download_native_source((-30, 63))

        self.assertEqual(error.exception.status_code, 502)
        source_store.return_value.mark_unavailable.assert_not_called()

    @staticmethod
    def native_geotiff() -> bytes:
        with MemoryFile() as memory_file:
            with memory_file.open(
                driver="GTiff",
                width=3,
                height=3,
                count=1,
                dtype=numpy.uint8,
                crs="EPSG:4326",
                transform=from_bounds(0, 0, 3, 3, 3, 3),
                nodata=0,
            ) as dataset:
                dataset.write(numpy.full((1, 3, 3), 10, dtype=numpy.uint8))
            return memory_file.read()


if __name__ == "__main__":
    unittest.main()
