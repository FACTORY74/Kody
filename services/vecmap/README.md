# Kody Natural Earth vector map service

This service imports and displays Natural Earth 1:10m physical and cultural
layers. By default it downloads the two public archive URLs configured in the
Vector map service settings, caching validated ZIPs beneath the service data
directory before importing. A local `Natural Earth archive folder` remains
available as an offline fallback when both archive URLs are blank.

```powershell
Set-Location services\vecmap
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\src"
python -m uvicorn kody_vecmap.server:app --host 127.0.0.1 --port 8004
```

Register `http://127.0.0.1:8004/` as the **Vector map** connection in the
central product, then run **Import Natural Earth dataset**. The import
downloads each configured archive over HTTPS to
`<dataDirectory>\vecmap\imports\natural-earth`, validates the nested
shapefiles, and imports every complete `ne_10m_*` layer. Downloaded archives
replace the cache atomically, so a failed download cannot replace the
previously validated local copy.

Features are streamed directly from the archives into
`maps\vector-library.gpkg`. The GeoPackage stores binary geometries with an
SQLite RTree index. Reimporting replaces the previous Natural Earth feature set
in one transaction. Display lossless WebP tiles for selected zooms (by default
`0, 2, 4, 6, 8, 10, 12`) are rendered only when viewed,
then saved in a versioned persistent cache. A cold tile receives `202 Accepted`
while a single service worker queues and renders it; the browser retains the
nearest cached parent tile and retries shortly afterward. The Vector map
configuration dialog reports queued and rendering tile progress. Revisiting a
location is a compact image read rather than another geometry query and draw
operation; unseen areas never consume rendering time or cache space. Zooms
above 8 use their smaller tile bounds with the prepared zoom-8 cartographic
geometry, avoiding a costly exact-geometry path. Zooming beyond the highest
configured level smoothly scales the regional Natural Earth cartography; the
source does not claim street-level coverage.

Layer switches group the source layers into physical features, coastlines,
hydrography, marine features, bathymetry, ice, geographic features, graticules,
administrative boundaries, places, transport, protected areas, time zones, and
other data. Reference-heavy categories such as marine boundaries, bathymetry,
transport, time zones, and miscellaneous source layers are imported but hidden
by default for a clean map. Cache revisions include the enabled layers, source
data, and `config\vecmap-theme.json`, so changes automatically create new
on-demand tiles without serving stale ones. The paint theme is maintained in
`config\vecmap-theme.json`.

When bathymetry is enabled, Natural Earth's 0 m through 10,000 m source bands
are painted in progressively darker blues. Existing imports retain their source
layer name, so this depth styling also applies without a reimport.

Natural Earth data is public domain. Satellite imagery and terrain are
independently owned by [`services/satmap`](../satmap/README.md) and
[`services/terrain`](../terrain/README.md).
