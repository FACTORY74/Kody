# Kody Microservice API Specification

**Version:** 1

**Status:** Normative for new map microservices

This document defines the HTTP contract between a Kody map microservice and
the central map product. A service owns its own source data, cache, and
implementation. The central product discovers its display behaviour from the
service manifest; it must not need a service-specific endpoint, response
shape, or browser integration.

Existing services predate this specification. Their currently supported
endpoints are listed in [Legacy compatibility](#legacy-compatibility). New
services MUST implement the normative contract below. Existing services SHOULD
migrate their manifests and endpoints to it before the central map client
removes its legacy adapters.

## 1. General rules

### 1.1 Base URL and transport

- A service is registered with a base URL, for example
  `http://127.0.0.1:8012`.
- All paths in this specification are relative to that base URL.
- JSON request and response bodies use UTF-8 and
  `Content-Type: application/json`.
- The service MUST allow the central browser origin in CORS. The local default
  origins are `http://127.0.0.1:8000` and `http://localhost:8000`.
- A service MUST expose `GET /api/kody/manifest`. It is the only endpoint the
  central map needs to probe in order to discover a service.
- Paths supplied by a manifest MUST begin with `/api/`. They MUST be relative
  paths, never complete URLs.
- A service MUST return a non-2xx status for errors. It MUST NOT return a
  successful response containing an error-shaped payload.

### 1.2 Error responses

Services SHOULD use the following error shape:

```json
{
  "detail": "human-readable explanation"
}
```

The central map treats these status codes consistently:

| Status | Meaning |
|---|---|
| `400` | Invalid request syntax or unsupported request option. |
| `404` | Resource, tile, or optional result is unavailable. |
| `409` | The request conflicts with current state, such as a stale configuration revision or an already-running exclusive action. |
| `422` | Request values are valid JSON but fail semantic validation. |
| `429` | Service is temporarily rate-limited. Include `Retry-After` where possible. |
| `500`-`599` | Service or upstream failure. Do not include secrets, tokens, or internal stack traces. |

## 2. Service discovery

### 2.1 `GET /api/kody/manifest`

The manifest declares every external capability consumed by the central map.
The response MUST contain these fields:

```json
{
  "kodyApiVersion": "1",
  "serviceId": "example-imagery",
  "displayName": "Example imagery",
  "layer": {
    "kind": "raster-tile",
    "tilePathTemplate": "/api/tiles/{z}/{x}/{y}",
    "revisionPath": "/api/tiles/revision",
    "contentTypes": ["image/webp"],
    "tileSizePixels": 256,
    "minZoom": 0,
    "maxZoom": 14
  },
  "configuration": {
    "readPath": "/api/config",
    "writePath": "/api/config",
    "fields": [],
    "actions": []
  }
}
```

`serviceId` is a stable identifier owned by the service. It MUST match
`^[a-z0-9][a-z0-9-]{0,63}$` and MUST NOT change when the user changes a
display name. `displayName` is user-facing text.

A service provides zero or one `layer` object. A service with no layer is a
configuration or utility service and is not rendered on the map.

The central runtime registration ID identifies a local process instance. It is
not an API type and MUST NOT be used to choose custom endpoints or rendering
logic. The manifest's `layer.kind` is the rendering contract.

### 2.2 Shared manifest members

All optional manifest members have a fixed schema:

| Member | Purpose |
|---|---|
| `layer` | Declares one rendered layer using a standard layer-kind contract. |
| `configuration` | Declares editable service configuration and user-triggered actions. |
| `contextMenuActions` | Declares actions that receive a map longitude/latitude point. |
| `featureActions` | Declares typed actions available for selected service features. |
| `creationActions` | Declares typed actions that create a new service object. |
| `capabilities` | Declares standard non-rendering capabilities, such as elevation. |

Unknown members and unknown enum values MUST be ignored by the central map for
forward compatibility. A service MUST NOT require a central-map change merely
to consume an unknown custom manifest member.

## 3. Layer contracts

All tile coordinates use the Web Mercator / slippy-map scheme:

- `z` is an integer zoom level.
- `x` and `y` are integers where `0 <= x, y < 2^z`.
- The normal tile size is 256 pixels.
- Geographic feature coordinates are WGS 84 longitude/latitude in `[longitude,
  latitude]` order.

### 3.1 `geojson`

Use for static, live, or periodically refreshed feature overlays.

```json
{
  "kind": "geojson",
  "dataPath": "/api/data",
  "refreshSeconds": 2
}
```

`GET dataPath` MUST return a GeoJSON `FeatureCollection` augmented with:

```json
{
  "type": "FeatureCollection",
  "revision": "opaque-revision",
  "features": [],
  "source": {
    "lastError": null
  }
}
```

- `revision` MUST change whenever the rendered feature content changes.
- `features` MUST be an array of valid GeoJSON features.
- `source` is optional metadata. If `source.lastError` is a string, the
  central map may display it as a non-fatal live-data warning.
- `refreshSeconds` is optional. If omitted, the central map selects its normal
  overlay refresh interval.

A live GeoJSON service that can suspend upstream polling MUST declare:

```json
{
  "activityPath": "/api/activity"
}
```

The central map sends `POST activityPath` with `{"active": true}` when the
layer is enabled and `{"active": false}` when it is disabled. The response is
`{"active": true}` or `{"active": false}`.

### 3.2 `symbol-catalog`

Use for a reusable catalogue of SVG symbols. It is not itself a rendered
feature layer.

```json
{
  "kind": "symbol-catalog",
  "dataPath": "/api/symbols"
}
```

`GET dataPath` MUST return:

```json
{
  "revision": "opaque-revision",
  "symbols": [
    {
      "name": "aircraft",
      "svg": "<svg ...>...</svg>",
      "orientation": "orientable",
      "colorMode": "colorable",
      "scale": 1
    }
  ]
}
```

`orientation` is `orientable` or `alwaysup`; `colorMode` is `colorable` or
`fixed`; and `scale` is a finite number greater than zero.

### 3.3 `raster-tile`

Use for server-rendered imagery, terrain shading, cached vector-cartography
renders, and other image tiles.

```json
{
  "kind": "raster-tile",
  "tilePathTemplate": "/api/tiles/{z}/{x}/{y}",
  "revisionPath": "/api/tiles/revision",
  "contentTypes": ["image/webp"],
  "tileSizePixels": 256,
  "minZoom": 0,
  "maxZoom": 18,
  "missingTileStatus": 404
}
```

`GET revisionPath` MUST return `{"revision": "opaque-revision"}`. The
revision MUST change when a cached image could be different for the same
coordinate. The central map uses it to invalidate its in-memory image cache.

The central map substitutes decimal `z`, `x`, and `y` values in
`tilePathTemplate` and performs `GET` on the resulting URL:

- `200 OK` MUST contain an image with a content type listed in `contentTypes`.
  It SHOULD use immutable cache headers when the revision is part of the
  client cache key.
- `404 Not Found` means the tile is currently unavailable. It is not a
  service-wide failure and the central map may retry later.
- `202 Accepted` means the service queued on-demand rendering. Its JSON body
  MUST be `{"status": "queued" | "rendering", "retryAfterMilliseconds": 1000}`
  and the central map retries after the indicated positive interval.
- `422 Unprocessable Content` means invalid tile coordinates.

`missingTileStatus` defaults to `404` and exists only to permit a future
standard alternative. A service MUST NOT invent a custom successful
missing-tile payload.

### 3.4 `vector-tile`

Use for Mapbox Vector Tiles when the central renderer supports them.

```json
{
  "kind": "vector-tile",
  "tilePathTemplate": "/api/tiles/{z}/{x}/{y}.pbf",
  "revisionPath": "/api/tiles/revision",
  "contentTypes": ["application/vnd.mapbox-vector-tile"],
  "minZoom": 0,
  "maxZoom": 14
}
```

The revision, coordinate validation, `404`, `202`, and `422` semantics are
identical to `raster-tile`. `200` responses contain a valid MVT payload.
Style information belongs in the declared layer contract once vector-tile
rendering is added; a service MUST NOT require an ad hoc browser renderer.

### 3.5 Feature identity and selection

Selectable GeoJSON features MUST include a stable string `id` and set
`properties.kody.selectable` to `true`. The identifier is owned by the
microservice and MUST remain stable while it denotes the same logical object.
A client MUST send the identifier back to the service rather than treating
copied feature properties as authoritative.

The optional `properties.kody.selectionLabel` is a concise, safe,
user-facing label for the selection list. If it is absent, the central map
uses the feature ID. `properties.kody` is reserved for this shared interaction
metadata; a service MAY NOT use it for a custom action path or UI definition.

The manifest declares feature actions at the top level:

```json
{
  "featureActions": [
    {
      "id": "inspect-track",
      "label": "Track details",
      "path": "/api/feature-actions/inspect-track",
      "method": "POST",
      "selection": {
        "minItems": 1,
        "maxItems": 1,
        "geometryTypes": ["Point"]
      }
    }
  ]
}
```

`id` is stable and unique within the service. `selection` is optional; its
defaults are `minItems: 1` and `maxItems: 1`. `geometryTypes`, when present,
contains GeoJSON geometry type names. The central map only presents an action
when the current selection meets these declared constraints.

A service MAY further limit actions per feature by returning
`properties.kodyActions`, an array of manifest-declared action IDs. The
central map MUST intersect this list with the manifest actions; it MUST NOT
accept paths, labels, or action definitions supplied by feature properties.

Raster and vector-tile services cannot currently expose individual selectable
features. They SHOULD use [context-menu actions](#7-context-menu-actions) or
publish a separate `geojson` interaction layer for selectable objects.

### 3.6 Standard capabilities

Non-rendering functions are declared rather than inferred from a service ID.
For example, an elevation provider declares:

```json
{
  "capabilities": {
    "elevation": {
      "path": "/api/elevation",
      "query": ["longitude", "latitude"]
    }
  }
}
```

`GET /api/elevation?longitude={longitude}&latitude={latitude}` returns:

```json
{
  "heightMetres": 123.4,
  "sourceId": "optional-stable-source-id"
}
```

`heightMetres` may be `null` when no local data covers the point.

## 4. Configuration contract

`manifest.configuration` is optional. If provided, it MUST include:

```json
{
  "readPath": "/api/config",
  "writePath": "/api/config",
  "fields": [],
  "actions": [],
  "activityStatusPath": "/api/activity/status"
}
```

`GET readPath` returns the current configuration object plus a required opaque
`revision` string. `PUT writePath` accepts the editable fields and the
central map sends the prior revision in the `If-Match` header. A successful
response returns the updated configuration with a new `revision`.

If the supplied `If-Match` revision is stale, the service MUST return
`409 Conflict`. Secret fields MUST NOT return their stored value. Instead,
the response may expose a boolean such as `clientSecretSaved`.

Each field is:

```json
{
  "key": "refreshSeconds",
  "label": "Refresh interval (seconds)",
  "type": "number",
  "required": true,
  "minimum": 1,
  "maximum": 300,
  "step": 1,
  "secretStoredKey": "clientSecretSaved"
}
```

Supported `type` values are `string`, `password`, `number`, `boolean`,
`color`, and `position`. A `position` value is:

```json
{"longitude": -4.5, "latitude": 55.9}
```

`longitude` is in `[-180, 180]`; `latitude` is in
`[-85.05112878, 85.05112878]`.

## 5. Actions and asynchronous activity

Configuration actions are declared by:

```json
{
  "label": "Import source data",
  "path": "/api/actions/import",
  "method": "POST",
  "statusPath": "/api/actions/import",
  "confirmationMessage": "Replace local data?",
  "confirmationPath": "/api/actions/assess-import"
}
```

- `path` is invoked using `method`, which defaults to `POST`.
- An asynchronous action MUST return `202 Accepted` and
  `{"message": "work started"}`.
- Its `statusPath` MUST return the standard activity response below.
- `confirmationMessage` is optional user-facing text.
- If `confirmationPath` is provided, `GET` returns
  `{"requiresConfirmation": true, "message": "..."}` before the action is
  invoked.

An activity-status response is:

```json
{
  "status": "idle",
  "message": "No work is running.",
  "progress": 0,
  "processed": 0,
  "total": 0,
  "queued": 0,
  "retained": 0
}
```

`status` is one of `idle`, `queued`, `running`, `processing`, `completed`, or
`failed`. `message` is required. The numeric members are optional
non-negative integers; `progress`, if provided, is in `[0, 100]`. A failed
action reports `status: "failed"` and a safe diagnostic message.

The optional `configuration.activityStatusPath` provides the same response
for long-running background work not associated with one action.

## 6. Feature actions and interactive workflows

Feature actions turn a selection into a service command. The central map
provides a generic selection and form UI; a service defines the data contract,
not a custom browser view.

### 6.1 Selection interaction and panel

The central map uses these selection gestures on a selectable feature layer:

- **Shift+click** toggles every eligible feature within a 12 CSS-pixel radial
  capture area around the pointer.
- **Shift+drag** displays a dotted rectangular marquee from the pointer-down
  position to the current pointer. On release, it toggles every eligible
  feature inside the rectangle.
- Normal click and drag retain their ordinary map interaction behaviour.

Selection is keyed by `{serviceId, featureId}`. Repeated selection of the same
key removes it. The central map keeps selection while a layer is temporarily
hidden, but removes it when the service disappears. When a layer revision
changes, the client retains only the feature reference and marks it stale; the
service remains responsible for accepting or rejecting it at action time.

The Display controls panel includes a **Selection** tab beside the existing
layer controls. Each selected item shows:

1. its service display name and `selectionLabel` or feature ID;
2. an **X** control that immediately removes that item from the selection;
3. an **arrow** control that opens only the actions declared by that item's
   service and allowed by its `kodyActions` list.

Every selected item also has the standard map-engine action **Center on
screen**. It moves the selected object's current point to the viewport marker
and keeps it there as live updates arrive. Scroll-wheel zoom remains available
while centring. A normal left-click drag that pans the map stops centring and
returns to ordinary map movement.

The panel may later offer actions that accept the complete selection. It MUST
not merge features from different services unless an action explicitly
declares a cross-service contract in a later API version.

### 6.2 Invocation request

The central map invokes `POST featureActions[].path` with:

```json
{
  "selection": [
    {
      "featureId": "flight-4ca123",
      "revision": "opaque-layer-revision"
    }
  ],
  "parameters": {
    "includeHistory": true
  }
}
```

`selection` contains only identifiers and the GeoJSON layer revision from
which they were selected. The service MUST re-resolve the selected features
and authorize the requested action itself. It MUST return `409 Conflict` when
the selection revision is no longer usable and the action cannot safely apply
to the current object.

`parameters` contains values declared by the action's optional `input` object:

```json
{
  "id": "define-exclusion-area",
  "label": "Define exclusion area",
  "path": "/api/feature-actions/define-exclusion-area",
  "selection": {"minItems": 0, "maxItems": 0},
  "input": {
    "fields": [
      {"key": "name", "label": "Name", "type": "string", "required": true},
      {
        "key": "area",
        "label": "Area",
        "type": "geometry",
        "geometryTypes": ["Circle", "Rectangle", "Polygon"]
      }
    ]
  }
}
```

The existing configuration field types are valid action-input field types.
Action inputs additionally support `select` and `geometry`:

- A `select` field supplies non-empty `options`, each with `value` and
  user-facing `label`.
- A `geometry` field supplies one or more `geometryTypes`. The central map
  collects the geometry with its native drawing tools and serializes it using
  the [geometry input format](#63-geometry-input-format).

### 6.3 Geometry input format

Simple geometries use standard GeoJSON geometry objects in WGS 84:

```json
{"type": "LineString", "coordinates": [[-4.5, 55.9], [-4.4, 55.8]]}
```

The following map-native geometries use these standardized objects because
they cannot be represented exactly by GeoJSON alone:

```json
{"type": "Circle", "center": [-4.5, 55.9], "radiusMetres": 5000}
{"type": "Rectangle", "west": -4.6, "south": 55.8, "east": -4.4, "north": 56.0}
{
  "type": "Wedge",
  "center": [-4.5, 55.9],
  "radiusMetres": 5000,
  "innerRadiusMetres": 0,
  "startBearingDegrees": 20,
  "endBearingDegrees": 80
}
```

Coordinates remain `[longitude, latitude]`. Radius values are positive metres;
bearings are degrees clockwise from true north in `[0, 360)`. For a
`Rectangle`, `west < east` and `south < north`. A service MUST validate all
geometry before persisting or acting on it.

Waypoints are supplied as a GeoJSON `LineString` or as a `MultiPoint`,
according to the action's declared geometry types. The service owns any route
calculation, storage, or domain constraints; the central map only captures
the user-selected geometry.

### 6.4 Creating new objects

Services declare actions that create a new object at the top level of the
manifest. `creationActions` use the same schema as `featureActions`, except
their selection is always empty:

```json
{
  "creationActions": [
    {
      "id": "create-patrol-route",
      "label": "Create patrol route",
      "path": "/api/creation-actions/create-patrol-route",
      "method": "POST",
      "input": {
        "fields": [
          {"key": "name", "label": "Route name", "type": "string", "required": true},
          {
            "key": "waypoints",
            "label": "Waypoints",
            "type": "geometry",
            "geometryTypes": ["LineString", "MultiPoint"]
          }
        ]
      }
    }
  ]
}
```

The central map exposes these actions in a **Create** section of the
Selection tab, even when no item is selected. Choosing an action opens its
standard form and, when needed, starts a visible construction session:

- **Point, MultiPoint, LineString, Polygon:** click to add vertices;
  Backspace removes the last vertex; Enter or an explicit **Finish** button
  completes the geometry; Escape or **Cancel** discards it.
- **Circle:** click-drag from the centre to its radius.
- **Rectangle:** click-drag between opposite corners.
- **Wedge:** click the centre, drag to choose range and bearing, then use
  standard numeric form inputs for precise start/end bearings and inner range.

While constructing, the map shows a preview and disables ordinary feature
selection for the active pointer gesture. It does not submit partial geometry
to the service. The client sends one validated action request only after the
user selects **Finish** and confirms the standard form.

This makes routes, polygons, circles, rectangles, and wedges ordinary
declared service operations rather than a new central-map mode for every
domain. A service can return the new object in the standard `overlay` result
or publish it on its normal GeoJSON layer.

### 6.5 Standard action results

A completed feature action returns a JSON object composed from these standard
members:

```json
{
  "message": "Track history loaded.",
  "details": {
    "title": "Flight 4CA123",
    "sections": [
      {
        "title": "Current position",
        "fields": [
          {"label": "Altitude", "value": "9,800 m"},
          {"label": "Speed", "value": "815 km/h"}
        ]
      }
    ]
  },
  "overlay": {
    "type": "FeatureCollection",
    "features": []
  },
  "statusPath": "/api/feature-actions/inspect-track/status"
}
```

- `message` is safe user-facing status text.
- `details` is a generic read-only inspector model. Its `title` and
  `sections` are required when `details` is provided. Field values are strings
  and are rendered as text, not HTML.
- `overlay`, when present, is a GeoJSON `FeatureCollection` drawn by the
  central map as temporary action output.
- `statusPath`, when present, returns the standard
  [activity-status response](#5-actions-and-asynchronous-activity) until the
  action completes. A final status response MAY contain `details` and
  `overlay`.

For longer, multi-stage work, the service returns `202 Accepted` with a
standard activity status path. It MAY include a `nextAction` object using the
same declared-action schema. The next action MUST still be declared in the
manifest; a service MUST NOT send executable code, arbitrary URLs, raw HTML,
or an unbounded custom UI schema to the central client.

## 7. Context-menu actions

Each `contextMenuActions` entry has:

```json
{
  "id": "set-origin",
  "label": "Set origin here",
  "path": "/api/actions/set-origin",
  "method": "POST",
  "statusPath": "/api/actions/set-origin",
  "resultPath": "/api/results/origin"
}
```

`id` is stable and unique within its service. The central map sends this
point body to `path`:

```json
{"longitude": -4.5, "latitude": 55.9}
```

`statusPath` and `resultPath` are optional. When `resultPath` is present,
`GET resultPath` MUST return a GeoJSON `FeatureCollection`.

## 8. Shared central-map context API

The central configuration service exposes transient viewport context for
services that need to limit their work to the currently visible area:

### `GET /api/context/viewport`

Returns:

```json
{
  "west": -5.2,
  "south": 57.1,
  "east": -4.1,
  "north": 57.8,
  "zoom": 9
}
```

The endpoint returns `404` before the browser has published a viewport.
`west < east` and `south < north`; antimeridian-spanning views are not
represented by one request.

### `PUT /api/context/viewport`

The central browser publishes the same object after camera changes. Services
MAY read this transient value; they MUST NOT depend on it for startup or
correctness.

## 9. Legacy compatibility

The current central client still has adapters for these pre-specification
service registration types:

| Existing type | Current layer endpoint(s) | Normative replacement |
|---|---|---|
| `geojson`, `shapes`, `adsb`, `ais`, `airlanes`, `tracks` | `/api/geojson` or `/api/shapes` | `layer.kind: "geojson"` with declared `dataPath` |
| `symbols` | `/api/symbols` | `layer.kind: "symbol-catalog"` |
| `satmap`, `copernicus`, `terrain` | `/api/raster/revision`, `/api/raster/{z}/{x}/{y}` | `layer.kind: "raster-tile"` |
| `vecmap` | `/api/tiles/revision`, `/api/tiles/{z}/{x}/{y}.webp` | `layer.kind: "raster-tile"` |

These endpoint differences are compatibility behaviour, not patterns for new
services. A new service MUST expose the manifest-declared standard path
template and MUST NOT require the central map to add another type-specific
branch.

## 10. Conformance checklist

Before registering a new microservice, verify that it:

1. Serves `GET /api/kody/manifest` with `kodyApiVersion: "1"` and a stable
   `serviceId`.
2. Declares exactly one supported `layer.kind`, or deliberately declares no
   layer.
3. Uses only the relevant standard data, revision, configuration, action, and
   context-action response shapes.
4. Validates coordinate and configuration input, returns the documented HTTP
   statuses, and never exposes secrets in errors or configuration reads.
5. Allows the central browser origins through CORS.
6. Works when the central viewport context is unavailable.
7. Uses stable feature IDs and manifest-declared feature actions for
   selectable objects.
8. Requires no new service-specific endpoint, renderer, or request branch in
   the central map.
