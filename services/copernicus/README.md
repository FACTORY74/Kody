# Kody Copernicus map service

Copernicus downloads the latest Sentinel-2 L2A true-color Global Mosaic for
the current Kody viewport and stores it as local 256px Web-Mercator quad tiles.
Downloaded tiles remain available through `/api/raster` until cleared.
Completed local tiles and generated parent tiles are saved as WebP at quality
90 in `data_starter\copernicus\tiles\tiles.sqlite`. The database stores each
tile as a WebP BLOB, rather than as an individual filesystem object. On startup,
the service migrates any legacy WebP cache files into the database.

```powershell
Set-Location services\copernicus
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\src"
python -m uvicorn kody_copernicus.server:app --host 127.0.0.1 --port 8011
```

Register `http://127.0.0.1:8011/` as a **Copernicus** connection.

In the Copernicus layer cog, enter a Copernicus Data Space OAuth client ID and
client secret. Select **Download current viewport** to assess the browser's
published bounds and tile zoom, then request the latest available Global Mosaic
tile for each required quad tile. Downloads that exceed 64 requests require a
confirmation in the configuration dialog.

Each Process API source request is limited to a 10° × 10° geographic extent.
Larger local map tiles are divided into a uniform grid of source requests and
composited into one cached 256px tile.

Saving the configuration stores credentials in the local,
Git-ignored `config\copernicus.json` file. The configuration read endpoint
never returns the client secret, and no credential is written to the local tile
cache.

Leave **Rebuild tile pyramid after download** enabled to generate lower-zoom
parent tiles for newly downloaded imagery. This makes each downloaded area
visible while the map is zoomed out. Use **Rebuild Copernicus tile pyramid** to
build parent tiles for imagery already in the local cache.

**Download Viewport At 10m** covers the current viewport with zoom-14 local
tiles and requests imagery no finer than the Global Mosaic's native 10m
resolution. It then generates the lower-zoom tile pyramid when enabled.
