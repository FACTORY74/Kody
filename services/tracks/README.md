# Kody Tracks service

This optional service owns a persistent, route-based aircraft simulation. It
has no dependency on the map service or central product source trees.

```powershell
Set-Location services\tracks
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\src"
python -m uvicorn kody_tracks.track_service:app --host 127.0.0.1 --port 8003
```

Register `http://127.0.0.1:8003/` as an optional **GeoJSON** connection.

## Scenario data and API

The first startup writes two declarative catalogues beneath the configured
`KODY_DATA_DIRECTORY` (or `services\tracks\data` by default):

- `tracks\aircraft-types.json` defines reusable aircraft limits and symbols.
- `tracks\scenario.json` defines routes, straight-line legs, aircraft, and
  completion policies.

`GET` and `PUT /api/tracks/aircraft-types` expose the aircraft-type catalogue.
`GET` and `PUT /api/tracks/scenario` expose the scenario catalogue. Both
responses contain an opaque `revision`; clients may pass it in `If-Match` on
updates to prevent overwriting newer data. The service validates route/type
references and all initial/target speed and altitude values against the
assigned aircraft type before it persists a scenario.

The GeoJSON layer publishes routes, selectable waypoints, live selectable
aircraft, and rolling trails. Routes support `despawn`, `restart`, `loop`,
`hold`, and `reverse` completion policies. Speed and altitude move toward each
leg target using the type's acceleration, deceleration, climb, and descent
limits.

The manifest publishes the standard `scenarioEditor` capability. Kody's map
uses it to draw and save routes, edit route names, colors, completion policies,
and waypoint targets, assign aircraft, and move a waypoint only in an explicit
drag mode. Normal map pans never alter a route. Heading/turn-rate-limited
navigation remains a later phase described in
[`TRACK_EDIT_DESIGN.md`](../../TRACK_EDIT_DESIGN.md).

## Phase 3 simulation

The service maintains a deterministic simulation clock that advances only
while running. `GET` and `PUT /api/tracks/simulation` expose its running state
and control revision; `POST /api/tracks/simulation/reset` returns every
aircraft to its declared initial state. The map editor exposes Pause/Resume and
Reset controls through the `simulationControls` capability.

Aircraft heading is constrained by `maximumTurnRateDegreesPerSecond`. Every
leg reaches its exact waypoint before the active leg changes; the following
leg then starts with the aircraft's inbound heading and turns at the permitted
rate. This produces a turn circle after each waypoint rather than an
early-cut fly-by. Aircraft output includes actual and desired bearings, leg
progress, distance, and estimated time to the active waypoint.
