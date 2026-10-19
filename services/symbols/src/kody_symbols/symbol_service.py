"""Named SVG definitions for map platform and annotation symbols."""

from __future__ import annotations

import hashlib
import json

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def svg(path: str, color_mode: str = "colorable") -> str:
    if color_mode == "colorable":
        path = path.replace("#ffffff", "currentColor").replace("#222222", "currentColor")
    stroke = "currentColor" if color_mode == "colorable" else "#ffffff"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" '
        f'fill="none" stroke="{stroke}" stroke-width="2" stroke-linecap="round" '
        f'stroke-linejoin="round">{path}</svg>'
    )


def vessel_symbol(name: str, path: str) -> dict[str, str]:
    return {"name": name, "orientation": "orientable", "colorMode": "colorable", "svg": svg(path)}


SYMBOLS = (
    {
        "name": "plane",
        "orientation": "orientable",
        "colorMode": "colorable",
        "svg": svg('<path fill="#ffffff" d="M16 1c2 0 3 2 3 5v8l8 4c2 1 2 4 0 5l-8-2v8l3 2v2l-6-1-6 1v-2l3-2v-8l-8 2c-2-1-2-4 0-5l8-4V6c0-3 1-5 3-5z"/>'),
    },
    {
        "name": "ship",
        "orientation": "orientable",
        "colorMode": "colorable",
        "svg": svg('<path fill="#ffffff" d="M16 2 7 10v15l4 4h10l4-4V10z"/><path d="M11 10h10v6H11zM9 19h14M10 24h12M13 5h6M16 2v8M12 29v2M20 29v2"/>'),
    },
    vessel_symbol(
        "sailboat",
        '<path fill="#ffffff" d="M16 2 7 25h18z"/><path d="M16 2v23M16 7l8 16h-8M15 12 9 23h6M7 25h18M16 25v5"/>',
    ),
    vessel_symbol(
        "yacht",
        '<path fill="#ffffff" d="M16 3 8 10v15l4 3h8l4-3V10z"/><path d="M12 10h8l2 6H10zM11 19h10M13 23h6M16 3v7"/>',
    ),
    vessel_symbol(
        "fishing_boat",
        '<path fill="#ffffff" d="M16 3 8 11v14l4 3h8l4-3V11z"/><path d="M12 11h8v7h-8zM10 22h12M8 14l4 4M24 14l-4 4M22 7v10l5 3M10 25l-2 3M22 25l2 3"/>',
    ),
    vessel_symbol(
        "tug",
        '<path fill="#ffffff" d="M16 5 8 12v13l4 3h8l4-3V12z"/><path d="M11 13h10v8H11zM16 5v8M10 24h12M7 25h3M22 25h3M12 29c1-2 3-2 4 0 1-2 3-2 4 0"/>',
    ),
    vessel_symbol(
        "pilot_boat",
        '<path fill="#ffffff" d="M16 3 10 10v15l3 3h6l3-3V10z"/><path d="M13 11h6v8h-6zM11 22h10M16 3v8M10 25l-2 3M22 25l2 3"/>',
    ),
    vessel_symbol(
        "rescue_boat",
        '<path fill="#ffffff" d="M16 3 9 10v15l4 3h6l4-3V10z"/><path d="M12 11h8v8h-8zM16 14v5M13.5 16.5h5M11 23h10M16 3v8"/>',
    ),
    vessel_symbol(
        "passenger_ship",
        '<path fill="#ffffff" d="M16 2 7 10v15l4 3h10l4-3V10z"/><path d="M11 9h10v5H11zM9 16h14M9 20h14M11 24h10M13 5h6"/>',
    ),
    vessel_symbol(
        "cargo_ship",
        '<path fill="#ffffff" d="M16 2 7 10v15l4 3h10l4-3V10z"/><path d="M10 11h5v6h-5zM17 11h5v6h-5zM10 19h5v6h-5zM17 19h5v6h-5zM13 5h6v4"/>',
    ),
    vessel_symbol(
        "tanker",
        '<path fill="#ffffff" d="M16 2 7 10v15l4 3h10l4-3V10z"/><circle cx="12" cy="16" r="3"/><circle cx="20" cy="16" r="3"/><circle cx="12" cy="23" r="2"/><circle cx="20" cy="23" r="2"/><path d="M16 4v7"/>',
    ),
    vessel_symbol(
        "high_speed_craft",
        '<path fill="#ffffff" d="M13 3 8 10v16l4 2 4-7 4 7 4-2V10l-5-7z"/><path d="M13 11h6v6h-6zM8 20h5M19 20h5M16 3v8M9 28l-2 2M23 28l2 2"/>',
    ),
    vessel_symbol(
        "military_ship",
        '<path fill="#ffffff" d="M16 2 7 11v14l4 3h10l4-3V11z"/><path d="M12 11h8v6h-8zM16 6v5M13 6h6M16 17v6M12 23h8M8 15h3M21 15h3"/>',
    ),
    vessel_symbol(
        "law_enforcement_boat",
        '<path fill="#ffffff" d="M16 3 9 10v15l4 3h6l4-3V10z"/><path d="M12 11h8v7h-8zM16 14v3M14.5 15.5h3M11 22h10M16 3v8"/>',
    ),
    vessel_symbol(
        "service_boat",
        '<path fill="#ffffff" d="M16 3 8 11v14l4 3h8l4-3V11z"/><path d="M12 12h8v7h-8zM10 23h12M21 9v13l4 3M21 12l-4 4M16 3v9"/>',
    ),
    {
        "name": "heli",
        "orientation": "orientable",
        "colorMode": "colorable",
        "svg": svg('<path fill="#ffffff" d="M16 3c4 0 6 4 6 8v6l2 7h-3l-2-5h-6l-2 5H8l2-7v-6c0-4 2-8 6-8z"/><path fill="#ffffff" d="M14 24h4v5h-4zM12 29h8v2h-8z"/><path d="M4 16h24M6 8l20 16M26 8 6 24"/><circle fill="#222222" cx="16" cy="16" r="2"/>'),
    },
    {
        "name": "balloon",
        "orientation": "alwaysup",
        "colorMode": "colorable",
        "svg": svg('<path fill="#ffffff" d="M16 3c-6 0-10 5-10 11 0 5 3 9 7 11l1 4h4l1-4c4-2 7-6 7-11 0-6-4-11-10-11z"/><path d="M11 14h10M13 29h6"/><path fill="#222222" d="M13 17h6l-1 5h-4z"/>'),
    },
    {
        "name": "undefined",
        "orientation": "alwaysup",
        "colorMode": "fixed",
        "svg": svg('<circle fill="#ffffff" cx="16" cy="16" r="12"/><path stroke="#222222" stroke-width="3" d="M12 12a4 4 0 1 1 6 3c-2 1-2 2-2 4M16 24h.01"/>', "fixed"),
    },
    {
        "name": "car",
        "orientation": "orientable",
        "colorMode": "colorable",
        "svg": svg('<path fill="#ffffff" d="M9 5h14l3 9v11H6V14z"/><path d="M6 14h20M10 25v3M22 25v3"/>'),
    },
    {
        "name": "map_pin",
        "orientation": "alwaysup",
        "colorMode": "colorable",
        "svg": svg('<path fill="#ffffff" d="M16 29s10-9 10-17a10 10 0 0 0-20 0c0 8 10 17 10 17z"/><circle fill="#222222" cx="16" cy="12" r="3"/>'),
    },
    {
        "name": "crosshair",
        "orientation": "alwaysup",
        "colorMode": "colorable",
        "svg": svg('<circle cx="16" cy="16" r="7"/><path d="M16 2v7M16 23v7M2 16h7M23 16h7"/>'),
    },
)

COLLECTION = {"symbols": SYMBOLS}
REVISION = hashlib.sha256(
    json.dumps(COLLECTION, separators=(",", ":"), sort_keys=True).encode("utf-8")
).hexdigest()

app = FastAPI(title="Kody Symbols Service", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_methods=["GET"],
    allow_headers=["Content-Type"],
)


@app.get("/api/kody/manifest")
def get_manifest() -> dict[str, object]:
    return {
        "kodyApiVersion": "1",
        "serviceId": "symbols",
        "displayName": "Symbols",
        "dataEndpoint": {"path": "/api/symbols", "format": "kody-symbols"},
        "configuration": {"readPath": "/api/config", "writePath": "/api/config", "fields": []},
    }


@app.get("/api/config")
def get_configuration() -> dict[str, str]:
    return {"revision": REVISION}


@app.get("/api/symbols")
def get_symbols() -> dict[str, object]:
    return {"revision": REVISION, **COLLECTION}
