import unittest

from kody_adsb.adsb_service import (
    REFRESH_INTERVAL_SECONDS,
    TRAIL_DURATION_SECONDS,
    AdsbConfiguration,
    AdsbStore,
    ViewportContext,
    aircraft_url,
    get_manifest,
)
from kody_adsb.units import (
    kilometres_to_metres,
    kilometres_to_nautical_miles,
    knots_to_metres_per_second,
    nautical_miles_to_kilometres,
)


class AdsbStoreTests(unittest.TestCase):
    def test_provider_unit_conversions_preserve_kody_kilometres(self) -> None:
        self.assertAlmostEqual(kilometres_to_nautical_miles(25), 13.4989200864)
        self.assertAlmostEqual(nautical_miles_to_kilometres(13.4989200864), 25)
        self.assertEqual(kilometres_to_metres(25), 25_000)
        self.assertAlmostEqual(knots_to_metres_per_second(100), 51.4444)

    def test_manifest_registers_set_origin_context_action(self) -> None:
        actions = get_manifest()["contextMenuActions"]
        self.assertEqual(
            actions,
            [{
                "id": "set-origin",
                "label": "Set Origin Here",
                "path": "/api/actions/set-origin",
                "method": "POST",
            }],
        )

    def test_builds_position_and_rolling_trail_from_cached_observations(self) -> None:
        clock = [1_000.0]
        observations = iter(
            [
                [
                    {
                        "hex": "40621d",
                        "flight": "BAW123  ",
                        "r": "G-TEST",
                        "lat": 50.6,
                        "lon": -1.2,
                        "alt_baro": 20_000,
                        "gs": 410.0,
                        "track": 90.0,
                    }
                ],
                [
                    {
                        "hex": "40621d",
                        "flight": "BAW123  ",
                        "r": "G-TEST",
                        "lat": 50.61,
                        "lon": -1.19,
                        "alt_baro": 20_100,
                        "gs": 411.0,
                        "track": 91.0,
                    }
                ],
            ]
        )
        requests: list[str] = []
        store = AdsbStore(
            fetcher=lambda url: (requests.append(url), next(observations))[1],
            clock=lambda: clock[0],
        )

        initial = store.response()
        self.assertEqual(len(initial["features"]), 1)
        self.assertEqual(initial["features"][0]["geometry"]["type"], "Point")
        self.assertEqual(initial["features"][0]["properties"]["callsign"], "BAW123")
        self.assertEqual(requests, ["https://api.adsb.lol/v2/point/50.65/-1.3/13.499"])

        clock[0] += REFRESH_INTERVAL_SECONDS
        updated = store.response()
        trail, position = updated["features"]
        self.assertEqual(trail["geometry"]["type"], "LineString")
        self.assertEqual(len(trail["geometry"]["coordinates"]), 2)
        self.assertEqual(position["geometry"]["coordinates"], [-1.19, 50.61])
        self.assertEqual(trail["properties"]["durationSeconds"], TRAIL_DURATION_SECONDS)
        self.assertNotEqual(updated["revision"], initial["revision"])

    def test_keeps_cached_data_and_uses_backoff_after_a_failed_refresh(self) -> None:
        clock = [1_000.0]
        store = AdsbStore(fetcher=lambda _: (_ for _ in ()).throw(RuntimeError("rate limited")), clock=lambda: clock[0])

        response = store.response()

        self.assertEqual(response["features"], [])
        self.assertEqual(response["source"]["lastError"], "rate limited")
        self.assertEqual(store.next_refresh, clock[0] + REFRESH_INTERVAL_SECONDS * 2)

    def test_extrapolates_shared_positions_and_draws_configured_boundary(self) -> None:
        clock = [1_000.0]
        store = AdsbStore(
            fetcher=lambda _: [
                {"hex": "40621d", "lat": 50.6, "lon": -1.2, "gs": 200.0, "track": 90.0}
            ],
            clock=lambda: clock[0],
        )
        store.apply_configuration(
            AdsbConfiguration(extrapolate=True, localRefreshSeconds=1, drawBoundary=True)
        )

        observed = store.response()
        clock[0] += 5
        extrapolated = store.response()

        observed_position = next(
            feature for feature in observed["features"] if feature["geometry"]["type"] == "Point"
        )
        extrapolated_position = next(
            feature for feature in extrapolated["features"] if feature["geometry"]["type"] == "Point"
        )
        boundary = next(
            feature
            for feature in extrapolated["features"]
            if feature["properties"]["kind"] == "adsb-boundary"
        )
        self.assertNotEqual(
            observed_position["geometry"]["coordinates"],
            extrapolated_position["geometry"]["coordinates"],
        )
        self.assertTrue(extrapolated_position["properties"]["isExtrapolated"])
        self.assertEqual(boundary["properties"]["lineStyle"], "dotted")

    def test_applies_configured_trail_length_and_fade(self) -> None:
        clock = [1_000.0]
        observations = iter(
            [
                [{"hex": "40621d", "lat": 50.6, "lon": -1.2}],
                [{"hex": "40621d", "lat": 50.61, "lon": -1.19}],
                [{"hex": "40621d", "lat": 50.62, "lon": -1.18}],
            ]
        )
        store = AdsbStore(
            fetcher=lambda _: next(observations),
            clock=lambda: clock[0],
        )
        store.apply_configuration(AdsbConfiguration(trackLengthSeconds=10, trackFade=True))

        store.response()
        clock[0] += REFRESH_INTERVAL_SECONDS
        store.response()
        clock[0] += REFRESH_INTERVAL_SECONDS
        response = store.response()

        trail = next(feature for feature in response["features"] if feature["properties"]["kind"] == "track-trail")
        self.assertEqual(trail["properties"]["durationSeconds"], 10)
        self.assertTrue(trail["properties"]["fadeTrail"])
        self.assertEqual(trail["geometry"]["coordinates"], [[-1.19, 50.61], [-1.18, 50.62]])

    def test_manifest_describes_trail_length_and_fade_configuration(self) -> None:
        fields = get_manifest()["configuration"]["fields"]
        fields_by_key = {field["key"]: field for field in fields}

        self.assertEqual(fields_by_key["trackLengthSeconds"]["label"], "Track length (seconds)")
        self.assertEqual(fields_by_key["trackLengthSeconds"]["minimum"], 10)
        self.assertEqual(fields_by_key["trackLengthSeconds"]["maximum"], 3600)
        self.assertEqual(fields_by_key["trackFade"]["label"], "Track fade")
        self.assertEqual(fields_by_key["trackFade"]["type"], "boolean")
        self.assertEqual(fields_by_key["displayColor"]["label"], "Display color")
        self.assertEqual(fields_by_key["displayColor"]["type"], "color")

    def test_applies_configured_display_color_to_cached_features(self) -> None:
        store = AdsbStore(
            fetcher=lambda _: [{"hex": "40621d", "lat": 50.6, "lon": -1.2}],
            clock=lambda: 1_000.0,
        )

        store.response()
        store.apply_configuration(AdsbConfiguration(displayColor="#00d4ff"))

        response = store.response()
        self.assertTrue(all(feature["properties"]["color"] == "#00d4ff" for feature in response["features"]))

    def test_filters_features_to_the_shared_viewport_and_margin(self) -> None:
        viewport = ViewportContext(
            west=-1.31,
            south=50.64,
            east=-1.29,
            north=50.66,
            zoom=18,
        )
        store = AdsbStore(
            fetcher=lambda _: [
                {"hex": "nearby", "lat": 50.65, "lon": -1.3},
                {"hex": "distant", "lat": 51.5, "lon": -2.0},
            ],
            clock=lambda: 1_000.0,
            viewport_reader=lambda: viewport,
        )
        store.apply_configuration(AdsbConfiguration(drawBoundary=True))

        response = store.response()

        self.assertTrue(response["source"]["viewportFiltered"])
        self.assertEqual(
            [feature["properties"]["icao24"] for feature in response["features"]],
            ["nearby"],
        )


if __name__ == "__main__":
    unittest.main()
