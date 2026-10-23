# Kody Common Workspace Design

## Purpose

Kody has two complementary extension surfaces:

- **Layers** answer: "What should the map draw now?" A service publishes
  raster tiles, vectors, symbols, or GeoJSON for the core map to compose.
- **Workspaces** answer: "How does an operator inspect, configure, analyse,
  and operate this domain?" A service declares structured resources and
  operations; Kody renders a trusted operational UI.

When a workspace-capable service also publishes a display layer, it appears
once in the map display pane's **Workspace** category. The standard enable
and opacity controls govern visibility, and its action button opens the
workspace.

This document defines the direction for a common workspace system and uses
two deliberately different examples:

1. **Tracks** is a stateful platform-simulation workspace with durable
   scenarios and a live clock.
2. **Terrain Analysis** is an analytical workspace that queries one or more
   elevation sources, creates line-of-sight (LOS) rosettes, and presents
   point-to-point elevation profiles.

The comparison prevents the workspace model from becoming an implicit Tracks
frontend. It must be useful to both a simulation service and a stateless or
job-oriented analysis service.

## Status and scope

The existing Tracks workspace shell, route editing, terrain elevation API,
and LOS rosette operation are implemented. The common resource declarations,
atomic workspace snapshots, Terrain Analysis workspace, and elevation-profile
operation described below are design proposals until they are added to
`API_SPEC.md` and implemented.

This is an architecture and UX design. It does not grant a service the right
to return HTML, JavaScript, CSS, arbitrary component trees, or arbitrary map
event handlers.

## Terms

| Term | Meaning |
|---|---|
| **Core** | The Kody map client and its trusted rendering/interactions. |
| **Service** | A Python microservice discovered through the Kody manifest. |
| **Layer** | A service-published map representation such as raster tiles or GeoJSON. |
| **Workspace** | A core-rendered, service-declared operational surface. |
| **Resource** | Durable or live structured domain data with an API and revision contract. |
| **View** | A core-owned presentation primitive, such as a collection, form, route editor, chart, or timeline. |
| **Command** | A service-declared operation with typed input and explicit result/status semantics. |
| **Map interaction** | A trusted core editing mode selected declaratively by a workspace. |
| **Subsystem** | Private service logic, such as flight dynamics or terrain sampling. It is never a core concern. |

## Architectural boundary

```text
Kody core
├─ Map canvas, navigation, selection, and layer composition
├─ Trusted workspace shell and standard views
├─ Form validation presentation, revisions, errors, and accessibility
└─ Map interaction modes
   ├─ edit point / line / polygon
   ├─ choose analysis origin / destination
   └─ render temporary and persisted result overlays

Service
├─ Declarative resources, snapshots, and commands
├─ Domain validation and persistence
├─ Domain calculations and long-running jobs
└─ Published layer data
```

The core must not understand flight physics, terrain-source precedence,
visibility rules, or the meaning of a route-completion policy. Conversely, a
service must not own browser rendering, drag gesture code, focus management,
or arbitrary UI layout.

### Why this boundary matters

- It provides a consistent application UI across services.
- It prevents a service from executing untrusted browser code.
- It lets the core reuse high-quality geometry, revision, error, and
  accessibility behavior.
- It keeps a service replaceable: removing Tracks removes simulation behavior,
  not the workspace platform.
- It avoids an ever-growing collection of service-ID-specific branches in the
  map client.

## Common workspace shell

The shell has a small number of stable regions. A region is shown only when it
adds value to the active view.

```text
+-------------------------------------------------------------------+
| Workspace title · snapshot state · unsaved/conflict state         |
+-----------------------+-------------------------------------------+
| Navigator             | Primary view                              |
|                       |                                           |
| Resource groups       | Collection, editor, analysis, or chart    |
| Saved analyses        |                                           |
|                       |                                           |
+-----------------------+-------------------------------------------+
| Optional timeline / job status / map-interaction command strip   |
+-------------------------------------------------------------------+
```

### Navigator

The navigator contains domain resources and saved analysis objects, not raw
HTTP paths or implementation details. It may show counts, status badges, and
an active selection.

Examples:

