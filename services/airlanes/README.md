# Kody AirLanes Service

AirLanes polls the official [NATS Digital Datasets](https://nats-uk.ead-it.com/cms-nats/opencms/en/Publications/digital-datasets/)
page every 24 hours by default. It discovers the currently effective LAC/PC
sector KML/KMZ download and UK ICAO AIP full KML/KMZ download rather than
embedding an AIRAC-specific URL. The last successful combined GeoJSON result
is cached locally, so temporary NATS outages do not remove visible geometry.

The service exposes:

- optional dashed ATC-sector boundary lines from the LAC/PC sector dataset;
- translucent CTA, CTR, Danger, Prohibited, and Restricted polygons from the
  UK ICAO AIP folder hierarchy, carrying category, source, URL, and hierarchy
  metadata.

The AirLanes configuration dialog persists **Show ATC sector boundaries** and
**Show controlled and special-use airspace** independently. `/api/geojson`
returns only enabled categories and names them in `enabledCategories`.

This is static published digital-dataset geometry. It does **not** provide or
make claims about NOTAM information unless **Show NATS UK PIB NOTAMs** is
enabled. That opt-in third source polls the public
[NATS UK PIB XML](https://pibs.nats.co.uk/operational/pibs/PIB.xml) hourly by
default. It displays only notices with an explicit Q-line centre and radius;
notices without reliable geometry are retained out of the map rather than
being guessed. To avoid a cluttered map, it further displays only bounded
warning and restriction Q-codes of 50 nautical miles or less, without automatic
labels; the full parsed bulletin remains in the local NOTAM cache. NATS states
that the PIB XML has no declared schema and may change with minimal notice, so
the map exposes source freshness and parsing errors as best-effort reference
information. When a notice explicitly defines an area bounded by coordinates
in `ItemE`, AirLanes renders that polygon; otherwise it falls back to the
Q-line centre/radius circle.

Run it on port 8009:

```powershell
Set-Location services\airlanes
$env:PYTHONPATH = "$PWD\src"
python -m uvicorn kody_airlanes.airlanes_service:app --host 127.0.0.1 --port 8009
```
