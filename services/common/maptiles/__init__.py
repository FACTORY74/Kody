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

"""Shared SQLite/WebP Web-Mercator tile handling for Kody map services."""

from .store import (
    WebPQuadTreeStore,
    crop_parent_tile,
    standard_tile_response,
    validate_tile_coordinates,
)

__all__ = [
    "WebPQuadTreeStore",
    "crop_parent_tile",
    "standard_tile_response",
    "validate_tile_coordinates",
]
