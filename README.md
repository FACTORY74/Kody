# Kody

Kody is an offline-capable mapping product with independently deployable
vector-map, satellite-map, Shapes, Tracks, and ADS-B services.

For the supplied starter imagery, terrain, and Vector data, follow
[Getting started with starter map data](./GETTING_STARTED.md).

See [the workspace architecture](./ARCHITECTURE.md) for the ownership
boundaries and run each component from its local runbook:

- [`kody`](./kody/README.md) — central browser UI and configuration service
- [`services/vecmap`](./services/vecmap/README.md) — offline vector map API, data, and package tooling
- [`services/satmap`](./services/satmap/README.md) — offline satellite imagery API and tiles
- [`services/shapes`](./services/shapes/README.md) — optional static Shapes GeoJSON service
- [`services/tracks`](./services/tracks/README.md) — optional live Tracks GeoJSON service

The central product starts on port 8000, configuration on 8001, Shapes on
8002, Tracks on 8003, Vector map on 8004, ADS-B on 8005, and Satellite map on
8006.

## Start everything

After installing the requirements from each service and product tree into the
same Python environment, start every enabled component with one command from
the repository root:

```powershell
python .\start_kody.py
```

The complete runtime catalog, including modules, working directories, ports,
enabled flags, host, and log retention, is stored in
[`config/kody.json`](./config/kody.json). View the
enabled launch plan without starting it with `python .\start_kody.py --list`.
The configuration API also exposes those settings at
`GET`/`PUT /api/config/runtime`; changes apply to the next launcher run.

Each service's standard output and error stream is stored beneath
`logs\<service-id>\service.log`. Logs rotate each minute and retain the latest
30 files by default, limiting retained output to roughly the last 30 minutes.
