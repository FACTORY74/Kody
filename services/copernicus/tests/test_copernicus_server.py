import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image, ImageDraw

from kody_copernicus.server import (
    ConfigurationStore,
    CopernicusConfiguration,
    Viewport,
    copernicus_request,
    download_current_viewport,
    import_georeferenced_png,
    raster_tile_bytes,
    viewport_tiles,
)


class SatelliteTileTests(unittest.TestCase):
    def test_current_viewport_uses_quad_tiles_at_the_browser_tile_zoom(self) -> None:
        zoom, tiles = viewport_tiles(
            Viewport(west=-1, south=-1, east=1, north=1, zoom=3.9)
        )

        self.assertEqual(zoom, 3)
        self.assertEqual(tiles, [(3, 3), (4, 3), (3, 4), (4, 4)])

    def test_process_request_uses_web_mercator_tile_bounds_and_latest_mosaic(self) -> None:
        request = copernicus_request(2, 1, 2)

        self.assertEqual(
            request["input"]["bounds"]["properties"]["crs"],
            "http://www.opengis.net/def/crs/EPSG/0/3857",
        )
        self.assertEqual(request["output"]["width"], 256)
        self.assertEqual(request["input"]["data"][0]["dataFilter"]["mosaickingOrder"], "mostRecent")

    def test_configuration_response_redacts_and_preserves_client_secret(self) -> None:
        store = ConfigurationStore()
        initial = CopernicusConfiguration(clientId="client", clientSecret="secret")
        store.update(initial, None)

        self.assertEqual(store.response()["clientSecret"], "")
        store.update(CopernicusConfiguration(clientId="client", clientSecret=""), store.revision())
        self.assertEqual(store.configuration.client_secret, "secret")

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
            configuration = CopernicusConfiguration(clientId="client", clientSecret="secret")

            with (
                patch("kody_copernicus.server.COPERNICUS_DIRECTORY", root),
                patch(
                    "kody_copernicus.server.viewport_from_configuration_service",
                    return_value=Viewport(west=-1, south=-1, east=1, north=1, zoom=0),
                ),
                patch("kody_copernicus.server.requests.Session", return_value=session),
            ):
                message = download_current_viewport(configuration)

            self.assertEqual(message, "Downloaded 1 Copernicus Global Mosaic tiles at zoom 0.")
            self.assertTrue((root / "0" / "0" / "0.png").is_file())
            self.assertEqual(session.post.call_count, 2)

    def test_missing_child_tile_is_cropped_from_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent_path = root / "0" / "0"
            parent_path.mkdir(parents=True)
            image = Image.new("RGB", (256, 256), "black")
            ImageDraw.Draw(image).rectangle((128, 0, 255, 127), fill="red")
            image.save(parent_path / "0.png")

            tile = raster_tile_bytes(root, 1, 1, 0)

            self.assertIsNotNone(tile)
            with Image.open(BytesIO(tile)) as generated:
                self.assertEqual(generated.getpixel((128, 128)), (255, 0, 0, 255))

    def test_transparent_child_tile_is_composited_over_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent_path = root / "0" / "0"
            child_path = root / "1" / "1"
            parent_path.mkdir(parents=True)
            child_path.mkdir(parents=True)
            Image.new("RGBA", (256, 256), "red").save(parent_path / "0.png")
            child = Image.new("RGBA", (256, 256))
            ImageDraw.Draw(child).rectangle((128, 0, 255, 255), fill="blue")
            child.save(child_path / "0.png")

            tile = raster_tile_bytes(root, 1, 1, 0)

            self.assertIsNotNone(tile)
            with Image.open(BytesIO(tile)) as generated:
                self.assertEqual(generated.getpixel((64, 128)), (255, 0, 0, 255))
                self.assertEqual(generated.getpixel((192, 128)), (0, 0, 255, 255))

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
            self.assertTrue((output / "0" / "0" / "0.png").is_file())

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
                self.assertEqual(image.getpixel((192, 64)), (0, 128, 0, 255))
                self.assertEqual(image.getpixel((64, 64)), (0, 0, 0, 0))

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
            self.assertTrue((output / "0" / "0" / "0.png").is_file())

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
            self.assertTrue((output / "1" / "1" / "0.png").is_file())
            self.assertFalse((output / "0" / "0" / "0.png").exists())


if __name__ == "__main__":
    unittest.main()
