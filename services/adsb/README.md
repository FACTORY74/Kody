# Kody ADS-B service

This optional microservice polls ADSB.lol for a bounded local radius, keeps
aircraft observations in a configurable local trail cache, and exposes Kody
GeoJSON at `/api/geojson`.

```powershell
Set-Location services\adsb
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\src"
python -m uvicorn kody_adsb.adsb_service:app --host 127.0.0.1 --port 8005
```

Register `http://127.0.0.1:8005/` as an optional **ADS-B** service in Kody.
The defaults request a 25 km radius centred on the Isle of Wight every 10
seconds. Set `KODY_ADSB_LATITUDE`, `KODY_ADSB_LONGITUDE`, and
`KODY_ADSB_RADIUS_KM` before startup to change the bounded request area. The
radius is restricted to 1–250 km, upstream failures are logged and use
exponential backoff, and the most recent cached observations remain available.

Kody always presents the radius in kilometres. ADSB.lol's point endpoint uses
nautical miles, so the service converts the configured kilometre radius before
making its upstream request; the optional coverage boundary therefore matches
the actual ADS-B query area.

The Kody service configuration dialog also controls **Track length** (10–3,600
seconds, default 300) and can fade each ADS-B trail line linearly to fully
transparent at its oldest point.

When the central product has published a viewport, the service reads the
shared `GET http://127.0.0.1:8001/api/context/viewport` resource and returns
only aircraft and trail geometry intersecting that viewport plus a 5 km safety
margin. Set `KODY_VIEWPORT_CONTEXT_URL` to use a different central service
address.

Use **ADS-B live flights → Set Origin Here** from the map context menu to move
the local ADS-B search centre to the selected map point.
