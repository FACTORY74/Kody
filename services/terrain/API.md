# Terrain service REST API

**Default base URL:** `http://127.0.0.1:8010`.

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
| `POST` | `/api/actions/build-numeric-tiles` | Start or perform build numeric tiles. |
| `POST` | `/api/actions/clear-local-data` | Start or perform clear local data. |
| `GET` | `/api/actions/import-terrain` | Return status for import terrain. |
| `POST` | `/api/actions/import-terrain` | Start or perform import terrain. |
| `POST` | `/api/actions/import-viewport` | Start or perform import viewport. |
| `GET` | `/api/actions/los-rosette` | Return status for los rosette. |
| `POST` | `/api/actions/los-rosette` | Start or perform los rosette. |
| `POST` | `/api/actions/rebuild-display-tiles` | Start or perform rebuild display tiles. |
| `GET` | `/api/config` | Return the current service configuration. |
| `PUT` | `/api/config` | Validate and persist service configuration. |
| `GET` | `/api/elevation` | Return elevation. |
| `GET` | `/api/kody/manifest` | Return the Kody capability manifest. |
| `DELETE` | `/api/los-rosette` | Clear los rosette. |
| `GET` | `/api/los-rosette` | Return los rosette. |
| `GET` | `/api/palette` | Return palette. |
| `GET` | `/api/raster/revision` | Return raster revision. |
| `GET` | `/api/raster/{zoom}/{x}/{y}` | Return raster tile. |
| `POST` | `/api/terrain/profile` | Create terrain profile. |
| `GET` | `/api/workspace` | Return the structured workspace model. |

## Request models and response formats

The endpoint implementation validates request bodies and query parameters with its service models. For Kody-facing data, configuration, and tile contracts, see the service manifest and the repository [API specification](../../API_SPEC.md).
