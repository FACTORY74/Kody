# Kody Static OSM Tile Service

This service serves prebuilt Kody-style WebP tiles directly from:

```text
tiles\<zoom>\<x>\<y>.webp
```

It deliberately performs no runtime map rendering. Missing tiles return HTTP
404. Static files are returned with immutable one-year cache headers.

The complete initial style configuration is
[`config/kody-style.json`](./config/kody-style.json). It defines the Kody
operational palette, layer visibility, label hierarchy, WebP quality 100, and
four-by-four metatile rendering requirements for the future offline builder.

Put `.osm.pbf`, `.gpkg`, or `.gpkg.zip` sources in `imports/`. The service
catalogues those inputs at `GET /api/sources`; it intentionally excludes the
`land-polygons-split-4326.zip` support archive. Configuration at `/api/config`
records a source, zoom range, and refresh request for the forthcoming
offline-render build stage.

Run it independently with:

```powershell
Set-Location services\osmtiles
$env:PYTHONPATH = "$PWD\src"
python -m uvicorn kody_osmtiles.server:app --host 127.0.0.1 --port 8011
```

Or use the root launcher:

```powershell
python .\start_kody.py
```
