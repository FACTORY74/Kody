import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from kody_shape_server import shape_server
from kody_shape_server.shape_server import (
    ConfigurationStore,
    EventProcessor,
    PointEvent,
    QueuedEvent,
    Shape,
    ShapeStore,
    contains,
)


def make_shape(value: dict[str, object]):
    return TypeAdapter(Shape).validate_python(value)


class ShapeServerTests(unittest.TestCase):
    def test_manifest_advertises_a_geojson_data_layer(self) -> None:
        manifest = shape_server.get_manifest()

        self.assertEqual(manifest["dataEndpoint"], {
            "path": "/api/shapes/geojson",
            "format": "geojson",
            "refreshIntervalMilliseconds": 200,
        })
        self.assertEqual(manifest["capabilities"]["dataLayer"], {
            "kind": "geojson",
            "dataPath": "/api/shapes/geojson",
        })

    def test_creates_shapes_with_bounds_and_default_palette_color(self) -> None:
        store = ShapeStore()
        stored = store.create(make_shape({
            "type": "circle",
            "name": "Circle",
            "center": [-3.38, 53.13],
            "radius": 1_000,
        }))

        self.assertEqual(stored.shape.color, "#35b8ff")
        self.assertLess(stored.bounds.west, -3.38)
        self.assertGreater(stored.bounds.north, 53.13)
        self.assertTrue(contains(stored.shape, [-3.38, 53.13]))
        self.assertFalse(contains(stored.shape, [-3.38, 53.2]))

    def test_type_specific_containment_handles_rectangle_sector_and_polygon(self) -> None:
        rectangle = make_shape({
            "type": "rectangle", "name": "Rectangle", "color": "#001122",
            "origin": [0, 0], "width": 1_000, "height": 1_000, "angle": 45,
        })
        sector = make_shape({
            "type": "annularSector", "name": "Sector", "color": "#001122",
            "center": [0, 0], "innerRadius": 100, "outerRadius": 1_000,
            "startAngle": 0, "endAngle": 90,
        })
        polygon = make_shape({
            "type": "polygon", "name": "Polygon", "color": "#001122",
            "coordinates": [[0, 0], [1, 0], [0, 1]],
        })

        self.assertTrue(contains(rectangle, [0, 0]))
        self.assertTrue(contains(sector, [0.003, 0.003]))
        self.assertFalse(contains(sector, [-0.003, 0.003]))
        self.assertTrue(contains(polygon, [0.1, 0.1]))
        self.assertFalse(contains(polygon, [1, 1]))

    def test_event_endpoint_enforces_queue_capacity_and_records_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            configuration = ConfigurationStore(Path(directory) / "config.json")
            configuration.update(
                shape_server.ShapeServerConfiguration(processingRate=10_000, queueCapacity=1),
                None,
            )
            store = ShapeStore()
            processor = EventProcessor(store, configuration)
            store.create(make_shape({
                "id": "circle", "type": "circle", "name": "Circle", "color": "#001122",
                "center": [0, 0], "radius": 1_000,
            }))
            with (
                patch.object(shape_server, "shapes", store),
                patch.object(shape_server, "configuration_store", configuration),
                patch.object(shape_server, "events", processor),
                TestClient(shape_server.app) as client,
            ):
                accepted = client.post("/api/events", json={"eventId": "inside", "longitude": 0, "latitude": 0})
                self.assertEqual(accepted.status_code, 202)
                self.assertEqual(accepted.json()["statusPath"], "http://testserver/api/events")
                for _ in range(100):
                    if client.get("/api/events").json()["matches"]:
                        break
                matches = client.get("/api/events").json()["matches"]
                self.assertEqual(matches[0]["event"]["eventId"], "inside")
                self.assertEqual(matches[0]["shape"]["properties"]["shapeId"], "circle")

    def test_event_processor_rejects_queue_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            processor = EventProcessor(ShapeStore(), ConfigurationStore(Path(directory) / "config.json"))
            processor.start()
            processor.queue = asyncio.Queue(maxsize=1)
            processor.enqueue(PointEvent(longitude=0, latitude=0))

            with self.assertRaises(HTTPException) as error:
                processor.enqueue(PointEvent(longitude=0, latitude=0))
            self.assertEqual(error.exception.status_code, 429)

    def test_event_processor_delivers_confirmed_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            configuration = ConfigurationStore(Path(directory) / "config.json")
            configuration.update(
                shape_server.ShapeServerConfiguration(outputUrl="https://example.test/events"),
                None,
            )
            store = ShapeStore()
            stored = store.create(make_shape({
                "type": "circle", "name": "Circle", "color": "#001122",
                "center": [0, 0], "radius": 1_000,
            }))
            processor = EventProcessor(store, configuration)
            processor.deliver = AsyncMock(return_value="delivered")

            asyncio.run(processor.process(QueuedEvent(PointEvent(longitude=0, latitude=0), 0)))

            processor.deliver.assert_awaited_once()
            self.assertEqual(processor.events()[0]["shape"]["id"], stored.shape.id)
            self.assertEqual(processor.events()[0]["delivery"], "delivered")

    def test_shapes_are_not_serialized_to_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            configuration_file = Path(directory) / "shape-server.json"
            configuration = ConfigurationStore(configuration_file)
            configuration.update(shape_server.ShapeServerConfiguration(outputUrl="https://example.test/events"), None)

            self.assertNotIn("shapes", configuration_file.read_text(encoding="utf-8"))
            self.assertEqual(configuration.response()["outputUrl"], "https://example.test/events")
