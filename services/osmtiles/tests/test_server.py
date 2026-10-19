import tempfile
import unittest
from pathlib import Path

from kody_osmtiles.server import ConfigurationStore, TileConfiguration, source_files


class StaticTileServiceTests(unittest.TestCase):
    def test_discovers_supported_map_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for filename in ("region.osm.pbf", "region.gpkg.zip", "land-polygons-split-4326.zip"):
                (root / filename).touch()
            from kody_osmtiles import server
            original = server.IMPORTS_DIRECTORY
            server.IMPORTS_DIRECTORY = root
            try:
                self.assertEqual(source_files(), ["region.gpkg.zip", "region.osm.pbf"])
            finally:
                server.IMPORTS_DIRECTORY = original

    def test_rejects_inverted_zoom_range(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigurationStore(Path(directory) / "config.json")
            with self.assertRaises(Exception):
                store.update(TileConfiguration(minZoom=10, maxZoom=8), None)


if __name__ == "__main__":
    unittest.main()
