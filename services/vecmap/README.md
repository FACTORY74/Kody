# Kody vector map service

This tree owns the offline vector map API and its GeoPackage-backed Vector
working library.

```powershell
Set-Location services\vecmap
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\src"
python -m uvicorn kody_vecmap.server:app --host 127.0.0.1 --port 8004
```

Register `http://127.0.0.1:8004/` as the **Vector map** connection in the
central product. Build/manage local packages with:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m kody_vecmap.map_manager list
```

`imports/` contains the immutable master copies of downloaded or supplied
sources. Kody never deletes these files during a working-library rebuild.

All generated Vector working data is stored in one GeoPackage:
`maps/vector-library.gpkg`. It stores binary GeoPackage geometries and an
SQLite RTree spatial index. The map service queries that index for each
display tile; it does not create a large file hierarchy of pre-rendered
GeoJSON tiles.

Each completed source update is committed as one SQLite transaction. A failed
or cancelled import cannot expose a partially written set of Vector features.
Overlapping imports merge their features and imports do not retain a separate
identity.
Satellite imagery is independently owned by
[`services/satmap`](../satmap/README.md).

The same dialog provides independent switches for the imported major
roads, urban areas, woodland, and placename layers.

Place `.osm.pbf`, `.gpkg`, and `.gpkg.zip` map sources in `imports/`, then use
**Import all Vector sources** to merge every map source into
`vector-library.gpkg`. Keep `land-polygons-split-4326.zip` in that folder as
the shared land-fill source; it is not itself a map import.

For a validated regional base-map source, import a Geofabrik `free.gpkg` or
`free.gpkg.zip` extract. Kody reads its water polygons, woodland, major roads,
and populated places and combines them with the OSM land-polygon archive in
the Vector `imports` directory, then writes binary geometry to the working
GeoPackage:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m kody_vecmap.map_manager install-geofabrik-gpkg `
  --id wales-geofabrik `
  --source C:\path\to\wales-latest-free.gpkg.zip `
  --replace
```

The imported Vector data remains subject to OpenStreetMap's ODbL attribution.
