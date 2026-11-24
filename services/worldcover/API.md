# ESA WorldCover 2021 service REST API

**Default base URL:** `http://127.0.0.1:8014`.

This document records the REST endpoints implemented by this service. Paths are relative to the base URL.

## Conventions

- Request and response bodies use JSON unless the endpoint returns binary map-tile content.
- `PUT` configuration and editable-resource endpoints use the current revision in `If-Match` when the service advertises a revision header.
- Dynamic path segments such as `{zoom}`, `{x}`, `{y}`, and resource IDs are required path parameters.
- Non-success responses use the Kody error convention documented in the repository [API specification](../../API_SPEC.md).
- `GET /api/kody/manifest` is the authoritative capability-discovery response for this service.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/actions/assess-current-viewport` | Return the native-source download assessment for the current viewport. |
| `POST` | `/api/actions/clear-local-data` | Start or perform clear local data. |
| `GET` | `/api/actions/download-current-viewport` | Return status for native-source download. |
| `POST` | `/api/actions/download-current-viewport` | Download and store native GeoTIFF source files for the current viewport; it does not render map tiles. |
| `GET` | `/api/config` | Return the current generated zoom levels and revision. |
| `PUT` | `/api/config` | Validate and persist comma-separated zoom levels allowed for on-demand tile rendering. |
| `GET` | `/api/kody/manifest` | Return the Kody capability manifest. |
| `GET` | `/api/tiles/revision` | Return the standardized WebP tile revision. |
| `GET` | `/api/tiles/status` | Return queued, actively rendering, and completed tile-request counts. |
| `GET` | `/api/tiles/{zoom}/{x}/{y}` | Return a standardized WebP map tile. |
| `GET` | `/api/raster/revision` | Deprecated compatibility alias for `/api/tiles/revision`. |
| `GET` | `/api/raster/{zoom}/{x}/{y}` | Deprecated compatibility alias for `/api/tiles/{zoom}/{x}/{y}`. |

## Request models and response formats

The endpoint implementation validates request bodies and query parameters with its service models. For Kody-facing data, configuration, and tile contracts, see the service manifest and the repository [API specification](../../API_SPEC.md).

Missing tiles are rendered on demand only when requested at a configured zoom
level and only from native GeoTIFF sources already stored locally.
Configured-zoom requests return only the exact stored or newly rendered tile;
they do not fall back to a lower-zoom parent tile. If the required native
source is not stored, the request returns `404` and native data must be
downloaded through the separate viewport action.
During native-source download, ESA `404` responses are recorded as unavailable
coverage and skipped; other HTTP and network errors fail the action explicitly.

`GET /api/tiles/status` returns the standard activity fields `status`,
`message`, `progress`, `processed`, `total`, `queued`, and `retained`. The
manifest advertises this as `configuration.activityStatusPath`.
