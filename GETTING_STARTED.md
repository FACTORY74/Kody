# Getting started with starter map data

This guide imports the sample satellite imagery, terrain elevation, and
country-boundary vector data included in
[`data_starter`](./data_starter/). The default configuration already selects
that folder as Kody's data directory. The full paths below are examples for
the supplied starter data; replace them with the full paths to your own source
files when importing different data.

## 1. Install requirements

From the repository root, create and activate a Python virtual environment,
then install the requirements for the Kody application and all services:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
Get-ChildItem -Path .\kody, .\services -Recurse -Filter requirements.txt |
    ForEach-Object { python -m pip install -r $_.FullName }
```

## 2. Start Kody

From the repository root, run:

```powershell
python .\start_kody.py
```

Open <http://127.0.0.1:8000/> in a browser. In the map's **Layers** panel,
enable **Satellite imagery**, **Terrain**, and **Vector map** as required.
Use each layer's opacity control to blend the data for the current analysis.

## 3. Import satellite imagery

Open the **Satellite map** cog, enter one of the following full PNG paths in
**PNG filename**, keep **Rebuild impacted overview tiles** enabled, then select
**Process image**:

```text
C:\Util\GithubRepos\Kody\data_starter\imports\MercatorWorld_10kmpp.png
C:\Util\GithubRepos\Kody\data_starter\imports\BritishIsles_2500mpp.png
C:\Util\GithubRepos\Kody\data_starter\imports\Portsmouth_100mpp.png
```

Import from broadest to most detailed coverage. Each PNG must remain beside
its matching world file (`.pgw` or `.pngw`) and projection file (`.prj`).
The starter images already have these companion files:

```text
MercatorWorld_10kmpp.pgw / MercatorWorld_10kmpp.prj
BritishIsles_2500mpp.pgw / BritishIsles_2500mpp.prj
Portsmouth_100mpp.pgw / Portsmouth_100mpp.prj
```

Wait for each background import to finish before starting the next one. Kody
uses the most detailed imagery available at each location and zoom level.

## 4. Import terrain elevation

Open the **Terrain** cog and enter a full path in **Terrain filename in
imports**. Import the following files, one at a time, using **Import terrain
file**:

```text
C:\Util\GithubRepos\Kody\data_starter\imports\SouthernUK_500mpp.asc
C:\Util\GithubRepos\Kody\data_starter\imports\NorthWestWales_50mpp.asc
```

Set **ArcASCII projection** to **web-mercator** for `SouthernUK_500mpp.asc`
and **world-mercator** for `NorthWestWales_50mpp.asc`. Wait for each import to
complete.
The Terrain service mosaics the packages automatically: where they overlap,
the 50 m North West Wales source replaces the 500 m Southern UK source.

## 5. Import the vector map

Open the **Vector map** cog and confirm the public archive URLs are set:

```text
https://www.factory74.com/permenant/kody/NaturalEarth/10m_physical.zip
https://www.factory74.com/permenant/kody/NaturalEarth/10m_cultural.zip
```

Select **Import Natural Earth dataset** and wait for the import to finish. Kody
downloads, validates, and loads the physical and cultural 1:10m archives into
the offline vector library.

## Result

The map now combines global/UK/Portsmouth satellite imagery, a terrain mosaic
with higher-resolution coverage in North West Wales, and the Natural Earth
physical, cultural, and administrative layers. Kody keeps the original archive
files and stores the processed service data beneath `data_starter`.
