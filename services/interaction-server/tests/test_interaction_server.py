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

import unittest

from kody_interaction_server.interaction_server import InteractionEvent, InteractionStore


def interaction_event() -> InteractionEvent:
    return InteractionEvent(
        event={
            "eventId": "tracks:track:aircraft-1",
            "longitude": -3.2,
            "latitude": 53.1,
            "properties": {"trackName": "Cessna"},
        },
        shape={"properties": {"shapeId": "shape-1", "name": "Training area"}},
        receivedAt=1_000,
        matchedAt=1_000,
        delivery="not-configured",
    )


class InteractionStoreTests(unittest.TestCase):
    def test_refreshes_a_circle_for_each_track_shape_pair(self) -> None:
        clock = [1_000.0]
        store = InteractionStore(clock=lambda: clock[0])

        first = store.record(interaction_event())
        clock[0] += 0.5
        second_event = interaction_event()
        second_event.event["longitude"] = -3.1
        second_event.event["latitude"] = 53.2
        second = store.record(second_event)
        collection = store.geojson_response()

        self.assertEqual(first.key, second.key)
        self.assertEqual(second.expires_at, 1_001.5)
        self.assertEqual(len(collection["features"]), 1)
        feature = collection["features"][0]
        self.assertEqual(feature["properties"]["trackName"], "Cessna")
        self.assertEqual(feature["properties"]["shapeName"], "Training area")
        self.assertAlmostEqual(feature["geometry"]["coordinates"][0][0][0], -3.1, places=2)
        self.assertAlmostEqual(feature["geometry"]["coordinates"][0][0][1], 53.2, places=2)
        self.assertEqual(len(feature["geometry"]["coordinates"][0]), 33)

        clock[0] += 0.51
        self.assertEqual(len(store.geojson_response()["features"]), 1)

    def test_expires_circles_after_one_second(self) -> None:
        clock = [1_000.0]
        store = InteractionStore(clock=lambda: clock[0])
        store.record(interaction_event())

        clock[0] += 1.01

        self.assertEqual(store.geojson_response()["features"], [])
