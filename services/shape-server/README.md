# Kody ShapeServer

ShapeServer holds named circles, rectangles, annular sectors, and polygons in
memory, accepts point events, and sends a JSON `POST` to a configured output
URL for every containing shape. Restarting ShapeServer clears all shapes,
queued events, and match history; only the service's output and queue settings
are persisted in `services/shape-server/config/shape-server.json`.

Create a shape with `POST /api/shapes`, update it with
`PUT /api/shapes/{id}`, and remove it with `DELETE /api/shapes/{id}`. Every
shape response includes a calculated latitude/longitude bounding box and a
GeoJSON `Feature`; `GET /api/shapes/geojson` returns all shapes as a GeoJSON
FeatureCollection. Shape coordinates are `[longitude, latitude]`, distances
and dimensions are metres, and angles are clockwise degrees from north.
Omit `color` to select a palette colour.

Submit a point check with:

```json
POST /api/events
{
  "eventId": "position-42",
  "longitude": -3.3808,
  "latitude": 53.1348,
  "timestamp": "2026-10-29T16:00:00Z",
  "properties": {"source": "example"}
}
```

`POST /api/events` returns `202 Accepted`; `GET /api/events` exposes recent
matches and callback delivery results. The service tests each shape's bounding
box before executing its type-specific containment check. Events over the
configured capacity receive `429` and are not discarded silently.
The pure shape definitions, bounds calculations, GeoJSON-ring generation, and
exact containment checks are isolated in
[`shape_intersections.py`](./src/kody_shape_server/shape_intersections.py).

Connect `http://127.0.0.1:8015/` as a Shapes service in Kody, then use its
configuration dialog to set the output callback URL, processing rate, and
queue capacity. The repository's default Kody configuration already connects
and enables ShapeServer as the `local-shape-server` data layer. Kody requests
each enabled GeoJSON layer every second; ShapeServer increments the collection
revision on every create, update, delete, or clear, so the map redraws the
current shapes after the next refresh.

When configured with its default TrackSim source, ShapeServer polls TrackSim
every 100 ms (10 Hz) and submits each track position through the same bounded
event queue. This improves detection for brief track/shape crossings while
the configured processing-rate and queue-capacity limits continue to protect
the service. A repeated containment callback for the same track/shape pair
updates the InteractionLayer circle to the newer point and renews its
one-second lifetime.

```powershell
Set-Location services\shape-server
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\src"
python -m uvicorn kody_shape_server.shape_server:app --host 127.0.0.1 --port 8015
```

## REST API

See the complete endpoint inventory in [API.md](./API.md).
