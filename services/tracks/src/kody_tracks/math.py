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

"""Minimal Web Mercator calculations owned by the Tracks service."""

from __future__ import annotations

from math import atan, cos, degrees, log, pi, radians, sinh, tan


class MapMath:
    """Coordinate conversions for the Tracks service's projected simulation."""

    MAX_LATITUDE = 85.05112878
    WEB_MERCATOR_EARTH_RADIUS_METRES = 6_378_137.0
    WEB_MERCATOR_WORLD_METRES = 2 * pi * WEB_MERCATOR_EARTH_RADIUS_METRES

    @classmethod
    def clamp_latitude(cls, latitude: float) -> float:
        return max(-cls.MAX_LATITUDE, min(cls.MAX_LATITUDE, latitude))

    @classmethod
    def lon_lat_to_world(cls, longitude: float, latitude: float) -> tuple[float, float]:
        latitude = cls.clamp_latitude(latitude)
        return (
            (longitude + 180.0) / 360.0,
            (1.0 - log(tan(radians(latitude)) + 1.0 / cos(radians(latitude))) / pi) / 2.0,
        )

    @classmethod
    def world_to_lon_lat(cls, x: float, y: float) -> tuple[float, float]:
        return x * 360.0 - 180.0, degrees(atan(sinh(pi * (1.0 - 2.0 * y))))

    @classmethod
    def metres_to_world_units(cls, metres: float) -> float:
        return metres / cls.WEB_MERCATOR_WORLD_METRES
