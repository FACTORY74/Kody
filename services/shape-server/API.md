# ShapeServer REST API

**Default base URL:** `http://127.0.0.1:8015`.

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
| `POST` | `/api/actions/clear-local-data` | Start or perform clear local data. |
| `GET` | `/api/config` | Return the current service configuration. |
| `PUT` | `/api/config` | Validate and persist service configuration. |
| `GET` | `/api/events` | Return events. |
| `POST` | `/api/events` | Submit event. |
| `GET` | `/api/kody/manifest` | Return the Kody capability manifest. |
| `GET` | `/api/shapes` | Return shapes. |
| `POST` | `/api/shapes` | Create shape. |
| `GET` | `/api/shapes/geojson` | Return the current GeoJSON feature collection. |
| `DELETE` | `/api/shapes/{shape_id}` | Delete shape. |
| `PUT` | `/api/shapes/{shape_id}` | Update shape. |

## Request models and response formats

The endpoint implementation validates request bodies and query parameters with its service models. For Kody-facing data, configuration, and tile contracts, see the service manifest and the repository [API specification](../../API_SPEC.md).
