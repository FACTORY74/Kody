# Kody Symbols Service

This service supplies named SVG map symbols at `/api/symbols`. Each definition
declares either `orientable` (rotated by a feature's `bearingDegrees`) or
`alwaysup` (kept upright on screen). Definitions also declare `colorMode`:
`colorable` symbols inherit a feature's `color`, while `fixed` symbols retain
their built-in SVG palette.

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
