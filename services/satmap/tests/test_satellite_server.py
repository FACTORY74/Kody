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
from unittest.mock import patch

from PIL import Image, ImageDraw

from kody_satmap.server import (
    import_georeferenced_png,
    raster_tile_bytes,
    satellite_tile_store,
)


class SatelliteTileTests(unittest.TestCase):
    def assert_pixel_color_near(
        self, actual: tuple[int, ...], expected: tuple[int, int, int, int]
    ) -> None:
        self.assertIn(len(actual), (3, 4))
        for value, expected_value in zip(actual[:3], expected[:3]):
            self.assertLessEqual(abs(value - expected_value), 2)
        if len(actual) == 4:
            self.assertEqual(actual[3], expected[3])

    def test_missing_child_tile_is_cropped_from_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = satellite_tile_store(root)
            image = Image.new("RGB", (256, 256), "black")
            ImageDraw.Draw(image).rectangle((128, 0, 255, 127), fill="red")
            store.put_image(0, 0, 0, image)
            image.close()

            tile = raster_tile_bytes(root, 1, 1, 0)

            self.assertIsNotNone(tile)
            with Image.open(BytesIO(tile)) as generated:
                self.assert_pixel_color_near(
                    generated.getpixel((128, 128)), (255, 0, 0, 255)
                )

    def test_transparent_child_tile_is_composited_over_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = satellite_tile_store(root)
            parent = Image.new("RGBA", (256, 256), "red")
            store.put_image(0, 0, 0, parent)
            parent.close()
            child = Image.new("RGBA", (256, 256))
            ImageDraw.Draw(child).rectangle((128, 0, 255, 255), fill="blue")
            store.put_image(1, 1, 0, child)
            child.close()

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

            with patch("kody_satmap.server.SATELLITE_DIRECTORY", output):
                result = import_georeferenced_png(str(source))

            self.assertIn("Imported 1 satellite tiles", result)
            self.assertIsNotNone(satellite_tile_store(output).get(0, 0, 0))

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

            with patch("kody_satmap.server.SATELLITE_DIRECTORY", output):
                result = import_georeferenced_png(str(source))
                ancestor = raster_tile_bytes(output, 0, 0, 0)

            self.assertIn("Updated 1 impacted lower-zoom tiles", result)
            self.assertIsNotNone(ancestor)
            with Image.open(BytesIO(ancestor)) as image:
                for actual, expected in zip(image.getpixel((192, 64)), (0, 128, 0, 255)):
                    self.assertLessEqual(abs(actual - expected), 2)
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

            with patch("kody_satmap.server.SATELLITE_DIRECTORY", output):
                result = import_georeferenced_png(str(source))

            self.assertIn("Imported 1 satellite tiles", result)
            self.assertIsNotNone(satellite_tile_store(output).get(0, 0, 0))

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

            with patch("kody_satmap.server.SATELLITE_DIRECTORY", output):
                result = import_georeferenced_png(str(source), rebuild_pyramid=False)

            self.assertNotIn("Updated", result)
            store = satellite_tile_store(output)
            self.assertIsNotNone(store.get(1, 1, 0))
            self.assertIsNone(store.get(0, 0, 0))


if __name__ == "__main__":
    unittest.main()
