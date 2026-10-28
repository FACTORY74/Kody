# Kody ESA WorldCover 2021 service

ESA WorldCover 2021 provides public 10 m global land-cover data. **Download
current viewport** reads only the needed Cloud-Optimized GeoTIFF windows from
ESA's public source, renders them at the current useful map resolution (up to
zoom 14), builds transparent parent tiles down to zoom 0, and retains the
resulting lossless WebP Web-Mercator tiles in the SQLite-backed sparse
quadtree at `KODY_DATA_DIRECTORY\worldcover\tiles\tiles.sqlite`. It does not
retain the much larger 3° source COGs, so later map use is fully local.

```powershell
Set-Location services\worldcover
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\src"
python -m uvicorn kody_worldcover.server:app --host 127.0.0.1 --port 8014
```
