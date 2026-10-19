"""File-backed SVG symbol catalogue for the Kody map."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


ROOT_DIRECTORY = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get("KODY_DATA_DIRECTORY", ROOT_DIRECTORY / "data"))
SYMBOLS_DIRECTORY = DATA_ROOT / "symbols"
CATALOGUE_FILE = SYMBOLS_DIRECTORY / "symbols.json"
ORIENTATIONS = {"orientable", "alwaysup"}
COLOR_MODES = {"colorable", "fixed"}


def initialize_data_directory() -> None:
    SYMBOLS_DIRECTORY.mkdir(parents=True, exist_ok=True)


initialize_data_directory()


def catalogue_error(message: str) -> RuntimeError:
    return RuntimeError(f"symbols catalogue is invalid: {message}")


def symbol_file(filename: object) -> Path:
    if not isinstance(filename, str) or not filename:
        raise catalogue_error("each symbol requires a non-empty file name")
    path = (SYMBOLS_DIRECTORY / filename).resolve()
    if path.parent != SYMBOLS_DIRECTORY.resolve() or path.suffix.lower() != ".svg":
        raise catalogue_error(f"symbol file must be an SVG directly in the symbols directory: {filename}")
    if not path.is_file():
        raise catalogue_error(f"symbol file is missing: {filename}")
    return path


def load_symbols() -> tuple[dict[str, object], ...]:
    if not CATALOGUE_FILE.is_file():
        return ()
    try:
        catalogue = json.loads(CATALOGUE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise catalogue_error(f"cannot read {CATALOGUE_FILE.name}") from error
    definitions = catalogue.get("symbols") if isinstance(catalogue, dict) else None
    if not isinstance(definitions, list):
        raise catalogue_error("symbols.json must contain a symbols array")

    symbols: list[dict[str, object]] = []
    names: set[str] = set()
    for index, definition in enumerate(definitions):
        if not isinstance(definition, dict):
            raise catalogue_error(f"symbol {index} must be an object")
        name = definition.get("name")
        orientation = definition.get("orientation")
        color_mode = definition.get("colorMode")
        scale = definition.get("scale")
        if not isinstance(name, str) or not name.strip():
            raise catalogue_error(f"symbol {index} requires a non-empty string name")
        if name in names:
            raise catalogue_error(f"symbol names must be unique: {name}")
        if orientation not in ORIENTATIONS:
            raise catalogue_error(f"symbol {name} has an invalid orientation")
        if color_mode not in COLOR_MODES:
            raise catalogue_error(f"symbol {name} has an invalid colorMode")
        if (
            not isinstance(scale, (int, float))
            or isinstance(scale, bool)
            or not 0 < scale <= 10
        ):
            raise catalogue_error(f"symbol {name} has an invalid scale")
        source = symbol_file(definition.get("file"))
        try:
            svg = source.read_text(encoding="utf-8")
        except OSError as error:
            raise catalogue_error(f"cannot read symbol file: {source.name}") from error
        if "<svg" not in svg:
            raise catalogue_error(f"symbol file is not SVG: {source.name}")
        names.add(name)
        symbols.append(
            {
                "name": name,
                "orientation": orientation,
                "colorMode": color_mode,
                "scale": scale,
                "svg": svg,
            }
        )
    return tuple(symbols)


def collection() -> dict[str, object]:
    return {"symbols": load_symbols()}


def revision(symbols: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(symbols, separators=(",", ":"), sort_keys=True).encode("utf-8")
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
    return {"revision": revision(collection())}


@app.get("/api/symbols")
def get_symbols() -> dict[str, object]:
    symbols = collection()
    return {"revision": revision(symbols), **symbols}
