# Kody Shapes service

This optional service owns static, named and coloured GeoJSON overlays and its
truth data at `data/shapes.geojson`.

```powershell
Set-Location services\shapes
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\src"
python -m uvicorn kody_shapes.shapes_service:app --host 127.0.0.1 --port 8002
```

Register `http://127.0.0.1:8002/` as an optional **Shapes** connection. The
central product continues working if this service is absent.
