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

from kody_vecmap.math import MapMath


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
if __name__ == "__main__":
    unittest.main()
