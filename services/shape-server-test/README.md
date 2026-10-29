# Kody ShapeServerTest

ShapeServerTest is a sample producer for manual integration testing. It creates
a circle, rectangle, and four annular sectors in ShapeServer. It updates all
sectors at 5 Hz; each completes one full rotation every 10 seconds while its
start/end bearings and inner/outer radii vary continuously. The rectangle also
completes one full rotation every 10 seconds. Once per rotation, it deletes
then recreates a transient annular sector. It exposes `GET /api/status` on
port 8016.

It is enabled in the root launcher and targets `http://127.0.0.1:8015` by
default. Set `KODY_SHAPE_SERVER_URL` before launching it to target another
ShapeServer instance.

## REST API

See the complete endpoint inventory in [API.md](./API.md).
