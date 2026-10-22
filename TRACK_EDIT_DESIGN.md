# Track Editing, Simulation, and Workspace Design

## Purpose

The Tracks service is Kody's first rich platform-simulation plugin. It owns
aircraft types, scenarios, routes, simulation state, and aviation-specific
validation. The central map owns reusable map and workspace interaction
primitives.

The goal is a capable simulation-editing experience without making the core
application an aircraft simulator or allowing services to ship arbitrary
browser user interfaces.

## Current implementation

The Tracks service now provides:

- a persisted aircraft-type catalogue;
- a persisted scenario catalogue containing routes and aircraft instances;
- GeoJSON aircraft, trails, routes, and selectable waypoints;
- route drawing, editing, waypoint movement, and aircraft assignment through
  the initial scenario editor;
- deterministic simulation time with pause, resume, and reset;
- constrained speed, altitude, and heading changes;
- exact waypoint overflight followed by a rate-limited turn towards the next
  leg;
- leg progress, distance, ETA, desired bearing, and actual heading telemetry.

The initial scenario editor is intentionally a transitional implementation.
Its flat dialog proves the API and map interactions, but it is not sufficient
for editing a catalogue, scenario, routes, platforms, and a timeline together.

## Architecture and ownership

```text
Aircraft type catalogue      Scenario catalogue       Simulation runtime
          \                       |                       /
           +---------------- Tracks microservice --------+
           |  validation, persistence, physics, timing   |
           +---------- structured REST resources --------+
                                      |
                                      v
                         Core workspace framework
              navigation | editor panes | map tools | timeline
                                      |
                                      v
                              Kody central map
```

### Core application owns

- service discovery and capability negotiation;
- a docked or full-size workspace shell;
- tabs, panes, trees, property inspectors, editable tables, forms, and
  save/revert/conflict UX;
- generic route drawing, geometry previews, selection, and explicit map
  editing modes;
- a generic timeline component and simulation-control presentation;
- standard optimistic-concurrency handling using `If-Match`;
- safe rendering of structured service data and validation errors.

The core does **not** understand aircraft performance, route-completion
meaning, wind, fuel, terrain clearance, or aviation constraints.

### Tracks service owns

- aircraft type definitions and performance limits;
- scenario, route, waypoint, and aircraft data;
- schema and cross-reference validation;
- all persistence and atomic writes;
- route-completion policies;
- simulation clock, runtime state, and reset/pause behavior;
- turning, speed, altitude, and future navigation physics;
- domain-specific details, warnings, and derived telemetry.

Deleting the Tracks service and its data should remove aircraft simulation
behavior. The core workspace framework remains available for another service,
such as maritime, ground-unit, or satellite simulation.

## Why a workspace instead of a custom browser plugin?

The service must not return HTML, JavaScript, CSS, or an arbitrary component
tree. That would create an unbounded UI-plugin security, compatibility, and
maintenance problem.

Instead, it declares capabilities and structured resources. The core
recognizes generic editor patterns and renders them with trusted, reusable
components. This is analogous to a database client recognizing tables and
forms: the service supplies data and contracts; the client supplies the
interaction chrome.

This is **not** a separate UI plugin executable. It is a REST capability
implemented by an ordinary service plugin:

```text
Tracks service declares "workspace" capability
              |
              +-- exposes resource paths and schemas
              |
Kody core recognizes the capability
              |
              +-- opens its built-in workspace shell
              +-- fetches the declared resources
              +-- renders generic panes and map modes
```

The current `scenarioEditor` and `simulationControls` capabilities are the
first small examples of this direction. A future `workspace` capability
replaces their one-dialog presentation, while retaining their data endpoints
and revision semantics.

## Proposed workspace capability

The exact shared contract belongs in `API_SPEC.md` when implemented. The
intended shape is:

```json
{
  "capabilities": {
    "workspace": {
      "id": "platform-simulation",
      "label": "Simulation workspace",
      "openPath": "/api/workspace",
      "revisionHeader": "If-Match",
      "panes": ["scenarios", "aircraftTypes", "routes", "aircraft", "timeline"]
    }
  }
}
```

`GET /api/workspace` returns structured metadata and resource locations, not
presentation code:

```json
{
  "revision": "opaque",
  "title": "Tracks simulation",
  "resources": {
    "scenarios": "/api/tracks/scenarios",
    "aircraftTypes": "/api/tracks/aircraft-types",
    "routes": "/api/tracks/routes",
    "aircraft": "/api/tracks/aircraft",
    "simulation": "/api/tracks/simulation"
  },
  "permissions": {
    "createRoute": true,
    "moveWaypoint": true,
    "controlSimulation": true
  }
}
```

At first, the existing whole-scenario endpoint can remain the persistence
boundary. The service can later split it into focused resources to avoid
transferring and conflicting on a large document for a small edit:

```text
GET/PUT /api/tracks/scenarios/{id}
GET/PUT /api/tracks/routes/{id}
GET/PUT /api/tracks/aircraft/{id}
GET/PUT /api/tracks/aircraft-types/{id}
GET/PUT /api/tracks/simulation
POST    /api/tracks/simulation/reset
```

Every write validates server-side and uses revisions. The core must never
assume that a local form is authoritative.

## Tracks workspace layout

