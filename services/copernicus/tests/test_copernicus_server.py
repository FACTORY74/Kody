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
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image, ImageDraw
import requests

from kody_copernicus.server import (
    ConfigurationStore,
    COPERNICUS_NATIVE_TILE_ZOOM,
    CopernicusConfiguration,
    MosaicOptionStore,
    Viewport,
    copernicus_mosaic_options,
    copernicus_request,
    copernicus_request_dimensions,
    copernicus_tile_grid_batches,
    copernicus_tile_sections,
    download_current_viewport,
    import_georeferenced_png,
    process_response_bytes,
    raster_tile_bytes,
    tile_store,
    web_mercator_latitude,
    web_mercator_longitude,
    viewport_tiles,
)


class SatelliteTileTests(unittest.TestCase):
    def assert_pixel_color_near(
        self, actual: float | tuple[int, ...] | None, expected: tuple[int, ...]
    ) -> None:
        if not isinstance(actual, tuple):
            self.fail(f"expected an RGB or RGBA pixel, got {actual!r}")
        self.assertIn(len(actual), (3, 4))
        for actual_channel, expected_channel in zip(actual[:3], expected[:3]):
            self.assertLessEqual(abs(actual_channel - expected_channel), 2)
        if len(actual) == 4:
            self.assertEqual(actual[3], expected[3])
        elif expected[3] != 255:
            self.fail("transparent pixel was flattened to RGB")

    def test_current_viewport_uses_quad_tiles_at_the_browser_tile_zoom(self) -> None:
        zoom, tiles = viewport_tiles(
            Viewport(west=-1, south=-1, east=1, north=1, zoom=3.9)
        )

        self.assertEqual(zoom, 3)
        self.assertEqual(tiles, [(3, 3), (4, 3), (3, 4), (4, 4)])

    def test_viewport_at_10m_uses_the_native_tile_zoom(self) -> None:
        viewport = Viewport(west=-1, south=-1, east=1, north=1, zoom=3.9)

        zoom, tiles = viewport_tiles(viewport, COPERNICUS_NATIVE_TILE_ZOOM)

        self.assertEqual(zoom, 14)
        self.assertGreater(len(tiles), 1)

    def test_singapore_region_selects_covering_zoom_six_tiles(self) -> None:
        zoom, tiles = viewport_tiles(
            Viewport(
                west=92.66530366587688,
                south=-5.116540556006811,
                east=114.36306755194505,
                north=6.469410035921003,
                zoom=6.400000000000034,
            )
        )

        self.assertEqual(zoom, 6)
        self.assertEqual(
            tiles,
            [
                (48, 30), (49, 30), (50, 30), (51, 30), (52, 30),
                (48, 31), (49, 31), (50, 31), (51, 31), (52, 31),
                (48, 32), (49, 32), (50, 32), (51, 32), (52, 32),
            ],
        )
        (left, bottom, right, top), destination = copernicus_tile_sections(6, 49, 31)[0]
        self.assertEqual(destination, (0, 0, 256, 256))
        self.assertAlmostEqual(web_mercator_longitude(left), 95.625)
        self.assertAlmostEqual(web_mercator_longitude(right), 101.25)
        self.assertAlmostEqual(web_mercator_latitude(bottom), 0)
        self.assertAlmostEqual(web_mercator_latitude(top), 5.61598582)

    def test_process_request_uses_web_mercator_tile_bounds_and_selected_mosaic(self) -> None:
        request = copernicus_request(
            (-1_000, -2_000, 1_000, 2_000),
            128,
            64,
            "2026-01-01T00:00:00Z/2026-03-31T23:59:59Z",
        )

        self.assertEqual(
            request["input"]["bounds"]["properties"]["crs"],
            "http://www.opengis.net/def/crs/EPSG/0/3857",
        )
        self.assertEqual(request["output"]["width"], 128)
        self.assertEqual(request["output"]["height"], 64)
        self.assertEqual(request["input"]["data"][0]["dataFilter"]["mosaickingOrder"], "mostRecent")

    def test_process_request_can_target_a_selected_quarterly_mosaic(self) -> None:
        request = copernicus_request(
            (-1_000, -2_000, 1_000, 2_000),
            128,
            64,
            "2026-04-01T00:00:00Z/2026-06-30T23:59:59Z",
        )

        self.assertEqual(
            request["input"]["data"][0]["dataFilter"]["timeRange"],
            {"from": "2026-04-01T00:00:00Z", "to": "2026-06-30T23:59:59Z"},
        )

    def test_selected_mosaic_period_must_be_an_iso_datetime_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "mosaicPeriod"):
            CopernicusConfiguration(mosaicPeriod="summer")

    def test_legacy_latest_mosaic_is_migrated_to_an_unselected_period(self) -> None:
        configuration = CopernicusConfiguration(mosaicPeriod="latest")

        self.assertEqual(configuration.mosaic_period, "")

    def test_catalogue_mosaics_are_returned_as_select_options(self) -> None:
        token_response = MagicMock()
        token_response.json.return_value = {"access_token": "token"}
        catalogue_response = MagicMock()
        catalogue_response.json.return_value = {
            "features": ["2026-04-01", "2026-01-01"],
        }
        session = MagicMock()
        session.post.side_effect = [token_response, catalogue_response]

        with patch("kody_copernicus.server.requests.Session", return_value=session):
            options = copernicus_mosaic_options(
                CopernicusConfiguration(clientId="client", clientSecret="secret")
            )

        self.assertIn(
            {
                "value": "2026-04-01T00:00:00Z/2026-06-30T23:59:59Z",
                "label": "2026 Q2 (2026-04-01 to 2026-06-30)",
            },
            options,
        )
        self.assertEqual(session.post.call_count, 2)
        catalogue_request = session.post.call_args_list[1].kwargs["json"]
        self.assertEqual(catalogue_request["bbox"], [-180, -85, 180, 85])
        self.assertEqual(catalogue_request["distinct"], "date")
        self.assertEqual(catalogue_request["limit"], 100)

    def test_mosaic_options_are_served_from_the_startup_cache(self) -> None:
        store = MosaicOptionStore()
        options = [
            {"value": "2026-01-01T00:00:00Z/2026-03-31T23:59:59Z", "label": "2026 Q1"},
        ]

        with patch(
            "kody_copernicus.server.copernicus_mosaic_options",
            return_value=options,
        ) as load_options:
            store.load(CopernicusConfiguration(clientId="client", clientSecret="secret"))
            first_response = store.response()
            second_response = store.response()

        self.assertEqual(first_response, {"options": options, "warning": ""})
        self.assertEqual(second_response, first_response)
        load_options.assert_called_once()

    def test_large_area_request_uses_collection_resolution(self) -> None:
        dimensions = copernicus_request_dimensions(
            (-5_009_377.085697312, 8_659_174.417649025, -4_174_480.90474776, 10_018_754.171394626),
            (0, 0, 85, 139),
        )

        self.assertEqual(dimensions, (522, 850))

    def test_request_dimensions_do_not_exceed_ten_metre_resolution(self) -> None:
        dimensions = copernicus_request_dimensions(
            (0, 0, 1_000, 1_000), (0, 0, 256, 256)
        )

        self.assertEqual(dimensions, (100, 100))

    def test_process_api_error_includes_request_context(self) -> None:
        response = MagicMock(spec=requests.Response)
        response.text = '{"error":{"message":"invalid time range"}}'
        response.raise_for_status.side_effect = requests.HTTPError("400 Client Error")

        with self.assertRaisesRegex(
            RuntimeError,
            r"128x64px EPSG:3857 bbox \(-1000, -2000, 1000, 2000\).*invalid time range",
        ):
            process_response_bytes(response, (-1_000, -2_000, 1_000, 2_000), 128, 64)

    def test_world_tile_is_split_into_requests_no_larger_than_ten_degrees(self) -> None:
        sections = copernicus_tile_sections(0, 0, 0)

        self.assertGreater(len(sections), 1)
        for (left, bottom, right, top), (pixel_left, pixel_top, pixel_right, pixel_bottom) in sections:
            self.assertLessEqual(web_mercator_longitude(right) - web_mercator_longitude(left), 10.000001)
            self.assertLessEqual(web_mercator_latitude(top) - web_mercator_latitude(bottom), 10.000001)
            self.assertLess(pixel_left, pixel_right)
            self.assertLess(pixel_top, pixel_bottom)

    def test_section_destination_rows_follow_projected_grid(self) -> None:
        sections = copernicus_tile_sections(0, 0, 0)
        first_row_bottom = sections[0][1][3]
        row_count = len({destination[1] for _, destination in sections})

        self.assertEqual(first_row_bottom, round(256 / row_count))

    def test_viewport_tiles_are_grouped_into_bounded_contiguous_mosaic_requests(self) -> None:
        tiles = [(x, y) for y in range(10, 20) for x in range(20, 31)]

        grids = copernicus_tile_grid_batches(tiles)

        self.assertEqual(len(grids), 4)
        self.assertEqual({tile for grid in grids for tile in grid}, set(tiles))
        self.assertTrue(
            all(
                len({x for x, _ in grid}) <= 9 and len({y for _, y in grid}) <= 9
                for grid in grids
            )
        )

    def test_configuration_response_redacts_and_preserves_client_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "copernicus.json"
            store = ConfigurationStore(path)
            initial = CopernicusConfiguration(clientId="client", clientSecret="secret")
            store.update(initial, None)

            self.assertEqual(store.response()["clientSecret"], "")
            self.assertTrue(store.response()["clientSecretSaved"])
            reloaded_store = ConfigurationStore(path)
            self.assertEqual(reloaded_store.configuration.client_secret, "secret")
            reloaded_store.update(
                CopernicusConfiguration(clientId="client", clientSecret=""),
                reloaded_store.revision(),
            )
            self.assertEqual(reloaded_store.configuration.client_secret, "secret")

    def test_download_stores_the_process_api_tile_as_a_quad_tile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_bytes = BytesIO()
            Image.new("RGB", (256, 256), "red").save(image_bytes, format="PNG")
            token_response = MagicMock()
            token_response.json.return_value = {"access_token": "token"}
            process_response = MagicMock()
            process_response.content = image_bytes.getvalue()
            session = MagicMock()
            session.post.side_effect = [token_response, process_response]
            configuration = CopernicusConfiguration(
                clientId="client",
                clientSecret="secret",
                mosaicPeriod="2026-01-01T00:00:00Z/2026-03-31T23:59:59Z",
                rebuildPyramidWithDownload=False,
            )

            with (
                patch("kody_copernicus.server.COPERNICUS_DIRECTORY", root),
                patch(
                    "kody_copernicus.server.viewport_from_configuration_service",
                    return_value=Viewport(west=-1, south=-1, east=1, north=1, zoom=0),
                ),
                patch("kody_copernicus.server.viewport_tiles", return_value=(7, [(1, 1)])),
                patch("kody_copernicus.server.requests.Session", return_value=session),
            ):
                message = download_current_viewport(configuration)

            self.assertEqual(message, "Downloaded 1 Copernicus Global Mosaic tiles at zoom 7 using 1 Copernicus requests.")
            tile_payload = tile_store(root).get(7, 1, 1)
            self.assertIsNotNone(tile_payload)
            with Image.open(BytesIO(tile_payload)) as tile:
                self.assertEqual(tile.format, "WEBP")
            self.assertEqual(session.post.call_count, 2)

    def test_download_crops_adjacent_tiles_from_one_process_mosaic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_bytes = BytesIO()
            mosaic = Image.new("RGB", (512, 256), "red")
            ImageDraw.Draw(mosaic).rectangle((256, 0, 511, 255), fill="blue")
            mosaic.save(image_bytes, format="PNG")
            mosaic.close()
            token_response = MagicMock()
            token_response.json.return_value = {"access_token": "token"}
            process_response = MagicMock()
            process_response.content = image_bytes.getvalue()
            session = MagicMock()
            session.post.side_effect = [token_response, process_response]
            configuration = CopernicusConfiguration(
                clientId="client",
                clientSecret="secret",
                mosaicPeriod="2026-01-01T00:00:00Z/2026-03-31T23:59:59Z",
                rebuildPyramidWithDownload=False,
            )

            with (
                patch("kody_copernicus.server.COPERNICUS_DIRECTORY", root),
                patch(
                    "kody_copernicus.server.viewport_from_configuration_service",
                    return_value=Viewport(west=-1, south=-1, east=1, north=1, zoom=0),
                ),
                patch("kody_copernicus.server.viewport_tiles", return_value=(7, [(1, 1), (2, 1)])),
                patch("kody_copernicus.server.requests.Session", return_value=session),
            ):
                download_current_viewport(configuration)

            self.assertEqual(session.post.call_count, 2)
            for x, color in [(1, (255, 0, 0, 255)), (2, (0, 0, 255, 255))]:
                payload = tile_store(root).get(7, x, 1)
                self.assertIsNotNone(payload)
                with Image.open(BytesIO(payload)) as tile:
                    self.assert_pixel_color_near(tile.getpixel((128, 128)), color)

    def test_missing_child_tile_is_cropped_from_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = Image.new("RGB", (256, 256), "black")
            ImageDraw.Draw(image).rectangle((128, 0, 255, 127), fill="red")
            payload = BytesIO()
            image.save(payload, format="WEBP")
            tile_store(root).put(0, 0, 0, payload.getvalue())

            tile = raster_tile_bytes(root, 1, 1, 0)

            self.assertIsNotNone(tile)
            with Image.open(BytesIO(tile)) as generated:
                self.assert_pixel_color_near(
                    generated.getpixel((128, 128)), (255, 0, 0, 255)
                )

    def test_png_cache_tile_is_not_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tile_path = root / "0" / "0"
            tile_path.mkdir(parents=True)
            Image.new("RGB", (256, 256), "red").save(tile_path / "0.png")

            self.assertIsNone(raster_tile_bytes(root, 0, 0, 0))

    def test_legacy_webp_tiles_are_migrated_into_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy_path = root / "2" / "1" / "3.webp"
            legacy_path.parent.mkdir(parents=True)
            Image.new("RGB", (256, 256), "red").save(legacy_path, format="WEBP")
            marker = root / ".revision"
            marker.write_text("legacy-revision", encoding="utf-8")

            store = tile_store(root)

            self.assertEqual(store.migrate_legacy_files(), 1)
            self.assertFalse(legacy_path.exists())
            self.assertFalse(marker.exists())
            self.assertEqual(store.revision(), "legacy-revision")
            payload = store.get(2, 1, 3)
            self.assertIsNotNone(payload)
            with Image.open(BytesIO(payload)) as image:
                self.assertEqual(image.format, "WEBP")

    def test_transparent_child_tile_is_composited_over_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = BytesIO()
            Image.new("RGBA", (256, 256), "red").save(parent, format="WEBP")
            tile_store(root).put(0, 0, 0, parent.getvalue())
            child = Image.new("RGBA", (256, 256))
            ImageDraw.Draw(child).rectangle((128, 0, 255, 255), fill="blue")
            child_payload = BytesIO()
            child.save(child_payload, format="WEBP")
            tile_store(root).put(1, 1, 0, child_payload.getvalue())

            tile = raster_tile_bytes(root, 1, 1, 0)

            self.assertIsNotNone(tile)
            with Image.open(BytesIO(tile)) as generated:
                self.assert_pixel_color_near(
                    generated.getpixel((64, 128)), (255, 0, 0, 255)
                )
                self.assert_pixel_color_near(
                    generated.getpixel((192, 128)), (0, 0, 255, 255)
                )

    def test_georeferenced_png_is_added_to_the_satellite_library(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "example.png"
            Image.new("RGBA", (256, 256), "green").save(source)
            pixel_size = 2 * 20037508.342789244 / 256
            (root / "example.pgw").write_text(
                f"{pixel_size}\n0\n0\n{-pixel_size}\n{-20037508.342789244 + pixel_size / 2}\n"
                f"{20037508.342789244 - pixel_size / 2}\n",
                encoding="utf-8",
            )
            (root / "example.prj").write_text("Mercator_Auxiliary_Sphere", encoding="utf-8")
            output = root / "satellite"

            with patch("kody_copernicus.server.COPERNICUS_DIRECTORY", output):
                result = import_georeferenced_png(str(source))

            self.assertIn("Imported 1 satellite tiles", result)
            self.assertIsNotNone(tile_store(output).get(0, 0, 0))

    def test_georeferenced_png_generates_visible_lower_zoom_ancestors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "detail.png"
            Image.new("RGBA", (256, 256), "green").save(source)
            pixel_size = 2 * 20037508.342789244 / 512
            (root / "detail.pgw").write_text(
                f"{pixel_size}\n0\n0\n{-pixel_size}\n{pixel_size / 2}\n"
                f"{20037508.342789244 - pixel_size / 2}\n",
                encoding="utf-8",
            )
            (root / "detail.prj").write_text("Mercator_Auxiliary_Sphere", encoding="utf-8")
            output = root / "satellite"

            with patch("kody_copernicus.server.COPERNICUS_DIRECTORY", output):
                result = import_georeferenced_png(str(source))
                ancestor = raster_tile_bytes(output, 0, 0, 0)

            self.assertIn("Updated 1 impacted lower-zoom tiles", result)
            self.assertIsNotNone(ancestor)
            with Image.open(BytesIO(ancestor)) as image:
                self.assert_pixel_color_near(
                    image.getpixel((192, 64)), (0, 128, 0, 255)
                )
                self.assert_pixel_color_near(
                    image.getpixel((64, 64)), (0, 0, 0, 0)
                )

    def test_georeferenced_world_mercator_png_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "world-mercator.png"
            Image.new("RGBA", (256, 256), "green").save(source)
            pixel_size = 38_000_000 / 256
            (root / "world-mercator.pgw").write_text(
                f"{pixel_size}\n0\n0\n{-pixel_size}\n{-19_000_000 + pixel_size / 2}\n"
                f"{19_000_000 - pixel_size / 2}\n",
                encoding="utf-8",
            )
            (root / "world-mercator.prj").write_text('PROJCS["WGS_84_World_Mercator"]', encoding="utf-8")
            output = root / "satellite"

            with patch("kody_copernicus.server.COPERNICUS_DIRECTORY", output):
                result = import_georeferenced_png(str(source))

            self.assertIn("Imported 1 satellite tiles", result)
            self.assertIsNotNone(tile_store(output).get(0, 0, 0))

    def test_georeferenced_png_can_skip_impacted_overview_tile_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "detail.png"
            Image.new("RGBA", (256, 256), "green").save(source)
            pixel_size = 2 * 20037508.342789244 / 512
            (root / "detail.pgw").write_text(
                f"{pixel_size}\n0\n0\n{-pixel_size}\n{pixel_size / 2}\n"
                f"{20037508.342789244 - pixel_size / 2}\n",
                encoding="utf-8",
            )
            (root / "detail.prj").write_text("Mercator_Auxiliary_Sphere", encoding="utf-8")
            output = root / "satellite"

            with patch("kody_copernicus.server.COPERNICUS_DIRECTORY", output):
                result = import_georeferenced_png(str(source), rebuild_pyramid=False)

            self.assertNotIn("Updated", result)
            self.assertIsNotNone(tile_store(output).get(1, 1, 0))
            self.assertIsNone(tile_store(output).get(0, 0, 0))


if __name__ == "__main__":
    unittest.main()
