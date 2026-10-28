import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from io import BytesIO

from kody_worldcover.server import MAX_NATIVE_ZOOM, Viewport, build_tile_pyramid, raster_tile_bytes, source_coordinates_for_bounds, source_filename, source_coordinate, tile_bounds, tile_store, viewport_tiles, web_mercator_bounds, write_tile


class WorldCoverTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
