"""Web Mercator coordinate and display-tile calculations."""

from __future__ import annotations

from dataclasses import dataclass
from math import atan, cos, degrees, log, pi, radians, sinh, tan


@dataclass(frozen=True)
class Bounds:
    """Longitude/latitude bounds in west, south, east, north order."""

    west: float
    south: float
    east: float
    north: float


class MapMath:
    """Coordinate helpers mirrored by web/map-math.js."""

    MAX_LATITUDE = 85.05112878
    TILE_SIZE = 256
    WEB_MERCATOR_EARTH_RADIUS_METRES = 6_378_137.0
    WEB_MERCATOR_WORLD_METRES = 2 * pi * WEB_MERCATOR_EARTH_RADIUS_METRES

    @classmethod
    def clamp_latitude(cls, latitude: float) -> float:
        return max(-cls.MAX_LATITUDE, min(cls.MAX_LATITUDE, latitude))

    @classmethod
    def lon_lat_to_world(cls, longitude: float, latitude: float) -> tuple[float, float]:
        latitude = cls.clamp_latitude(latitude)
        x = (longitude + 180.0) / 360.0
        y = (1.0 - log(tan(radians(latitude)) + 1.0 / cos(radians(latitude))) / pi) / 2.0
        return x, y

    @classmethod
    def world_to_lon_lat(cls, x: float, y: float) -> tuple[float, float]:
        longitude = x * 360.0 - 180.0
        latitude = degrees(atan(sinh(pi * (1.0 - 2.0 * y))))
        return longitude, latitude

    @classmethod
    def metres_to_world_units(cls, metres: float) -> float:
        """Convert a Web Mercator ground-plane distance into normalized world units."""

        return metres / cls.WEB_MERCATOR_WORLD_METRES

    @classmethod
    def metres_per_second_to_pixels_per_second(
        cls, metres_per_second: float, zoom: float
    ) -> float:
        """Convert projected velocity into pixels per second at a Web Mercator zoom."""

        return cls.metres_to_world_units(metres_per_second) * cls.TILE_SIZE * 2**zoom

    @classmethod
    def tile_bounds(cls, zoom: int, x: int, y: int, buffer_pixels: int = 0) -> Bounds:
        cls.validate_tile(zoom, x, y)
        if not 0 <= buffer_pixels <= cls.TILE_SIZE:
            raise ValueError("tile buffer must be between 0 and the tile size")
        scale = 1 << zoom
        buffer_world = buffer_pixels / (cls.TILE_SIZE * scale)
        west, north = cls.world_to_lon_lat(x / scale - buffer_world, y / scale - buffer_world)
        east, south = cls.world_to_lon_lat((x + 1) / scale + buffer_world, (y + 1) / scale + buffer_world)
        return Bounds(west, south, east, north)

    @staticmethod
    def validate_tile(zoom: int, x: int, y: int) -> None:
        if not 0 <= zoom <= 18:
            raise ValueError("zoom must be between 0 and 18")
        limit = 1 << zoom
        if not 0 <= x < limit or not 0 <= y < limit:
            raise ValueError("tile coordinates are outside the requested zoom")
