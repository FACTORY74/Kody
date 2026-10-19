import unittest

from kody_tracks.math import MapMath
from kody_tracks.track_service import (
    ADDITIONAL_TRACK_COUNT,
    ISLE_OF_WIGHT_LATITUDE,
    ISLE_OF_WIGHT_LONGITUDE,
    TRACK_RADIUS_METRES,
    TRACK_SPEED_METRES_PER_SECOND,
    TRACK_SYMBOLS,
    TRAIL_DURATION_SECONDS,
    TrackStore,
)


class TrackStoreTests(unittest.TestCase):
    def test_returns_current_positions_and_five_minute_tails(self) -> None:
        clock = [1_000_000.0]
        track_store = TrackStore(clock=lambda: clock[0])
        response = track_store.response()

        self.assertEqual(len(track_store.tracks), ADDITIONAL_TRACK_COUNT + 1)
        self.assertEqual(len(response["features"]), (ADDITIONAL_TRACK_COUNT + 1) * 2)
        trail, position = response["features"][:2]
        coordinates = trail["geometry"]["coordinates"]
        self.assertEqual(len(coordinates), TRAIL_DURATION_SECONDS + 1)
        self.assertEqual(position["geometry"]["coordinates"], coordinates[-1])
        self.assertEqual(position["properties"]["speedMetresPerSecond"], TRACK_SPEED_METRES_PER_SECOND)
        self.assertEqual(position["properties"]["symbol"], track_store.tracks[0].symbol)
        self.assertGreaterEqual(position["properties"]["bearingDegrees"], 0)
        self.assertLess(position["properties"]["bearingDegrees"], 360)
        self.assertAlmostEqual(
            position["properties"]["bearingDegrees"],
            (-track_store.tracks[0].phase - int(clock[0]) * track_store.tracks[0].angular_velocity)
            * 180 / 3.141592653589793 % 360,
        )

        center_x, center_y = MapMath.lon_lat_to_world(
            ISLE_OF_WIGHT_LONGITUDE, ISLE_OF_WIGHT_LATITUDE
        )
        point_x, point_y = MapMath.lon_lat_to_world(*position["geometry"]["coordinates"])
        projected_radius = (
            ((point_x - center_x) ** 2 + (point_y - center_y) ** 2) ** 0.5
            * MapMath.WEB_MERCATOR_WORLD_METRES
        )
        self.assertAlmostEqual(projected_radius, TRACK_RADIUS_METRES)
        initial_revision = response["revision"]
        clock[0] += 1
        self.assertNotEqual(track_store.response()["revision"], initial_revision)
        self.assertEqual({track.symbol for track in track_store.tracks}, set(TRACK_SYMBOLS))


if __name__ == "__main__":
    unittest.main()
