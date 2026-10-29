# Copernicus Global Mosaic service REST API

**Default base URL:** `http://127.0.0.1:8011`.

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
| `GET` | `/api/actions/assess-current-viewport` | Return status for assess current viewport. |
| `GET` | `/api/actions/assess-viewport-at-10m` | Return status for assess viewport at 10m. |
| `GET` | `/api/actions/build-overview-tiles` | Return status for build overview tiles. |
| `POST` | `/api/actions/build-overview-tiles` | Start or perform build overview tiles. |
| `POST` | `/api/actions/clear-local-data` | Start or perform clear local data. |
| `GET` | `/api/actions/download-current-viewport` | Return status for download current viewport. |
| `POST` | `/api/actions/download-current-viewport` | Start or perform download current viewport. |
| `GET` | `/api/actions/download-viewport-at-10m` | Return status for download viewport at 10m. |
| `POST` | `/api/actions/download-viewport-at-10m` | Start or perform download viewport at 10m. |
| `GET` | `/api/config` | Return the current service configuration. |
| `PUT` | `/api/config` | Validate and persist service configuration. |
| `GET` | `/api/kody/manifest` | Return the Kody capability manifest. |
| `GET` | `/api/mosaics` | Return mosaics. |
| `GET` | `/api/raster/revision` | Return raster revision. |
| `GET` | `/api/raster/{zoom}/{x}/{y}` | Return raster tile. |

## Request models and response formats

The endpoint implementation validates request bodies and query parameters with its service models. For Kody-facing data, configuration, and tile contracts, see the service manifest and the repository [API specification](../../API_SPEC.md).
