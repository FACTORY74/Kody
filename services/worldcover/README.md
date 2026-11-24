# Kody ESA WorldCover 2021 service

ESA WorldCover 2021 provides public 10 m global land-cover data. **Download
current viewport** downloads each required 3° source as its original GeoTIFF
format into `KODY_DATA_DIRECTORY\worldcover\native-sources.sqlite`. Requested
map tiles are retained as lossless WebP in
`KODY_DATA_DIRECTORY\worldcover\tiles\tiles.sqlite`.

The service configuration dialog has a **Generated zoom levels** text field.
Enter comma-separated levels from 0 through 14 (for example `6, 8, 10`).
WorldCover has two separate operator stages:

1. Select **Download current viewport** to download and store the required
   native ESA GeoTIFF source files. This action never renders browser tiles.
2. Set **Generated zoom levels** and select **Save** to configure which zoom
   levels may be rendered. Save does not generate tiles.

As the enabled map layer requests visible tiles at configured zoom levels, the
service renders only each requested tile and stores it as WebP. It does not
pre-render the viewport or generate unused tiles. Changing the zoom-level
configuration changes which zoom the map requests. Tile rendering reads only
the locally stored native GeoTIFF sources; map tile requests never download
from ESA. The service reports tile-queue progress through its REST activity
status endpoint; it is not shown on the map layer controls.

WorldCover displays only exact tiles at the selected configured zoom. It does
not substitute lower-zoom cached tiles when a requested tile is unavailable;
areas without a stored native source remain uncovered until the explicit
download action stores that source. Newly downloaded sources invalidate only
cached map tiles that overlap them; those tiles are regenerated when requested.
The derived WebP cache is versioned, and rendering changes use a separate cache
namespace so previous derived tiles remain untouched.

ESA does not publish a GeoTIFF for every 3° cell (for example, empty ocean
areas). A source-file `404` is recorded as an unavailable cell and skipped;
it does not fail the rest of the viewport download. Other HTTP and network
errors remain visible as download failures.

```powershell
Set-Location services\worldcover
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r ..\..\requirements.txt
$env:PYTHONPATH = "$PWD\src"
python -m uvicorn kody_worldcover.server:app --host 127.0.0.1 --port 8014
```

## REST API

See the complete endpoint inventory in [API.md](./API.md).
