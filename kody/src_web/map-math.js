/*
 * Kody — General-Purpose Mapping Application
 *
 * A Python-based mapping application with a JavaScript/HTML web interface and a plugin architecture for extending functionality.
 *
 * Copyright (c) 2026 Factory74
 *
 * SPDX-License-Identifier: MIT
 *
 * Distributed under the MIT License. See LICENSE for details.
 * Provided "AS IS", without warranty of any kind.
 */

export class MapMath {
  static MAX_LATITUDE = 85.05112878;
  static TILE_SIZE = 256;

  static clampLatitude(latitude) {
    return Math.max(-this.MAX_LATITUDE, Math.min(this.MAX_LATITUDE, latitude));
  }

  static lonLatToWorld(longitude, latitude) {
    const safeLatitude = this.clampLatitude(latitude) * Math.PI / 180;
    return {
      x: (longitude + 180) / 360,
      y: (1 - Math.log(Math.tan(safeLatitude) + 1 / Math.cos(safeLatitude)) / Math.PI) / 2,
    };
  }

  static worldToLonLat(x, y) {
    return {
      longitude: x * 360 - 180,
      latitude: Math.atan(Math.sinh(Math.PI * (1 - 2 * y))) * 180 / Math.PI,
    };
  }

  static tileBounds(zoom, x, y) {
    this.validateTile(zoom, x, y);
    const scale = 2 ** zoom;
    const northwest = this.worldToLonLat(x / scale, y / scale);
    const southeast = this.worldToLonLat((x + 1) / scale, (y + 1) / scale);
    return {
      west: northwest.longitude,
      south: southeast.latitude,
      east: southeast.longitude,
      north: northwest.latitude,
    };
  }

  static validateTile(zoom, x, y) {
    if (!Number.isInteger(zoom) || zoom < 0 || zoom > 18) {
      throw new RangeError("zoom must be between 0 and 18");
    }
    const limit = 2 ** zoom;
    if (!Number.isInteger(x) || !Number.isInteger(y) || x < 0 || y < 0 || x >= limit || y >= limit) {
      throw new RangeError("tile coordinates are outside the requested zoom");
    }
  }
}
