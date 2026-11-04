# Kody

Kody is an offline-capable mapping product with independently deployable
vector-map, satellite-map, Shapes, Tracks, and ADS-B services.

For the supplied starter imagery, terrain, and Vector data, follow
[Getting started with starter map data](./GETTING_STARTED.md).

See [the workspace architecture](./ARCHITECTURE.md) for the ownership
boundaries and run each component from its local runbook:

![Kody progress screenshot](./progress/2026-10-21%20-%20001.png)

All map microservices implement, or are migrating to, the shared
[microservice API specification](./API_SPEC.md). New services must follow this
contract rather than add central-map service-specific interfaces.

Each service directory also contains an `API.md` endpoint inventory; the
[service endpoint index](./API_SPEC.md#service-endpoint-inventories) links to
all of them.

## Offline installation

The bundled [`wheels`](./wheels/) directory contains the complete Windows
CPython 3.15 dependency wheelhouse for Kody and its services. Follow
[`command_lines.txt`](./command_lines.txt) from the repository root to create
the virtual environment and install dependencies without contacting PyPI.


- [`kody`](./kody/README.md) — central browser UI and configuration service
- [`services/vecmap`](./services/vecmap/README.md) — offline vector map API, data, and package tooling
- [`services/satmap`](./services/satmap/README.md) — offline satellite imagery API and tiles
- [`services/copernicus`](./services/copernicus/README.md) — on-demand Copernicus Global Mosaic imagery API and tiles
- [`services/shapes`](./services/shapes/README.md) — optional static Shapes GeoJSON service
- [`services/shape-server`](./services/shape-server/README.md) — in-memory point-containment shapes and output events
- [`services/shape-server-test`](./services/shape-server-test/README.md) — sample ShapeServer producer
- [`services/interaction-server`](./services/interaction-server/README.md) — transient ShapeServer interaction layer
- [`services/tracks`](./services/tracks/README.md) — optional live Tracks GeoJSON service

The central product starts on port 8000, configuration on 8001, Shapes on
8002, Tracks on 8003, Vector map on 8004, ADS-B on 8005, Satellite map on
8006, ShapeServer on 8015, ShapeServerTest on 8016, and Copernicus Global
Mosaic on 8011, and InteractionServer on 8017.

## Start everything

After installing the requirements, start the standalone application and every
enabled component with either of these equivalent commands from the repository
root:

```powershell
.\.venv\Scripts\python.exe .\main.py
# Or, double-click Kody.cmd in Windows Explorer.
```

[`main.py`](./main.py) is the field-deployment entrypoint. It starts the
supervisor in [`start_kody.py`](./start_kody.py), which launches every enabled
service in the complete runtime catalog. [`Kody.cmd`](./Kody.cmd) uses the
offline-installed `.venv` automatically and keeps failures visible in its
console window. The runtime catalog, including modules, working directories,
ports, enabled flags, host, and log retention, is stored in
[`config/kody.json`](./config/kody.json). View the enabled launch plan without
starting it with `.\.venv\Scripts\python.exe .\main.py --list`.
The configuration API also exposes those settings at
`GET`/`PUT /api/config/runtime`; changes apply to the next launcher run.

Each service's standard output and error stream is stored beneath
`logs\<service-id>\service.log`. Logs rotate each minute and retain the latest
30 files by default, limiting retained output to roughly the last 30 minutes.
