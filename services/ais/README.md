# Kody AIS Vessel Service

This service subscribes server-side to [OpenSeaFeed](https://openseafeed.com/api-docs)
and exposes bounded live vessel GeoJSON at `/api/geojson`. It uses the no-key
OpenSeaFeed free tier; coverage is community/terrestrial AIS and is therefore
strongest in coastal areas.

```powershell
Set-Location services\ais
pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\src"
python -m uvicorn kody_ais.ais_service:app --host 127.0.0.1 --port 8008
```

Register `http://127.0.0.1:8008/` as an **AIS vessels** connection.

Position reports are merged with enriched metadata and AIS static/voyage
messages by MMSI. When an AIS vessel type is supplied, its GeoJSON track uses
the corresponding Symbols-service name: sailing, yacht, fishing, tug, pilot,
rescue, passenger, cargo, tanker, high-speed, military, law-enforcement, or
service vessel. The generic `ship` symbol remains the fallback for missing or
unknown type data.

Use **AIS vessels → Set Origin Here** from the map context menu to move the
local AIS subscription centre to the selected map point.

OpenSeaFeed receives a rectangular subscription envelope. Kody draws that same
dotted rectangular boundary and filters returned positions and trails to it, so
the visible vessel area matches the subscribed service area exactly. The
**Subscription half-size (km)** setting is the distance from the configured
centre to each north/south edge; longitude span is adjusted for latitude.
