# Kody architecture

Kody is organized as independently runnable source trees. The central product
does not import service code or own service data.

```text
Kody core
├─ Map tile compositor
├─ Data-feature renderer
├─ Symbol registry
└─ Trusted workspace shell

Independent services
├─ tileLayer: sparse Web-Mercator quadtree tiles
├─ dataLayer: GeoJSON drawing data and refresh metadata
├─ symbolCatalog: named reusable SVG symbols
└─ workspace: operator controls backed by service APIs
```

The central configuration document owns the runtime catalog for every Kody
process, including each module, working directory, port, enabled state, host,
and rolling-log retention. [`start_kody.py`](./start_kody.py) launches all
enabled entries without importing their source code, so service ownership and
data boundaries remain independent.

## Extension capabilities

A service may declare any combination of these independent manifest
capabilities:

| Capability | Meaning | Display surface |
|---|---|---|
| `tileLayer` | Sparse quadtree tiles rendered by the map compositor. The service owns tile creation and storage; the core owns requesting, caching, ordering, opacity, and presentation. | Map |
| `dataLayer` | JSON drawing data, normally GeoJSON. Static, polling, and streaming data all use this capability; “live” is refresh metadata, not a category. | Data |
| `symbolCatalog` | Named SVG definitions used by map data and trusted application controls. | Symbols |
| `workspace` | A trusted operator layout that invokes additional REST endpoints on its declaring service. It is not a map layer. | Workspaces |

`tileLayer` standardizes the Web-Mercator `{z}/{x}/{y}` request and sparse
tile semantics. It does not require a shared internal database implementation:
a service may generate, cache, or read tiles however its domain requires.

A workspace can extend a tile or data service, but it does not own that
service’s visibility or opacity. For example, Terrain declares `tileLayer`
and `workspace`; TrackSim declares `dataLayer` and `workspace`
(`TrackSimControl`). SimTimeControl is a workspace-only service. Their
workspaces call service endpoints for configuration, analysis, persistence,
jobs, and results. The core renders only its trusted UI vocabulary and map
interaction modes.

TrackSimControl depends on SimTimeControl for progression: the Tracks service
reads SimTimeControl's elapsed seconds server-to-server, advances only when
that value increases, and rewinds its non-persisted flight runtime when the
shared clock resets.

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
