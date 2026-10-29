# InteractionServer REST API

**Default base URL:** `http://127.0.0.1:8017`.

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
| `POST` | `/api/interactions` | Submit interaction. |
| `GET` | `/api/interactions/geojson` | Return the current GeoJSON feature collection. |
| `GET` | `/api/kody/manifest` | Return the Kody capability manifest. |

## Request models and response formats

The endpoint implementation validates request bodies and query parameters with its service models. For Kody-facing data, configuration, and tile contracts, see the service manifest and the repository [API specification](../../API_SPEC.md).