```text
Tracks                         Terrain Analysis
├─ Overview                    ├─ Overview
├─ Aircraft types              ├─ Elevation sources
├─ Routes                      ├─ Line of sight
├─ Aircraft                    ├─ Elevation profiles
└─ Simulation                  └─ Saved analyses
```

### Primary view

The primary view is the operator's active task. It is the only mandatory
workspace region. A route editor or terrain profile should use the full width
to the right of the navigator rather than retaining an empty inspector.

### Inspector

The inspector is conditional. It shows selected-object telemetry, validation,
provenance, cross-resource references, or read-only detail. It must not
duplicate a full editor.

For example:

- Tracks: selected aircraft state, leg ETA, target altitude, and errors.
- Terrain: selected sample's height, contributing source, resolution, and
  no-data status.

### Bottom region

The bottom region is not inherently a timeline. It is a core-provided
**activity region**:

- Tracks renders deterministic time and run/pause/reset controls.
- Terrain renders long-running job progress, warnings, and result summaries.
- A simple workspace may omit it.

This avoids making the common shell simulation-specific.

## Trusted UI vocabulary

The service chooses from a constrained, versioned vocabulary. The core owns
the visual language and interaction implementation.

| View or interaction | Core responsibility | Service declaration |
|---|---|---|
| `collection` | list/table, selection, empty state, sorting affordances | resource fields and summary columns |
| `detailForm` | labels, controls, validation display, save/cancel | typed fields, constraints, values, revision |
| `routeEditor` | waypoint table, map handoff, midpoint insertion | line geometry and per-waypoint fields |
| `mapGeometryEditor` | handles, preview, pan protection, commit/cancel | point, line, or polygon resource/action |
| `profileChart` | axis, units, cursor, segment highlighting | ordered distance/elevation samples |
| `metrics` | concise read-only status cards | labelled values |
| `activity` | progress, cancellation, error/result state | operation-status resource |
| `timeline` | elapsed time, playback controls, events | simulation-control capability and events |

The first implementation should support only the primitives proven by Tracks
and Terrain. New primitives are added after at least two domains require them.

## Data, snapshots, and revisions

The existing workspace capability describes panes and resource locations. The
next version should separate three concerns:

1. **Definition** — view declarations, resources, commands, and labels.
2. **Snapshot** — coherent resource values and their individual revisions.
3. **Commands** — explicit reads or writes that may create jobs/results.

Opening a workspace must not combine independently changing resources without
declaring that fact. A workspace should return an atomic snapshot, or a
revision vector that the core can verify:

```json
{
  "workspaceVersion": 1,
  "snapshotRevision": "opaque",
  "title": "Terrain Analysis",
  "resources": {
    "sources": {
      "revision": "sources-r7",
      "value": {"items": []}
    },
    "analyses": {
      "revision": "analyses-r12",
      "value": {"items": []}
    }
  }
}
```

Each writable resource retains its own optimistic-concurrency header and
revision. A workspace-level revision is for snapshot coherence, not a
replacement for resource-level conflict handling.

`GET` operations must be read-only. Detecting an external local-file change
may produce a status notification, but applying it, resetting simulation, or
invalidating analyses requires an explicit command.

## Declarative workspace outline

The precise schema belongs in `API_SPEC.md`. This shape illustrates the
boundary without embedding presentation code:

```json
{
  "id": "terrain-analysis",
  "label": "Terrain Analysis",
  "openPath": "/api/workspace",
  "resources": [{
    "id": "profiles",
    "kind": "analysisCollection",
    "readPath": "/api/terrain/profiles",
    "views": ["collection", "profileChart"]
  }],
  "panes": [{
    "id": "profiles",
    "label": "Elevation profiles",
    "resource": "profiles",
    "primaryView": "profileChart"
  }],
  "commands": [{
    "id": "create-profile",
    "kind": "mapInteraction",
    "mode": "chooseLine",
    "resultResource": "profiles"
  }]
}
```

Allowed field types, enum values, number constraints, resource references,
and map-interaction modes must be schema-validated. A service cannot name an
unknown view, inject a DOM selector, or supply arbitrary labels for untrusted
feature data.

