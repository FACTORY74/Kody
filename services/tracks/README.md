# Kody Tracks service

This optional service owns the live GeoJSON track simulation. It has no
dependency on the map service or central product source trees.

```powershell
Set-Location services\tracks
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\src"
python -m uvicorn kody_tracks.track_service:app --host 127.0.0.1 --port 8003
```

Register `http://127.0.0.1:8003/` as an optional **GeoJSON** connection. It
publishes 21 current-position Points and five-minute LineString tails.
