# Kody Terrain service

The Terrain service imports a local geographic WGS 84 or Web Mercator ArcASCII Grid and serves
colourised topographical PNG XYZ tiles at `/api/raster/{z}/{x}/{y}` on port
`8010`. It is entirely key-free and standalone.

```powershell
Set-Location services\terrain
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\src"
python -m uvicorn kody_terrain.server:app --host 127.0.0.1 --port 8010
```

Copy one `.asc` or `.grd` file to `services\terrain\imports\`, enter its
filename in the Terrain cog, then select **Import terrain file**. The parser
requires `ncols`, `nrows`, `cellsize`, either `xllcorner`/`yllcorner` or
`xllcenter`/`yllcenter`, and honours optional `NODATA_value`. Imported source
and rendered pyramid are retained under `data\terrain`.

ArcASCII does not store CRS metadata. Use **ArcASCII projection** to choose
`geographic`, `web-mercator`, or `world-mercator` when the source is
projected; leave it as `auto` only for inputs whose coordinates unambiguously
identify their projection. For example, `NorthWales.asc` uses
`world-mercator`, which preserves its latitude alignment with its equivalent
GeoTIFF.

Every import writes 256×256 compressed numeric terrain chunks beside its
`source.asc`. These chunks support bounded elevation and LOS lookups without
loading a large source grid. For existing packages imported before this layout,
select **Build numeric terrain chunks** in the Terrain cog to convert them
without changing the original import file.

Terrain display tiles are generated at the zoom appropriate to each source
resolution (for example, 50 m at z12 and 10 m at z14), with lower zoom
overviews. After updating the tile builder, select **Rebuild terrain display
tiles** to regenerate existing package pyramids.

**Import viewport from local file** is deliberately not a remote download:
it reads the shared Kody viewport and imports only intersecting cells from the
configured local file. It reports a clear error when no source is supplied or
does not cover the viewport.

## LOS Rosette

Right-click a point on the map to open the **context menu**, then choose
**Create LOS Rosette**. Terrain samples radial lines from that point until a
sample is higher than the point of origin, reaches the configured distance
limit, or leaves available local terrain. The result is a GeoJSON overlay on
the map. Select **Clear LOS Rosette** from the same context menu to remove it.

Use the Terrain cog to set **LOS bearings**, **LOS sample distance (metres)**,
**LOS distance limit (metres)**, and **LOS observer height (metres)**. The
default is 72 bearings, 250 metre samples, a 20 kilometre limit, and a 2 metre
observer height. Only one LOS calculation can run at a time.

## REST API

See the complete endpoint inventory in [API.md](./API.md).
