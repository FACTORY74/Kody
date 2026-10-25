import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from kody_shapes import shapes_service
from kody_shapes.shapes_service import ShapeDefinition, ShapesStore, destination, shape_feature


def make_definition(value: dict[str, object]):
    return TypeAdapter(ShapeDefinition).validate_python(value)


class ShapesStoreTests(unittest.TestCase):
    def test_rectangle_geometry_uses_clockwise_width_and_height_faces(self) -> None:
        rectangle = make_definition({
            "type": "rectangle",
            "name": "Rotated rectangle",
            "color": "#001122",
            "origin": [0, 0],
            "width": 100,
            "height": 20,
            "angle": 90,
        })
        ring = shape_feature(rectangle)["geometry"]["coordinates"][0]

        width_face = [
            (ring[1][index] + ring[2][index]) / 2
            for index in range(2)
        ]
        height_face = [
            (ring[2][index] + ring[3][index]) / 2
            for index in range(2)
        ]
        for actual, expected in zip(width_face, destination([0, 0], 180, 50)):
            self.assertAlmostEqual(actual, expected, places=7)
        for actual, expected in zip(height_face, destination([0, 0], 90, 10)):
            self.assertAlmostEqual(actual, expected, places=7)

    def test_initializes_missing_catalogue_and_renders_circle_as_geojson(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalogue_file = Path(directory) / "shapes" / "shapes.json"
            store = ShapesStore(catalogue_file)

            created = store.create(
                make_definition({
                    "type": "circle",
                    "name": "Test circle",
                    "color": "#0099ff",
                    "center": [0, 0],
                    "radius": 1_000,
                }),
                None,
            )
            response = store.geojson_response()

            self.assertTrue(catalogue_file.is_file())
            self.assertEqual(created.type, "circle")
            self.assertEqual(response["type"], "FeatureCollection")
            self.assertEqual(response["features"][0]["properties"]["name"], "Test circle")
            self.assertEqual(response["features"][0]["geometry"]["type"], "Polygon")
            self.assertEqual(len(response["revision"]), 64)

    def test_persists_all_definition_types_and_checks_revisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalogue_file = Path(directory) / "shapes.json"
            store = ShapesStore(catalogue_file)
            definitions = [
                {"id": "circle", "type": "circle", "name": "Circle", "color": "#000001", "center": [0, 0], "radius": 10},
                {"id": "rectangle", "type": "rectangle", "name": "Rectangle", "color": "#000002", "origin": [1, 1], "width": 20, "height": 10, "angle": 45},
                {"id": "sector", "type": "annularSector", "name": "Sector", "color": "#000003", "center": [2, 2], "innerRadius": 5, "outerRadius": 10, "startAngle": 0, "endAngle": 90},
                {"id": "polygon", "type": "polygon", "name": "Polygon", "color": "#000004", "coordinates": [[0, 0], [1, 0], [0, 1]]},
            ]
            for shape_definition in definitions:
                store.create(make_definition(shape_definition), None)
            revision = store.definitions_response()["revision"]

            reloaded = ShapesStore(catalogue_file)
            self.assertEqual([shape["type"] for shape in reloaded.definitions_response()["shapes"]], ["circle", "rectangle", "annularSector", "polygon"])
            self.assertEqual(len(reloaded.geojson_response()["features"]), 4)
            with self.assertRaises(HTTPException) as error:
                store.delete("circle", "stale")
            self.assertEqual(error.exception.status_code, 409)
            store.delete("circle", revision)
            self.assertNotEqual(store.definitions_response()["revision"], revision)

    def test_migrates_legacy_polygons_without_overwriting_geojson(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_directory = Path(directory) / "shapes"
            data_directory.mkdir()
            legacy_file = data_directory / "shapes.geojson"
            legacy_file.write_text(json.dumps({
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "properties": {"name": "Legacy", "color": "#0099ff"},
                    "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [0, 1], [0, 0]]]},
                }],
            }), encoding="utf-8")

            store = ShapesStore(data_directory / "shapes.json", legacy_file)

            self.assertEqual(store.definitions_response()["shapes"][0]["type"], "polygon")
            self.assertTrue(legacy_file.is_file())

    def test_rejects_unknown_update_and_invalid_definition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ShapesStore(Path(directory) / "shapes.json")
            with self.assertRaises(HTTPException) as error:
                store.update("missing", make_definition({
                    "type": "circle", "name": "Circle", "color": "#001122",
                    "center": [0, 0], "radius": 1,
                }), None)
            self.assertEqual(error.exception.status_code, 404)
            with self.assertRaises(Exception):
                store.create(make_definition({
                    "type": "annularSector", "name": "Bad", "color": "#001122",
                    "center": [0, 0], "innerRadius": 2, "outerRadius": 1,
                    "startAngle": 0, "endAngle": 90,
                }), None)

    def test_crud_endpoints_use_catalogue_revisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(shapes_service, "shapes_store", ShapesStore(Path(directory) / "shapes.json")):
                client = TestClient(shapes_service.app)
                manifest = client.get("/api/kody/manifest").json()
                self.assertEqual(
                    manifest["capabilities"]["shapeEditor"]["revisionHeader"], "If-Match"
                )
                self.assertEqual(manifest["dataEndpoint"]["path"], "/api/shapes")
                self.assertEqual(manifest["capabilities"]["dataLayer"]["kind"], "geojson")
                self.assertEqual(manifest["capabilities"]["workspace"]["openPath"], "/api/workspace")
                feature_action = manifest["featureActions"][0]
                self.assertEqual(feature_action["id"], "edit-on-map")
                self.assertEqual(feature_action["workspace"], {
                    "mode": "edit-on-map",
                    "resource": "definitions",
                    "featureIdProperty": "shapeId",
                })
                workspace = client.get("/api/workspace").json()
                self.assertEqual(workspace["kind"], "shapes-catalog")
                self.assertEqual(workspace["resources"]["definitions"], "/api/shapes/definitions")
                created = client.post("/api/shapes/definitions", json={
                    "type": "circle", "name": "Circle", "color": "#001122",
                    "center": [0, 0], "radius": 1,
                })
                self.assertEqual(created.status_code, 201)
                invalid = client.post("/api/shapes/definitions", json={
                    "type": "annularSector", "name": "Bad", "color": "#001122",
                    "center": [0, 0], "innerRadius": 2, "outerRadius": 1,
                    "startAngle": 0, "endAngle": 90,
                })
                self.assertEqual(invalid.status_code, 422)
                self.assertIn("outerRadius", str(invalid.json()["detail"]))
                shape_id = created.json()["shape"]["id"]
                revision = created.json()["revision"]
                updated = client.put(
                    f"/api/shapes/definitions/{shape_id}",
                    headers={"If-Match": revision},
                    json={
                        "type": "polygon", "name": "Polygon", "color": "#001122",
                        "coordinates": [[0, 0], [1, 0], [0, 1]],
                    },
                )
                self.assertEqual(updated.status_code, 200)
                feature_properties = client.get("/api/shapes").json()["features"][0]["properties"]
                self.assertEqual(feature_properties["shapeType"], "polygon")
                self.assertTrue(feature_properties["kody"]["selectable"])
                self.assertEqual(feature_properties["kodyActions"], ["edit-on-map"])
                self.assertEqual(client.delete(
                    f"/api/shapes/definitions/{shape_id}",
                    headers={"If-Match": revision},
                ).status_code, 409)
                deleted = client.delete(f"/api/shapes/definitions/{shape_id}")
                self.assertEqual(deleted.status_code, 200)
                self.assertEqual(deleted.json()["shapes"], [])


if __name__ == "__main__":
    unittest.main()