```text
+------------------------------------------------------------------+
| Scenario: [Coastal exercise v]               Save  Run  Pause    |
+----------------------+---------------------------+---------------+
| Navigator            | Primary editor            | Inspector     |
|                      |                           |               |
| Scenarios            | Route map / profile       | Selected item |
| Aircraft types       | Waypoint table            | Limits        |
| Routes               | Altitude / speed profile  | Live state    |
| Aircraft             |                           | Validation    |
+----------------------+---------------------------+---------------+
| Timeline: 00:12:43       [<] [Play/Pause] [>] [Reset]           |
+------------------------------------------------------------------+
```

### Navigator

- Scenario list and lifecycle.
- Aircraft-type catalogue.
- Routes.
- Aircraft instances.
- Later: events, triggers, formations, and environment data.

### Primary editor

The selected resource determines the central editor:

| Resource | Primary editor |
|---|---|
| Scenario | metadata, participating platforms, global settings |
| Aircraft type | performance envelope and symbol |
| Route | map geometry, legs, altitude/speed profile |
| Aircraft | type, route, initial state, live telemetry |
| Timeline | scheduled events and simulation time |

### Inspector

The inspector contains structured fields and validation feedback for the
currently selected route, leg, aircraft, or type. It should not be a duplicate
of the primary editor; it is a compact contextual detail view.

### Timeline

The timeline is valuable only once scenario-level timing exists. Start with
the deterministic clock, play/pause/reset, and speed controls. Add scheduled
spawns, route activation, scripted events, and conditional triggers only when
they are persisted service concepts.

## Data model

### Aircraft types

Aircraft performance is separate from aircraft instances:

```json
{
  "schemaVersion": 1,
  "types": [{
    "id": "light-turboprop",
    "displayName": "Light turboprop",
    "symbol": "plane",
    "limits": {
      "minimumSpeedMetresPerSecond": 55,
      "maximumSpeedMetresPerSecond": 150,
      "minimumAltitudeMetres": 0,
      "maximumAltitudeMetres": 9000,
      "maximumClimbRateMetresPerSecond": 12,
      "maximumDescentRateMetresPerSecond": 15,
      "maximumTurnRateDegreesPerSecond": 3.5,
      "maximumBankAngleDegrees": 30,
      "maximumAccelerationMetresPerSecondSquared": 2.5,
      "maximumDecelerationMetresPerSecondSquared": 3.5
    }
  }]
}
```

Future extensions can add fuel, payload, wind response, terrain constraints,
failures, and performance tables by altitude.

### Scenarios, routes, and aircraft

```json
{
  "schemaVersion": 1,
  "routes": [{
    "id": "coastal-patrol",
    "name": "Coastal patrol",
    "color": "#38c8ec",
    "completionPolicy": "loop",
    "legs": [{
      "id": "leg-a",
      "waypoint": [-4.42, 57.28],
      "targetAltitudeMetres": 1200,
      "targetSpeedMetresPerSecond": 95
    }]
  }],
  "aircraft": [{
    "id": "patrol-01",
    "name": "Patrol 01",
    "aircraftTypeId": "light-turboprop",
    "routeId": "coastal-patrol",
    "color": "#ffd166",
    "initialPosition": [-4.42, 57.28],
    "initialAltitudeMetres": 1200,
    "initialSpeedMetresPerSecond": 80,
    "state": "running"
  }]
}
```

Completion policies are `despawn`, `restart`, `loop`, `hold`, and `reverse`.

## Flight model

For every tick, the Tracks service:

1. resolves the active route leg;
2. moves speed and altitude towards its targets within aircraft limits;
3. calculates the desired bearing to the current waypoint;
4. limits heading change to the permitted turn rate;
5. advances the aircraft using its current heading;
6. reaches the exact waypoint before changing legs;
7. starts the next leg with the inbound heading, producing the post-waypoint
   turning circle;
8. publishes live GeoJSON and derived telemetry.

The service, not the core map, determines the physically valid position.

## Map interaction

Routes are edited through core-owned generic interactions:

- draw a line to create a route;
- select a route, aircraft, or waypoint;
- use an explicit **Move waypoint** mode to drag geometry;
- preview route geometry before persistence;
- use normal pan/zoom at all other times.

Ordinary map panning must never move a waypoint. The service validates the
final coordinate and revision before persisting it.

## Delivery plan

### Completed foundation

- [x] Persisted aircraft-type and scenario catalogues.
- [x] Routes, aircraft, waypoints, and trails as GeoJSON.
- [x] Route drawing and explicit waypoint movement.
- [x] Scenario editor with route, leg, and aircraft assignment controls.
- [x] Deterministic simulation pause, resume, and reset.
- [x] Rate-limited heading, speed, altitude, exact waypoint overflight, and
  live navigation telemetry.

### Next: core workspace shell

1. Create the generic full-size/docked workspace shell.
2. Implement resizable navigator, primary editor, inspector, and optional
   timeline regions.
3. Define the shared `workspace` capability in `API_SPEC.md`.
4. Adapt Tracks to publish workspace metadata and resource locations.
5. Port the existing route editor into the Routes pane.

### Then: Tracks workspace

1. Aircraft-type catalogue pane.
2. Scenario navigator and metadata pane.
3. Route editor with map/profile/table synchronization.
4. Aircraft editor and live inspector.
5. Basic deterministic timeline controls.

### Later simulation work

- wind and performance by altitude;
- timing and scheduled route activation;
- formations and holding patterns;
- terrain clearance;
- scripted events and conditional route branches.

## Core boundary

The core provides trusted, reusable editing surfaces. A plugin declares data
and capability contracts and remains authoritative for all domain behavior.
That separation keeps deletion, replacement, and future platform plugins
practical.