## Map interactions and result overlays

Map-native work belongs to the core. A workspace can request a declared mode:

| Mode | Example |
|---|---|
| `choosePoint` | Choose an LOS observer origin. |
| `chooseLine` | Choose profile start and end. |
| `editLineString` | Move route waypoints. |
| `editPolygon` | Edit an analysis area or shape. |

The interaction lifecycle is consistent:

1. The core saves or validates active form state before entering the mode.
2. The core announces the mode and renders temporary geometry.
3. The user commits, cancels, or presses Escape.
4. The core sends typed geometry to the declared command or resource write.
5. The service validates and returns a new revision, job, or result.
6. The core refreshes only affected resources and overlays.

Services calculate results. The core draws them as standard layers:

- Tracks publishes routes, aircraft, and trails as GeoJSON.
- Terrain publishes or returns LOS and profile result geometry.

The core may render temporary pending geometry, but never reconstructs the
domain algorithm locally.

## Comparison workspace: Tracks

Tracks remains the first implemented workspace and demonstrates durable
catalogues plus a live subsystem.

### Resources

| Resource | Core views | Service authority |
|---|---|---|
| Aircraft types | collection, detail form | performance limits and validation |
| Scenario | summary, references | durable scenario and revisions |
| Routes | collection, route editor, map line editing | completion policy and waypoint targets |
| Aircraft | collection, detail form, inspector | assignment and initial state |
| Simulation | timeline/activity | clock, reset, runtime state |

### Tracks-specific subsystem

The Tracks service owns route progression, speed/altitude change, turning,
and live telemetry. The core only renders the route and live output and
provides map editing. A rate-limited turn is a service behavior, not a
generic workspace feature.

### Route editor UX

- Route metadata occupies a compact primary-view row.
- Waypoints expose name, longitude, latitude, altitude, and speed.
- Inserting between adjacent points starts exactly at their midpoint.
- Opening map editing persists the current route first.
- Map editing is an explicit core mode; normal map panning cannot move points.
- The primary route editor does not retain an unused inspector.

## Comparison workspace: Terrain Analysis

Terrain Analysis proves that a workspace can be useful without a simulation,
aircraft, or editable domain catalogue.

### Goal

An operator can:

1. inspect which local elevation layers cover the area;
2. choose the elevation-source policy for an analysis;
3. create and inspect an LOS rosette from an observer point;
4. draw a start-to-end line and inspect its elevation profile;
5. retain, compare, rename, or clear analysis results;
6. see source provenance, resolution, missing coverage, warnings, and job
   progress.

### Elevation layers

Terrain already composites locally imported packages for rendering and
samples elevation through `/api/elevation`. The workspace should make source
selection and provenance explicit.

An elevation source record should contain at least:

```json
{
  "id": "north-ridge-dem",
  "label": "North Ridge DEM",
  "bounds": {"west": -4.5, "south": 57.1, "east": -4.1, "north": 57.4},
  "nativeResolutionMetres": 10,
  "enabled": true,
  "priority": 20
}
```

The Terrain service owns the policy for overlapping sources. It may select
the highest resolution, use priority, or return no result for ambiguous
coverage. The workspace must display the applied source policy and the source
used by each result; it must not invent a blending rule in the browser.

### Terrain Analysis layout

```text
+-------------------------------------------------------------------+
| Terrain Analysis · source coverage · analysis status              |
+-----------------------+-------------------------------------------+
| Navigator             | Primary view                              |
|                       |                                           |
| Overview              | Source coverage map / selected analysis   |
| Elevation sources     |                                           |
| Line of sight         | LOS options and result summary            |
| Elevation profiles    | or elevation profile chart                |
| Saved analyses        |                                           |
+-----------------------+-------------------------------------------+
| Job progress · coverage warning · result provenance               |
+-------------------------------------------------------------------+
```

The right inspector appears only for a selected source/sample/result. It is
hidden while the profile chart or LOS configuration needs the full primary
width.

### LOS rosette

The current Terrain service can create a job from an origin point and returns
a GeoJSON `MultiLineString` result. Terrain configuration already controls
observer height, bearing count, sample distance, and distance limit.

