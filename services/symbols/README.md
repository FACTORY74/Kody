# Kody Symbols Service

This service supplies named SVG map symbols at `/api/symbols`. It reads its
catalogue from `<dataDirectory>\symbols\symbols.json` and reads each referenced
SVG file from that same folder. It does not generate symbol artwork.

Each JSON entry has a string `name`, an SVG `file` name, and the default
rendering flags `orientation` and `colorMode`:

```json
{
  "name": "surveillance-aircraft",
  "file": "aircraft.svg",
  "orientation": "orientable",
  "colorMode": "colorable",
  "scale": 2
}
```

`name` is the identifier used by feature data and is independent of the SVG
filename. `orientation` is either `orientable` (rotated by a feature's
`bearingDegrees`) or `alwaysup` (kept upright on screen). `colorMode` is
either `colorable` (the SVG uses `currentColor` and inherits a feature's
`color`) or `fixed` (the SVG retains its own palette). The starter data folder
includes the default `symbols.json` and SVG assets. `scale` is a positive
number that multiplies Kody's 28-pixel base marker size; the starter symbols
use `2` for 56-pixel markers. An empty symbols directory is valid and
publishes an empty catalogue.

Run it on port 8007:

```powershell
Set-Location services\symbols
$env:PYTHONPATH = "$PWD\src"
python -m uvicorn kody_symbols.symbol_service:app --host 127.0.0.1 --port 8007
```

Register `http://127.0.0.1:8007/` as a **Symbols** connection.

## AIS vessel symbols

The catalogue includes `ship` as the generic vessel fallback and colorable,
course-orientable symbols for `sailboat`, `yacht`, `fishing_boat`, `tug`,
`pilot_boat`, `rescue_boat`, `passenger_ship`, `cargo_ship`, `tanker`,
`high_speed_craft`, `military_ship`, `law_enforcement_boat`, and
`service_boat`. AIS category mapping can select these names when static vessel
type data is available.

## REST API

See the complete endpoint inventory in [API.md](./API.md).
