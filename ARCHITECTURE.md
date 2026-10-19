# Kody workspace architecture

Kody is organized as independently runnable source trees. The central product
does not import service code or own service data.

```text
kody/                Central browser UI and configuration service
services/vecmap/     Offline vector map API, map packages, and vector data
services/satmap/     Offline satellite imagery API and raster tile data
services/shapes/     Static named-colour GeoJSON Shapes API and source data
services/tracks/     Live GeoJSON Tracks API and its simulation source
services/adsb/       Live ADSB.lol aircraft GeoJSON API and local track cache
```

The central configuration document owns the runtime catalog for every Kody
process, including each module, working directory, port, enabled state, host,
and rolling-log retention. [`start_kody.py`](./start_kody.py) launches all
enabled entries without importing their source code, so service ownership and
data boundaries remain independent. Start the central product on ports 8000
and 8001. Start `vecmap` on 8004 and `satmap` on 8006, then register their
independent `vecmap` and `satmap` connections. Shapes and Tracks are optional
`shapes` and `geojson` connections respectively. The central product polls
optional overlays independently; a stopped, removed, or unreachable overlay
does not prevent the map or other overlays from running.

Each tree contains its own `requirements.txt`, `src/`, tests, and local
runbook. Install its requirements and set `PYTHONPATH` to that tree's `src`
directory before starting it.

## Shared map context

The central configuration service exposes the current browser viewport through
`GET /api/context/viewport`. The map publishes `{west, south, east, north,
zoom}` with `PUT /api/context/viewport` after camera changes. This is
transient shared context, not persisted user configuration: microservices may
read it to reduce data to the current view without taking a dependency on a
browser-specific request parameter.
