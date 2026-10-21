# Kody starter data

Copy this folder to the configured Kody data location for a small baseline
library. Its `imports` directory contains source files intended for the
appropriate service import workflow:

- `MercatorWorld.png` is the global Web Mercator raster source.
- `natural-earth-10m-countries.geojson` is the recommended higher-resolution
  world country boundary source for Vector Map imports. It is approximately
  12 MB and based on Natural Earth 1:10m Admin 0 Countries. Each feature has
  a baked-in `fill` colour so country shading persists through later imports.
- `natural-earth-110m-countries.geojson` is the smaller 1:110m alternative
  for quick, low-resolution vector imports.
- `symbols\symbols.json` is the Symbols service catalogue. It defines each
  symbol's string name, SVG file, orientation, and colour mode; the referenced
  SVG files are stored alongside it.

The country boundaries are derived from Natural Earth Admin 0 Countries.
The 1:10m GeoJSON is distributed by DataHub at
<https://datahub.io/core/geo-countries>. Natural Earth data is public domain. See
<https://www.naturalearthdata.com/about/terms-of-use/>.
