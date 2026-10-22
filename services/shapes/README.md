# Kody Shapes service

This optional service owns an atomically persisted declarative shape catalogue
at `data/shapes/shapes.json`. `GET /api/shapes` derives a GeoJSON
`FeatureCollection`, so existing overlay clients remain compatible.

Definitions are named and coloured (`#RRGGBB` or `#RRGGBBAA`) `circle`,
`rectangle`, `annularSector`, or `polygon` objects. Circle and sector radii,
and rectangle width/height, are metres. Coordinates are `[longitude, latitude]`;
a rectangle's `origin` is its centre and `angle` is clockwise degrees from
north. Sectors use clockwise bearings from north.

`GET /api/shapes/definitions` returns the catalogue and its revision.
`POST /api/shapes/definitions`, `PUT /api/shapes/definitions/{id}`, and
`DELETE /api/shapes/definitions/{id}` edit it and return the new revision.
Supply the returned revision in
an `If-Match` header to reject stale edits with `409`; omitting it preserves the
service's existing optional-concurrency behaviour. The manifest advertises
these endpoints as a `shapeEditor` capability.

On first run, the service writes the new catalogue atomically. If the new file
does not exist, valid polygons from the legacy `data/shapes/shapes.geojson` are
migrated; the legacy file is retained. Non-polygon legacy GeoJSON cannot be
represented by the declarative catalogue and is left untouched.

```powershell
Set-Location services\shapes
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\src"
python -m uvicorn kody_shapes.shapes_service:app --host 127.0.0.1 --port 8002
```

Register `http://127.0.0.1:8002/` as an optional **Shapes** connection. The
central product continues working if this service is absent.