The future workspace flow is:

1. Select **Line of sight**.
2. Configure observer height, range, bearing count, and sample spacing in the
   primary form.
3. Choose **Pick origin on map** (`choosePoint`).
4. The core sends the coordinate and options to a Terrain command.
5. The workspace activity region shows progress and permits cancellation when
   supported.
6. The Terrain service returns a named result with source provenance and
   missing-sample count.
7. The core draws the returned GeoJSON as an analysis overlay and shows its
   summary in the workspace.

The rosette calculation remains entirely in the Terrain service. The core
draws the result layer, manages the point-selection gesture, and presents
progress.

### Point-to-point elevation profile

The profile is a new proposed Terrain operation, not a current endpoint.

The UI flow is:

1. Select **Elevation profiles** and create a profile.
2. Choose **Draw profile line on map** (`chooseLine`).
3. The core previews the line and submits its two coordinates plus sampling
   options.
4. The Terrain service samples the line across its configured elevation
   sources and returns an ordered, immutable profile result.
5. The core renders a `profileChart` and highlights the matching distance on
   the map when the user moves through the chart.

A conceptual result shape is:

```json
{
  "id": "profile-uuid",
  "name": "Ridge crossing",
  "line": [[-4.42, 57.28], [-4.30, 57.33]],
  "distanceMetres": 10240,
  "samples": [{
    "distanceMetres": 0,
    "longitude": -4.42,
    "latitude": 57.28,
    "heightMetres": 286.4,
    "sourceId": "north-ridge-dem",
    "resolutionMetres": 10
  }],
  "statistics": {
    "minimumMetres": 122.1,
    "maximumMetres": 486.5,
    "ascentMetres": 528.8,
    "descentMetres": 312.2,
    "missingSampleCount": 4
  }
}
```

The service chooses sampling rules, handles no-data cells, and computes
statistics. The core chart must visibly break across missing samples rather
than interpolating them silently.

### Proposed Terrain resources and commands

| Item | Type | Responsibility |
|---|---|---|
| Elevation sources | read/write resource | source enablement, ordering, metadata |
| LOS options | detail form/resource | observer and sampling settings |
| LOS analyses | command/job/result resource | rosette creation, progress, GeoJSON |
| Elevation profiles | command/result resource | line sampling, provenance, chart samples |
| Analysis overlay | layer/result representation | core draws, service supplies geometry/data |

The initial implementation may preserve a single current LOS result for
backward compatibility. Saved multi-result analyses should follow once the
workspace resource model is stable.

## Delivery sequence

### 1. Stabilise the common contract

1. Align the current `scenarioEditor` specification and Tracks manifest.
2. Add manifest/capability contract tests.
3. Define the versioned workspace definition, snapshot, resource, command,
   activity, and map-interaction schemas in `API_SPEC.md`.
4. Remove state-changing behavior from ordinary workspace reads.

### 2. Extract core primitives from Tracks

1. Replace service-ID and pane-ID dispatch with resource kinds and view IDs.
2. Preserve the polished `routeEditor` behavior as a core primitive.
3. Make the inspector optional and the lower region an activity region.
4. Update simulation state without re-rendering active editors or losing
   focus.

### 3. Add Terrain Analysis

1. Publish a Terrain workspace definition and atomic snapshot.
2. Expose elevation-source metadata and policy.
3. Move LOS orchestration from a context-menu-only flow into a workspace
   command while retaining the existing quick action.
4. Add asynchronous job/cancellation/result contracts.
5. Add point-to-point profile sampling and the core `profileChart`.
6. Add provenance, no-data, overlapping-source, and boundary tests.

## Non-goals

- Running service-provided browser code.
- Encoding arbitrary layout trees in manifests.
- Making the core implement flight or terrain algorithms.
- Treating all workspaces as simulations or all bottom regions as timelines.
- Hiding source gaps, stale revisions, or job failures behind successful
  looking UI.

## Decision rule

Add a core primitive only when it is safe, generic, and useful to more than
one domain. Keep domain behavior in services.

> Services declare what operators can inspect and do. Kody owns how those
> declarations are rendered and interacted with safely.
