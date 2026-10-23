# Tracks Workspace

The Tracks workspace is the first implementation of the shared workspace
direction described in the [common workspace design](../README.md).

It combines durable simulation resources with a live flight subsystem:

```text
Tracks workspace
├─ Aircraft types
├─ Routes
├─ Aircraft
└─ Simulation
   ├─ run / pause / reset
   ├─ live aircraft telemetry
   └─ published GeoJSON routes, tracks, and trails
```

## Current responsibilities

The Kody core renders the trusted workspace shell, resource lists, forms,
route editor, timeline controls, and map interaction modes. It saves the
current route before map editing, supplies visible waypoint handles, and
protects normal map panning from changing route geometry.

The Tracks service owns:

- aircraft-type limits;
- scenario, route, waypoint, and aircraft persistence;
- revision validation and cross-resource validation;
- route-completion policy;
- flight progression, speed, altitude, and turn-rate behavior;
- simulation time and published live GeoJSON.

## Route editor

The route editor is a full-width primary view rather than an inspector:

- route name, color, and completion policy share the metadata row;
- every waypoint exposes name, longitude, latitude, altitude, and speed;
- an inserted waypoint starts halfway between its neighbouring waypoints;
- editing on the map saves the route first and persists waypoint drops;
- map editing is an explicit mode exited with Escape.

## Next extraction step

The current core implementation is still Tracks-aware. It should evolve into
the shared `collection`, `detailForm`, `routeEditor`, and `mapGeometryEditor`
primitives described in the common design. Tracks should then declare a
line-route resource instead of relying on pane identifiers such as `routes`
or `aircraft`.
