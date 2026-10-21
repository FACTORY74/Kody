# Kody Copernicus map service

Copernicus downloads the latest Sentinel-2 L2A true-color Global Mosaic for
the current Kody viewport and stores it as local 256px Web-Mercator quad tiles.
Downloaded tiles remain available through `/api/raster` until cleared.

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

Credentials are held only in the running service's memory. The configuration
read endpoint never returns the client secret, and no credential is written to
the local tile cache.
