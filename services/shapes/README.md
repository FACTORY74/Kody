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
service's existing optional-concurrency behaviour. The manifest advertises the
GeoJSON endpoint as a `dataLayer`, and exposes the trusted **Shapes** catalog
workspace at `GET /api/workspace`; its catalog inspector uses the same
revision-safe definition APIs.

The workspace can hand a draft to the trusted map editor without persisting it:
drag cyan controls for its centre/origin, dimensions, radii, angles, or
polygon vertices, then press Escape to return to the inspector and save.
Published shape features are selectable GeoJSON polygons; use Shift-click or
Shift-drag and choose **Edit on map** from the Selection panel to edit a draft.
Right-clicking a selectable polygon or edge exposes the same declared feature
actions in the map context menu.
Escaping that Selection-panel edit saves it through the revision-safe update
API; a save failure remains visible on the map so it can be retried.

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

## REST API

See the complete endpoint inventory in [API.md](./API.md).
