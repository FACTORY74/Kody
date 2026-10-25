# Kody ESA Download Terrain service

ESA Download Terrain retrieves Copernicus DEM GLO-30 through the Copernicus
Data Space DEM Process API. Configure a dedicated Copernicus client ID and
client secret in its layer cog; the secret is stored only in the local,
Git-ignored `config\esa_dem.json` file and is never returned by its API.
GLO-30 access through this API requires that the Copernicus account is
registered for **Copernicus Contributing Missions (CCM)**. Create the client
credentials after that registration and enter that separate pair here; normal
imagery-only credentials receive an explicit CCM guidance error.

**Download current viewport** stores local elevation tiles at the map's
current display zoom. **Download viewport at GLO-30 resolution** stores them
at the approximately 30 m source resolution and asks for confirmation for
large areas. Downloaded data include numerical EGM2008-height samples as well
as rendered local terrain tiles, so `/api/elevation` and
`/api/terrain/profile` remain available without another remote request.

Copernicus DEM attribution: © DLR e.V. 2010-2014 and © Airbus Defence and
Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA;
all rights reserved.

```powershell
Set-Location services\esa-dem
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\src"
python -m uvicorn kody_esa_dem.server:app --host 127.0.0.1 --port 8013
```
