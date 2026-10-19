# Kody user guide

## Start Kody

After installing the requirements for the Kody product and its enabled
services into the same Python environment, open PowerShell at the repository
root and run:

```powershell
python .\start_kody.py
```

This starts the Kody browser application, its configuration service, and every
enabled microservice. Press `Ctrl+C` in that window to stop them all.

Use the following command to inspect the enabled services, ports, and URLs
without starting them:

```powershell
python .\start_kody.py --list
```

Runtime options, including which services start, are in
[`config/kody.json`](./config/kody.json). Each
service writes a rolling log to `logs\<service-id>\service.log`; the default
retention is approximately 30 minutes.
Only one launcher instance may run at a time. If Kody is already running,
stop it with `Ctrl+C` in its launcher window before running
`start_kody.py` again.

Once started, use **Service control** to manage connections. On the map, use
the dark **Layers** panel to toggle base map, reference, and attached data
sources; the compact cog control opens an attached service's own settings
dialog. Vector imports merge into one native on-disk tile hierarchy at
`services\vecmap\maps\tiles\<zoom>\<x>\<y>.geojson.gz`; the map reads the
requested tile directly rather than searching among import folders. Its **Show
major roads**, **Show urban areas**, **Show woodland**, and **Show placenames**
checkboxes control those imported Vector map layers independently. Placenames
include named cities, towns, villages, and hamlets at progressively closer
zoom levels.
Place regional `.osm.pbf`, `.gpkg`, or `.gpkg.zip` files in the Vector
`imports` folder and use **Import all Vector sources** to rebuild the native
tile store. Keep `land-polygons-split-4326.zip` there as supporting land-fill
data; it is not imported as a map layer.

The **Lat/Lon Grid** uses square Web Mercator cells so horizontal and vertical
lines represent the same projected ground distance. It automatically selects
a readable spacing with approximately 5–10 cells across the map, while labels
continue to show latitude and longitude. The top-bar **Grid Size** readout
shows one current cell's latitude/longitude spans in degrees and estimated
kilometres while the Lat/Lon Grid is visible.

Right-click the map to open its **context menu**. Services register their
available context-menu actions when they start. With Terrain configured,
**Create LOS Rosette** creates a background terrain line-of-sight overlay from
the selected point; **Clear LOS Rosette** removes it. Configure its bearing
count, metre-based sample/limit distances, and local observer height from the
Terrain cog. The observer height defaults to 2 metres.

The context menu has two levels: first select a plugin (such as **Map** or
**Terrain**), then select one of its submitted actions. The Map plugin offers
**Measure** actions: select **From Here**, right-click the destination and
select **To Here**, then read the kilometre distance drawn between the two map
pins. The route follows the great circle, so long routes curve on the Mercator
map. **Clear** removes the measurement. Its kilometre precision adapts to the
current zoom level.

The **ADS-B live flights** and **AIS vessels** plugins both offer **Set Origin
Here**, which moves that service's local search/subscription centre to the
right-clicked map point.

The required map connections are:

```text
Vector map:    http://127.0.0.1:8004/
Satellite map: http://127.0.0.1:8006/
```

Optional services can be added, stopped, or removed without affecting base-map
operation:

| Service | Type | URL |
| --- | --- | --- |
| Static Shapes | Shapes | `http://127.0.0.1:8002/` |
| Live Tracks | GeoJSON | `http://127.0.0.1:8003/` |
| Live ADS-B | ADS-B | `http://127.0.0.1:8005/` |

The map Layers pane groups controls as **Map**, **Data**, and **Live**. The
Map category contains Satellite imagery, Vector map, and the latitude/longitude
grid. Each has a fixed-width **01–10** opacity control, where 01 is 10% and
10 is 100%. An unavailable optional endpoint is reported as an overlay
message; its failure does not disable map panning, zooming, imagery, or other
available overlays.
