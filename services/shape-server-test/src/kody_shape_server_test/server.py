"""A small process that continuously exercises the ShapeServer CRUD API."""

from __future__ import annotations

import asyncio
import json
import math
import os
from contextlib import asynccontextmanager, suppress
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from fastapi import FastAPI

SHAPE_SERVER_URL = os.environ.get("KODY_SHAPE_SERVER_URL", "http://127.0.0.1:8015").rstrip("/")
UPDATES_PER_SECOND = 5
ROTATION_SECONDS = 10


def request(method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, object] | None:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    with urlopen(Request(f"{SHAPE_SERVER_URL}{path}", data=body, headers=headers, method=method), timeout=3) as response:
        return json.load(response) if response.length != 0 else None


async def exercise_shapes() -> None:
    static_shapes = [
        {"id": "shape-server-test-circle", "type": "circle", "name": "Test circle", "center": [-3.3808, 53.1348], "radius": 4_000},
        {"id": "shape-server-test-rectangle", "type": "rectangle", "name": "Rotating test rectangle", "origin": [-3.32, 53.13], "width": 6_000, "height": 2_000, "angle": 0},
    ]
    sectors = [
        {"id": "shape-server-test-sector-north", "type": "annularSector", "name": "North spinning sector", "center": [-3.45, 53.15], "innerRadius": 700, "outerRadius": 4_000, "startAngle": 330, "endAngle": 30},
        {"id": "shape-server-test-sector-east", "type": "annularSector", "name": "East spinning sector", "center": [-3.34, 53.18], "innerRadius": 1_100, "outerRadius": 5_000, "startAngle": 45, "endAngle": 115},
        {"id": "shape-server-test-sector-south", "type": "annularSector", "name": "South spinning sector", "center": [-3.28, 53.10], "innerRadius": 900, "outerRadius": 3_500, "startAngle": 150, "endAngle": 240},
        {"id": "shape-server-test-sector-west", "type": "annularSector", "name": "West spinning sector", "center": [-3.48, 53.10], "innerRadius": 1_300, "outerRadius": 4_500, "startAngle": 230, "endAngle": 305},
    ]
    for shape in [*static_shapes, *sectors]:
        try:
            await asyncio.to_thread(request, "POST", "/api/shapes", shape)
        except HTTPError as error:
            if error.code != 409:
                raise
            await asyncio.to_thread(request, "PUT", f"/api/shapes/{shape['id']}", shape)
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    step = 0
    transient_rotation = -1
    transient_id = "shape-server-test-transient-sector"
    while True:
        elapsed_seconds = loop.time() - started_at
        rotation = elapsed_seconds * 360 / ROTATION_SECONDS
        updates = []
        for index, sector in enumerate(sectors):
            phase = elapsed_seconds + index * math.pi / 2
            start_angle = (sector["startAngle"] + rotation) % 360
            sweep = 55 + 35 * (math.sin(phase) + 1)
            updated_sector = {
                **sector,
                "innerRadius": round(500 + 900 * (math.sin(phase) + 1)),
                "outerRadius": round(3_200 + 1_800 * (math.cos(phase) + 1)),
                "startAngle": start_angle,
                "endAngle": (start_angle + sweep) % 360,
            }
            updates.append(asyncio.to_thread(request, "PUT", f"/api/shapes/{sector['id']}", updated_sector))
        rotating_rectangle = {
            **static_shapes[1],
            "angle": rotation % 360,
        }
        updates.append(
            asyncio.to_thread(
                request,
                "PUT",
                f"/api/shapes/{rotating_rectangle['id']}",
                rotating_rectangle,
            ),
        )
        await asyncio.gather(*updates)

        rotation_number = int(elapsed_seconds / ROTATION_SECONDS)
        if rotation_number != transient_rotation:
            try:
                await asyncio.to_thread(request, "DELETE", f"/api/shapes/{transient_id}")
            except HTTPError as error:
                if error.code != 404:
                    raise
            transient = {
                "id": transient_id,
                "type": "annularSector",
                "name": f"Transient sector {rotation_number + 1}",
                "center": [-3.38, 53.08],
                "innerRadius": 300 + (rotation_number % 4) * 400,
                "outerRadius": 2_800 + (rotation_number % 5) * 500,
                "startAngle": rotation % 360,
                "endAngle": (rotation + 70) % 360,
            }
            await asyncio.to_thread(request, "POST", "/api/shapes", transient)
            transient_rotation = rotation_number
        step += 1
        next_update_at = started_at + step / UPDATES_PER_SECOND
        await asyncio.sleep(max(0, next_update_at - loop.time()))


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(exercise_shapes())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Kody ShapeServerTest", version="1.0.0", lifespan=lifespan)


@app.get("/api/status")
def get_status() -> dict[str, str]:
    return {"shapeServerUrl": SHAPE_SERVER_URL, "message": "ShapeServer sample producer is running."}
