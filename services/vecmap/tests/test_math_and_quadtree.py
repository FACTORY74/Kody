import unittest

from shapely.geometry import Point

from kody_vecmap.math import MapMath
from kody_vecmap.quadtree import IndexedFeature, QuadTreeMap


class MapMathTests(unittest.TestCase):
    def test_coordinate_round_trip(self) -> None:
        longitude, latitude = -0.1276, 51.5072
        world_x, world_y = MapMath.lon_lat_to_world(longitude, latitude)

        result_longitude, result_latitude = MapMath.world_to_lon_lat(world_x, world_y)

        self.assertAlmostEqual(result_longitude, longitude)
        self.assertAlmostEqual(result_latitude, latitude)

    def test_root_tile_covers_web_mercator_world(self) -> None:
        bounds = MapMath.tile_bounds(0, 0, 0)

        self.assertAlmostEqual(bounds.west, -180)
        self.assertAlmostEqual(bounds.east, 180)
        self.assertAlmostEqual(bounds.south, -MapMath.MAX_LATITUDE)
        self.assertAlmostEqual(bounds.north, MapMath.MAX_LATITUDE)


class QuadTreeMapTests(unittest.TestCase):
    def test_query_returns_only_intersecting_features(self) -> None:
        index = QuadTreeMap(node_capacity=1)
        london = IndexedFeature({"name": "London"}, Point(-0.1276, 51.5072))
        sydney = IndexedFeature({"name": "Sydney"}, Point(151.2093, -33.8688))
        index.insert(london)
        index.insert(sydney)

        bounds = MapMath.tile_bounds(2, 1, 1)

        self.assertEqual(list(index.query(bounds)), [london])


if __name__ == "__main__":
    unittest.main()
