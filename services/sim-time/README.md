# Kody SimTimeControl service

SimTimeControl is a workspace-only service that owns a simple monotonic
simulation clock. It exposes pause, resume, and reset controls through Kody's
standard `simulationControls` capability and broadcasts state over
server-sent events; it does not publish map tiles or data features.

```powershell
Set-Location services\sim-time
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\src"
python -m uvicorn kody_sim_time.sim_time_service:app --host 127.0.0.1 --port 8012
```

`GET /api/sim-time` returns server wall-clock UTC, elapsed seconds, update
rate, lifecycle state, and revision. The default update rate is one update per
second. `PUT /api/sim-time` accepts one of
`{"action": "start" | "pause" | "resume" | "stop"}` with the current
`If-Match` revision. `PUT /api/sim-time/update-rate` accepts
`{"updatesPerSecond": 1..60}` with the same revision header; higher rates
produce smoother Tracks updates but use more network and rendering resources.
`POST /api/sim-time/reset` sets elapsed time to zero and stops the clock.

`GET /api/sim-time/events` is a `text/event-stream` endpoint. A new
subscriber receives a `state` event immediately, then receives ordered
`started`, `paused`, `resumed`, `stopped`, and `reset` events. It also
receives a `sync` event at the configured update rate. Every
event body is the current simulation state, and transition events include an
SSE event ID for reconnection.

## REST API

See the complete endpoint inventory in [API.md](./API.md).
