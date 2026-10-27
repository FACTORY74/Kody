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

Opening TrackSimControl reloads both catalogues from disk. This makes
external edits available without restarting the service. If either catalogue
changed, the service validates the pair and resets the live simulation to its
initial state.

The GeoJSON layer publishes routes, selectable waypoints, live selectable
aircraft, and rolling trails. Routes support `despawn`, `restart`, `loop`,
`hold`, and `reverse` completion policies. Speed and altitude move toward each
leg target using the type's acceleration, deceleration, climb, and descent
limits.

The manifest publishes the standard `scenarioEditor` capability. Kody's map
uses it to draw and save routes, edit route names, colors, completion policies,
and waypoint targets, assign aircraft, and move a waypoint only in an explicit
drag mode. Normal map pans never alter a route. Heading/turn-rate-limited
navigation is described in the [`TrackSimControl workspace`](../../workspaces/tracks/README.md)
and the [common workspace design](../../workspaces/README.md).

## Shared simulation time

TrackSimControl depends on SimTimeControl for its authoritative elapsed time.
The Tracks service reads `GET /api/sim-time` from the configured
`KODY_SIM_TIME_SERVICE_URL` (default `http://127.0.0.1:8012`) whenever it
updates published data or opens its workspace. TrackSim advances only while
that shared elapsed time increases. If SimTimeControl resets to an earlier
elapsed value, TrackSim resets every aircraft to its route's initial state.

The former Tracks-local `PUT /api/tracks/simulation` and
`POST /api/tracks/simulation/reset` endpoints now return `409`; consumers
must use SimTimeControl's lifecycle controls instead.

Aircraft heading is constrained by `maximumTurnRateDegreesPerSecond`. Every
leg reaches its exact waypoint before the active leg changes; the following
leg then starts with the aircraft's inbound heading and turns at the permitted
rate. This produces a turn circle after each waypoint rather than an
early-cut fly-by. Aircraft output includes actual and desired bearings, leg
progress, distance, and estimated time to the active waypoint.

## TrackSimControl workspace

The manifest publishes the `TrackSimControl` `workspace` capability at
`GET /api/workspace`.
It supplies structured, text-only pane summaries for scenarios, aircraft
types, routes, aircraft, and timeline state. Kody renders those summaries with
its generic workspace cards and inspector, while this service remains
authoritative for its data and simulation behavior.

The workspace timeline reports progress driven by SimTimeControl. TrackSim's
manifest declares this server-to-server dependency so operators configure the
clock through the SimTimeControl workspace, not TrackSimControl.
Kody interpolates each live aircraft position between consecutive TrackSim
updates so movement remains smooth at display-frame cadence.

Aircraft types, routes, and aircraft are managed directly in their workspace
panes. Each has Add, Edit, and guarded Remove controls; edits use the
workspace inspector and retain the service's revision checks. Editing a route
on the map closes the workspace only for that focused task: every cyan
waypoint handle is draggable, each drop persists immediately, and Escape
returns to the Routes workspace pane rather than reopening the former
scenario dialog. Route editors also provide Add waypoint and Remove controls;
every route keeps at least two waypoints.
