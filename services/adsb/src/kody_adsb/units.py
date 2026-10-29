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

"""Distance and speed unit conversions at external ADS-B provider boundaries."""

from __future__ import annotations

KILOMETRES_PER_NAUTICAL_MILE = 1.852
METRES_PER_KILOMETRE = 1_000.0
METRES_PER_SECOND_PER_KNOT = 0.514444


def kilometres_to_nautical_miles(kilometres: float) -> float:
    """Convert Kody's kilometre distances for ADSB.lol point queries."""

    return kilometres / KILOMETRES_PER_NAUTICAL_MILE


def nautical_miles_to_kilometres(nautical_miles: float) -> float:
    """Convert provider nautical-mile distances into Kody kilometres."""

    return nautical_miles * KILOMETRES_PER_NAUTICAL_MILE


def kilometres_to_metres(kilometres: float) -> float:
    """Convert a Kody kilometre distance to metres."""

    return kilometres * METRES_PER_KILOMETRE


def knots_to_metres_per_second(knots: float) -> float:
    """Convert ADS-B ground speed from knots for local extrapolation."""

    return knots * METRES_PER_SECOND_PER_KNOT
