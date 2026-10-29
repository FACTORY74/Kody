# Kody InteractionServer

InteractionServer receives ShapeServer containment callbacks at
`POST /api/interactions`. It displays each active track/shape pair as a
500 metre circle for one second through `GET /api/interactions/geojson`.

ShapeServer polls TrackSim and sends every matching point through its bounded
event queue. Its configured output URL defaults to this service:
`http://127.0.0.1:8017/api/interactions`.

```powershell
Set-Location services\interaction-server
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\src"
python -m uvicorn kody_interaction_server.interaction_server:app --host 127.0.0.1 --port 8017
```

## REST API

See the complete endpoint inventory in [API.md](./API.md).
