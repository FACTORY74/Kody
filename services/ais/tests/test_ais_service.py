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

from kody_ais.ais_service import (
    AisConfiguration,
    AisStore,
    OpenSeaFeedClient,
    ais_symbol,
    get_manifest,
    subscription_boundary,
    subscription_bounds,
)


class AisServiceTests(unittest.TestCase):
    def test_clear_discards_cached_vessels_and_trails(self) -> None:
        store = AisStore()
        store.ingest({"MetaData": {}, "Message": {"PositionReport": {"UserID": 123456789, "Latitude": 50.6, "Longitude": -1.2}}})
        store.clear()

        self.assertEqual(store.vessels, {})
        self.assertEqual(store.ship_types, {})
        self.assertEqual(store.trails, {})

    def test_feed_client_is_inactive_until_explicitly_enabled(self) -> None:
        client = OpenSeaFeedClient(AisStore())

        self.assertFalse(client.active_event.is_set())
        client.set_active(True)
        self.assertTrue(client.active_event.is_set())
        client.set_active(False)
        self.assertFalse(client.active_event.is_set())

    def test_builds_ship_position_trail_and_boundary(self) -> None:
        clock = [1_000.0]
        store = AisStore(clock=lambda: clock[0])
        message = {"MetaData": {"ShipName": "Test vessel"}, "Message": {"PositionReport": {"UserID": 123456789, "Latitude": 50.6, "Longitude": -1.2, "Cog": 90, "Sog": 12}}}
        store.ingest(message)
        clock[0] += 10
        message["Message"]["PositionReport"].update({"Latitude": 50.61, "Longitude": -1.19})
        store.ingest(message)
        store.apply_configuration(AisConfiguration(drawBoundary=True, extrapolate=True))

        response = store.response()
        position = next(feature for feature in response["features"] if feature["geometry"]["type"] == "Point")
        self.assertEqual(position["id"], "ais:123456789")
        self.assertEqual(
            position["properties"]["kody"],
            {"selectable": True, "selectionLabel": "Test vessel"},
        )
        self.assertEqual(position["properties"]["symbol"], "ship")
        self.assertEqual(position["properties"]["bearingDegrees"], 90)
        trail = next(feature for feature in response["features"] if feature["properties"]["kind"] == "track-trail")
        self.assertEqual(
            trail["geometry"]["coordinates"],
            [[-1.2, 50.6], [-1.19, 50.61]],
        )
        boundary = next(feature for feature in response["features"] if feature["properties"]["kind"] == "ais-boundary")
        self.assertFalse(boundary["properties"]["label"])
        self.assertEqual(
            boundary["geometry"]["coordinates"],
            subscription_boundary(store.configuration),
        )

    def test_subscription_bounds_follow_configured_radius(self) -> None:
        bounds = subscription_bounds(AisConfiguration(radiusKilometres=50))
        self.assertLess(bounds[0][0], 50.65)
        self.assertGreater(bounds[1][1], -1.3)

    def test_filters_reports_outside_the_subscription_rectangle(self) -> None:
        store = AisStore(
            configuration=AisConfiguration(
                centre={"longitude": 0, "latitude": 0},
                radiusKilometres=1,
            )
        )
        store.ingest({
            "MetaData": {"MMSI": 123456789},
            "Message": {
                "PositionReport": {
                    "UserID": 123456789,
                    "Latitude": 0.02,
                    "Longitude": 0.02,
                }
            },
        })

        self.assertEqual(store.response()["features"], [])

    def test_retains_reports_inside_the_subscription_rectangle_corner(self) -> None:
        store = AisStore(
            configuration=AisConfiguration(
                centre={"longitude": 0, "latitude": 0},
                radiusKilometres=1,
            )
        )
        store.ingest({
            "MetaData": {"MMSI": 123456789},
            "Message": {
                "PositionReport": {
                    "UserID": 123456789,
                    "Latitude": 0.008,
                    "Longitude": 0.008,
                }
            },
        })

        self.assertEqual(len(store.response()["features"]), 1)

    def test_maps_static_ais_types_to_vessel_symbols(self) -> None:
        expected_symbols = {
            30: "fishing_boat", 31: "tug", 35: "military_ship", 36: "sailboat",
            37: "yacht", 40: "high_speed_craft", 50: "pilot_boat", 51: "rescue_boat",
            55: "law_enforcement_boat", 60: "passenger_ship", 70: "cargo_ship",
            80: "tanker", 90: "service_boat", 0: "ship",
        }
        for ship_type, symbol in expected_symbols.items():
            self.assertEqual(ais_symbol(ship_type), symbol)

        clock = [1_000.0]
        store = AisStore(clock=lambda: clock[0])
        store.ingest({"MetaData": {"MMSI": 123456789}, "Message": {"ShipStaticData": {"UserID": 123456789, "Type": 80}}})
        store.ingest({"MetaData": {"ShipName": "Test tanker"}, "Message": {"PositionReport": {"UserID": 123456789, "Latitude": 50.6, "Longitude": -1.2, "Cog": 90, "Sog": 12}}})

        position = next(feature for feature in store.response()["features"] if feature["geometry"]["type"] == "Point")
        self.assertEqual(position["properties"]["symbol"], "tanker")
        self.assertEqual(position["properties"]["aisShipType"], 80)

    def test_manifest_exposes_adsb_comparable_controls(self) -> None:
        fields = {field["key"] for field in get_manifest()["configuration"]["fields"]}
        self.assertTrue({"centre", "radiusKilometres", "extrapolate", "drawBoundary", "trackLengthSeconds", "displayColor"} <= fields)
        self.assertEqual(
            get_manifest()["contextMenuActions"][0]["label"],
            "Set Origin Here",
        )


if __name__ == "__main__":
    unittest.main()
