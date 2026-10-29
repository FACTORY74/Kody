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

"""Typed shape definitions and pure geographic containment calculations.

This module deliberately has no HTTP, queue, storage, or callback dependency.
It converts supported shapes to polygon rings, calculates cached geographic
bounds, and performs the exact point-in-shape test used by ShapeServer.
"""

from __future__ import annotations

import math
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

EARTH_RADIUS_METRES = 6_371_008.8
ARC_SEGMENTS = 64
COLOUR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$")


def validate_coordinate(value: list[float]) -> list[float]:
    """Validate a GeoJSON-order ``[longitude, latitude]`` coordinate."""
    if (
        len(value) != 2
        or any(not isinstance(item, (int, float)) or isinstance(item, bool) for item in value)
        or not all(math.isfinite(item) for item in value)
        or not -180 <= value[0] <= 180
        or not -90 <= value[1] <= 90
    ):
        raise ValueError("coordinates must be [longitude, latitude] within valid bounds")
    return value


Coordinate = Annotated[list[float], Field(min_length=2, max_length=2)]


class ShapeBase(BaseModel):
    """Common validated properties for every supported shape."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    color: str | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        """Reject blank names after removing surrounding whitespace."""
        if not value.strip():
            raise ValueError("name must not be blank")
        return value.strip()

    @field_validator("color")
    @classmethod
    def validate_color(cls, value: str | None) -> str | None:
        """Accept six- or eight-digit hexadecimal display colours."""
        if value is not None and not COLOUR_PATTERN.fullmatch(value):
            raise ValueError("color must be a hexadecimal #RRGGBB or #RRGGBBAA value")
        return value


class CircleShape(ShapeBase):
    """A great-circle-radius shape centered on one coordinate."""

    type: Literal["circle"]
    center: Coordinate
    radius: float = Field(gt=0, le=20_000_000)

    _validate_center = field_validator("center")(validate_coordinate)


class RectangleShape(ShapeBase):
    """A rectangle defined by center origin, dimensions, and clockwise angle."""

    type: Literal["rectangle"]
    origin: Coordinate
    width: float = Field(gt=0, le=40_000_000)
    height: float = Field(gt=0, le=40_000_000)
    angle: float = Field(ge=-360, le=360)

    _validate_origin = field_validator("origin")(validate_coordinate)


class AnnularSectorShape(ShapeBase):
    """A radial sector between inner and outer great-circle distances."""

    type: Literal["annularSector"]
    center: Coordinate
    innerRadius: float = Field(ge=0, le=20_000_000)
    outerRadius: float = Field(gt=0, le=20_000_000)
    startAngle: float = Field(ge=-360, le=360)
    endAngle: float = Field(ge=-360, le=360)

    _validate_center = field_validator("center")(validate_coordinate)

    @field_validator("outerRadius")
    @classmethod
    def validate_outer_radius(cls, value: float, info: Any) -> float:
        """Require a non-empty radial band."""
        if value <= info.data.get("innerRadius", -1):
            raise ValueError("outerRadius must be greater than innerRadius")
        return value


class PolygonShape(ShapeBase):
    """A simple polygon supplied as unclosed GeoJSON-order coordinates."""

    type: Literal["polygon"]
    coordinates: list[Coordinate] = Field(min_length=3, max_length=10_000)

    @field_validator("coordinates")
    @classmethod
    def validate_polygon(cls, value: list[list[float]]) -> list[list[float]]:
        """Require at least three distinct polygon positions."""
        if len({(longitude, latitude) for longitude, latitude in value}) < 3:
            raise ValueError("polygon coordinates must contain at least three distinct positions")
        return value


Shape = Annotated[
    CircleShape | RectangleShape | AnnularSectorShape | PolygonShape,
    Field(discriminator="type"),
]


class Bounds(BaseModel):
    """Axis-aligned geographic bounds cached for fast containment rejection."""

    west: float
    south: float
    east: float
    north: float

    def contains(self, longitude: float, latitude: float) -> bool:
        """Return whether a point is within these inclusive bounds."""
        return self.west <= longitude <= self.east and self.south <= latitude <= self.north


def destination(center: list[float], bearing: float, distance: float) -> list[float]:
    """Return the longitude/latitude reached from ``center`` on a great-circle path."""
    longitude, latitude = map(math.radians, center)
    angular_distance = distance / EARTH_RADIUS_METRES
    angle = math.radians(bearing)
    destination_latitude = math.asin(
        math.sin(latitude) * math.cos(angular_distance)
        + math.cos(latitude) * math.sin(angular_distance) * math.cos(angle)
    )
    destination_longitude = longitude + math.atan2(
        math.sin(angle) * math.sin(angular_distance) * math.cos(latitude),
        math.cos(angular_distance) - math.sin(latitude) * math.sin(destination_latitude),
    )
    return [((math.degrees(destination_longitude) + 540) % 360) - 180, math.degrees(destination_latitude)]


def haversine_metres(first: list[float], second: list[float]) -> float:
    """Return the great-circle distance between two longitude/latitude points."""
    longitude_delta = math.radians(second[0] - first[0])
    latitude_delta = math.radians(second[1] - first[1])
    latitude_first = math.radians(first[1])
    latitude_second = math.radians(second[1])
    value = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(latitude_first) * math.cos(latitude_second) * math.sin(longitude_delta / 2) ** 2
    )
    return EARTH_RADIUS_METRES * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def bearing_degrees(first: list[float], second: list[float]) -> float:
    """Return the initial great-circle bearing from ``first`` to ``second``."""
    longitude_delta = math.radians(second[0] - first[0])
    latitude_first = math.radians(first[1])
    latitude_second = math.radians(second[1])
    return math.degrees(math.atan2(
        math.sin(longitude_delta) * math.cos(latitude_second),
        math.cos(latitude_first) * math.sin(latitude_second)
        - math.sin(latitude_first) * math.cos(latitude_second) * math.cos(longitude_delta),
    )) % 360


def shape_ring(shape: Shape) -> list[list[float]]:
    """Approximate a shape as a closed GeoJSON polygon ring."""
    if isinstance(shape, CircleShape):
        # Sample the circumference at equal bearing intervals.
        return [destination(shape.center, angle, shape.radius) for angle in range(0, 361, 360 // ARC_SEGMENTS)]
    if isinstance(shape, RectangleShape):
        corners: list[list[float]] = []
        angle = math.radians(shape.angle)
        for east, north in (
            (-shape.width / 2, -shape.height / 2),
            (shape.width / 2, -shape.height / 2),
            (shape.width / 2, shape.height / 2),
            (-shape.width / 2, shape.height / 2),
        ):
            rotated_east = east * math.cos(angle) + north * math.sin(angle)
            rotated_north = -east * math.sin(angle) + north * math.cos(angle)
            corners.append(destination(shape.origin, math.degrees(math.atan2(rotated_east, rotated_north)), math.hypot(rotated_east, rotated_north)))
        # GeoJSON polygon rings must close at their first coordinate.
        return corners + [corners[0]]
    if isinstance(shape, AnnularSectorShape):
        # Trace the outer arc forward and the inner arc backward to form a ring.
        sweep = (shape.endAngle - shape.startAngle) % 360 or 360
        angles = [shape.startAngle + sweep * index / ARC_SEGMENTS for index in range(ARC_SEGMENTS + 1)]
        outer = [destination(shape.center, angle, shape.outerRadius) for angle in angles]
        inner = [destination(shape.center, angle, shape.innerRadius) for angle in reversed(angles)]
        return outer + inner + [outer[0]]
    return shape.coordinates + [shape.coordinates[0]]


def calculate_bounds(shape: Shape) -> Bounds:
    """Calculate the axis-aligned longitude/latitude bounds for a shape."""
    ring = shape_ring(shape)
    longitudes, latitudes = zip(*ring)
    return Bounds(west=min(longitudes), south=min(latitudes), east=max(longitudes), north=max(latitudes))


def point_in_polygon(point: list[float], ring: list[list[float]]) -> bool:
    """Determine containment in a closed polygon with the ray-casting method."""
    longitude, latitude = point
    inside = False
    for current, following in zip(ring, ring[1:]):
        current_longitude, current_latitude = current
        following_longitude, following_latitude = following
        if ((current_latitude > latitude) != (following_latitude > latitude)) and (
            longitude < (following_longitude - current_longitude) * (latitude - current_latitude)
            / (following_latitude - current_latitude) + current_longitude
        ):
            inside = not inside
    return inside


def contains(shape: Shape, point: list[float]) -> bool:
    """Apply the exact containment test appropriate for ``shape``."""
    if isinstance(shape, CircleShape):
        return haversine_metres(shape.center, point) <= shape.radius
    if isinstance(shape, AnnularSectorShape):
        distance = haversine_metres(shape.center, point)
        # Reject the radial component before calculating the angular component.
        if not shape.innerRadius <= distance <= shape.outerRadius:
            return False
        sweep = (shape.endAngle - shape.startAngle) % 360 or 360
        return (bearing_degrees(shape.center, point) - shape.startAngle) % 360 <= sweep
    return point_in_polygon(point, shape_ring(shape))
