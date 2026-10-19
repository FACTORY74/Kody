import json
import tempfile
import unittest
from pathlib import Path

from kody_shapes.shapes_service import ShapesStore


class ShapesStoreTests(unittest.TestCase):
    def test_initializes_missing_shapes_file_with_empty_collection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            shapes_file = Path(directory) / "shapes" / "shapes.geojson"

            response = ShapesStore(shapes_file).response()

            self.assertTrue(shapes_file.is_file())
            self.assertEqual(response["type"], "FeatureCollection")
            self.assertEqual(response["features"], [])

    def test_loads_named_coloured_geojson(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            shapes_file = Path(directory) / "shapes.geojson"
            shapes_file.write_text(
                json.dumps(
                    {
                        "type": "FeatureCollection",
                        "features": [
                            {
                                "type": "Feature",
                                "properties": {"name": "Test shape", "color": "#0099ff"},
                                "geometry": {"type": "Point", "coordinates": [0, 0]},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            response = ShapesStore(shapes_file).response()

        self.assertEqual(response["features"][0]["properties"]["name"], "Test shape")
        self.assertEqual(len(response["revision"]), 64)


if __name__ == "__main__":
    unittest.main()
