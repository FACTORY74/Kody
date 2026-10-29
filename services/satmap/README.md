# Kody satellite map service

This service owns and serves local raster satellite imagery at `/api/raster`.

```powershell
Set-Location services\satmap
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\src"
python -m uvicorn kody_satmap.server:app --host 127.0.0.1 --port 8006
```

Register `http://127.0.0.1:8006/` as a **Satellite map** connection.

The Satellite map cog accepts a PNG filename and processes it in the
background. The PNG must have a sibling `.pgw` or `.pngw` world file and a
`.prj` file declaring `Mercator_Auxiliary_Sphere`; processed tiles are added
to this service's local `data/satellite/` library. Each processed image also
updates transparent lower-zoom ancestor tiles, keeping its footprint visible
at wider zoom levels before it resolves to native detail.

The **Rebuild impacted overview tiles** checkbox controls this per-import,
affected-area-only update. Leave it enabled for continuous presentation;
disable it when staging native imagery without generating its previews.

Use **Build overview tiles** from the Satellite map cog to create the same
ancestor pyramid for imagery already present in the library.

## REST API

See the complete endpoint inventory in [API.md](./API.md).
