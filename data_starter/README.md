# Kody starter data

Copy this folder to the configured Kody data location for a small baseline
library. Its `imports` directory contains source files intended for the
appropriate service import workflow:

- `MercatorWorld.png` is the global Web Mercator raster source.
- `natural-earth-110m-countries.geojson` is a low-resolution world country
  boundary source for basic vector imports.
- `symbols\symbols.json` is the Symbols service catalogue. It defines each
  symbol's string name, SVG file, orientation, and colour mode; the referenced
  SVG files are stored alongside it.

The country boundaries are derived from Natural Earth 1:110m Admin 0 Countries.
Natural Earth data is public domain. See
<https://www.naturalearthdata.com/about/terms-of-use/>.
