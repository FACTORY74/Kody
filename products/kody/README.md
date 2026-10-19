# Kody central product

This tree owns the browser UI, service configuration, and persisted user map
view. It is independent of every map-data or overlay implementation.

```powershell
Set-Location products\kody
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\src"
python -m uvicorn kody_app.app:app --host 127.0.0.1 --port 8000
```

In another PowerShell window, start the configuration service:

```powershell
Set-Location products\kody
$env:PYTHONPATH = "$PWD\src"
python -m uvicorn kody_app.config_service:app --host 127.0.0.1 --port 8001
```

To start this product and every enabled Kody microservice together, use
`python .\start_kody.py` from the repository root. The central
[`../../config/kody.json`](../../config/kody.json) runtime catalog controls the module,
working directory, port, enabled flag, and 30-minute rolling-log retention for
each process.

Use the **Service control** page to add or remove optional services. The map's
dark Layers panel provides Map, Data, and Live controls, including independent
Vector map and Satellite map attachments. GeoJSON and Shapes failures are
isolated and shown as overlay availability messages rather than preventing map
operation.
