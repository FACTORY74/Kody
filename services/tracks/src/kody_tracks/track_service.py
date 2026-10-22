"""Persistent route-based aircraft simulation service for the Kody map."""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from pathlib import Path
from threading import Lock
from typing import Annotated, Callable, Literal, TypeVar

from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator

TRAIL_DURATION_SECONDS = 300
ROOT_DIRECTORY = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get("KODY_DATA_DIRECTORY", ROOT_DIRECTORY / "data"))
AIRCRAFT_TYPES_FILE = DATA_ROOT / "tracks" / "aircraft-types.json"
SCENARIO_FILE = DATA_ROOT / "tracks" / "scenario.json"
CONFIG_FILE = ROOT_DIRECTORY / "config" / "tracks.json"
CatalogueModel = TypeVar("CatalogueModel", bound=BaseModel)


def _coordinate(value: list[float]) -> list[float]:
    if (
        len(value) != 2
        or any(not isinstance(item, (int, float)) or isinstance(item, bool) for item in value)
        or not all(math.isfinite(item) for item in value)
        or not -180 <= value[0] <= 180
        or not -90 <= value[1] <= 90
    ):
        raise ValueError("coordinates must be [longitude, latitude] within valid bounds")
    return value


Coordinate = Annotated[list[float], Field(min_length=2, max_length=2)]
CompletionPolicy = Literal["despawn", "restart", "loop", "hold", "reverse"]
AircraftState = Literal["running", "paused"]


class TracksConfiguration(BaseModel):
    trail_duration_seconds: int = Field(
        default=TRAIL_DURATION_SECONDS,
        alias="trailDurationSeconds",
        ge=30,
        le=1800,
    )


class AircraftLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimumSpeedMetresPerSecond: float = Field(ge=0, le=1_000)
    maximumSpeedMetresPerSecond: float = Field(gt=0, le=1_000)
    minimumAltitudeMetres: float = Field(ge=-1_000, le=100_000)
    maximumAltitudeMetres: float = Field(ge=-1_000, le=100_000)
    maximumClimbRateMetresPerSecond: float = Field(gt=0, le=1_000)
    maximumDescentRateMetresPerSecond: float = Field(gt=0, le=1_000)
    maximumTurnRateDegreesPerSecond: float = Field(gt=0, le=360)
    maximumBankAngleDegrees: float = Field(gt=0, le=90)
    maximumAccelerationMetresPerSecondSquared: float = Field(gt=0, le=1_000)
    maximumDecelerationMetresPerSecondSquared: float = Field(gt=0, le=1_000)

    @field_validator("maximumSpeedMetresPerSecond")
    @classmethod
    def validate_maximum_speed(cls, value: float, info: ValidationInfo) -> float:
        if value <= info.data.get("minimumSpeedMetresPerSecond", -1):
            raise ValueError("maximumSpeedMetresPerSecond must exceed minimumSpeedMetresPerSecond")
        return value

    @field_validator("maximumAltitudeMetres")
    @classmethod
    def validate_maximum_altitude(cls, value: float, info: ValidationInfo) -> float:
        if value < info.data.get("minimumAltitudeMetres", math.inf):
            raise ValueError("maximumAltitudeMetres must be at least minimumAltitudeMetres")
        return value


class AircraftType(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=200)
    displayName: str = Field(min_length=1, max_length=200)
    symbol: str = Field(min_length=1, max_length=100)
    limits: AircraftLimits


class AircraftTypeCatalogue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal[1] = 1
    types: list[AircraftType] = Field(min_length=1)

    @field_validator("types")
    @classmethod
    def validate_unique_ids(cls, value: list[AircraftType]) -> list[AircraftType]:
        if len({aircraft_type.id for aircraft_type in value}) != len(value):
            raise ValueError("aircraft type ids must be unique")
        return value


class RouteLeg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=200)
    waypoint: Coordinate
    targetAltitudeMetres: float = Field(ge=-1_000, le=100_000)
    targetSpeedMetresPerSecond: float = Field(ge=0, le=1_000)

    _validate_waypoint = field_validator("waypoint")(_coordinate)


class Route(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$")
    completionPolicy: CompletionPolicy
    legs: list[RouteLeg] = Field(min_length=1)

    @field_validator("legs")
    @classmethod
    def validate_unique_leg_ids(cls, value: list[RouteLeg]) -> list[RouteLeg]:
        if len({leg.id for leg in value}) != len(value):
            raise ValueError("route leg ids must be unique")
        return value


class Aircraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    aircraftTypeId: str = Field(min_length=1, max_length=200)
    routeId: str = Field(min_length=1, max_length=200)
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$")
    initialPosition: Coordinate
    initialAltitudeMetres: float = Field(ge=-1_000, le=100_000)
    initialSpeedMetresPerSecond: float = Field(ge=0, le=1_000)
    state: AircraftState = "running"

    _validate_initial_position = field_validator("initialPosition")(_coordinate)


class ScenarioCatalogue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal[1] = 1
    routes: list[Route] = Field(default_factory=list)
    aircraft: list[Aircraft] = Field(default_factory=list)

    @field_validator("routes")
    @classmethod
    def validate_unique_route_ids(cls, value: list[Route]) -> list[Route]:
        if len({route.id for route in value}) != len(value):
            raise ValueError("route ids must be unique")
        return value

    @field_validator("aircraft")
    @classmethod
    def validate_unique_aircraft_ids(cls, value: list[Aircraft]) -> list[Aircraft]:
        if len({aircraft.id for aircraft in value}) != len(value):
            raise ValueError("aircraft ids must be unique")
        return value


class FlightRuntime(BaseModel):
    position: Coordinate
    legStartPosition: Coordinate
    altitudeMetres: float
    speedMetresPerSecond: float
    headingDegrees: float = Field(ge=0, lt=360)
    activeLegIndex: int = 0
    direction: Literal[-1, 1] = 1
    state: AircraftState = "running"
    trail: list[Coordinate] = Field(default_factory=list)


class SimulationControls(BaseModel):
    model_config = ConfigDict(extra="forbid")

    running: bool


def atomic_json_write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(path)


def default_aircraft_types() -> AircraftTypeCatalogue:
    return AircraftTypeCatalogue(types=[AircraftType(
        id="light-turboprop",
        displayName="Light turboprop",
        symbol="plane",
        limits=AircraftLimits(
            minimumSpeedMetresPerSecond=55,
            maximumSpeedMetresPerSecond=150,
            minimumAltitudeMetres=0,
            maximumAltitudeMetres=9_000,
            maximumClimbRateMetresPerSecond=12,
            maximumDescentRateMetresPerSecond=15,
            maximumTurnRateDegreesPerSecond=3.5,
            maximumBankAngleDegrees=30,
            maximumAccelerationMetresPerSecondSquared=2.5,
            maximumDecelerationMetresPerSecondSquared=3.5,
        ),
    )])


def default_scenario() -> ScenarioCatalogue:
    return ScenarioCatalogue(
        routes=[Route(
            id="south-coast-patrol",
            name="South coast patrol",
            color="#38c8ec",
            completionPolicy="loop",
            legs=[
                RouteLeg(id="leg-a", waypoint=[-0.78, 51.02], targetAltitudeMetres=1_200, targetSpeedMetresPerSecond=85),
                RouteLeg(id="leg-b", waypoint=[-0.35, 51.05], targetAltitudeMetres=1_500, targetSpeedMetresPerSecond=100),
                RouteLeg(id="leg-c", waypoint=[-0.31, 51.30], targetAltitudeMetres=1_800, targetSpeedMetresPerSecond=105),
                RouteLeg(id="leg-d", waypoint=[-0.72, 51.28], targetAltitudeMetres=1_300, targetSpeedMetresPerSecond=90),
            ],
        )],
        aircraft=[Aircraft(
            id="patrol-01",
            name="Patrol 01",
            aircraftTypeId="light-turboprop",
            routeId="south-coast-patrol",
            color="#ffd166",
            initialPosition=[-0.72, 51.28],
            initialAltitudeMetres=1_200,
            initialSpeedMetresPerSecond=80,
        )],
    )


class ConfigurationStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = Lock()
        self.configuration = self._load()

    def _load(self) -> TracksConfiguration:
        if not self.path.is_file():
            return TracksConfiguration()
        return TracksConfiguration.model_validate_json(self.path.read_text(encoding="utf-8"))

    def revision(self) -> str:
        return hashlib.sha256(self.configuration.model_dump_json(by_alias=True).encode("utf-8")).hexdigest()

    def response(self) -> dict[str, object]:
        with self.lock:
            return {"revision": self.revision(), **self.configuration.model_dump(by_alias=True)}

    def update(self, configuration: TracksConfiguration, expected_revision: str | None) -> None:
        with self.lock:
            if expected_revision is not None and expected_revision != self.revision():
                raise HTTPException(status_code=409, detail="configuration revision is stale")
            atomic_json_write(self.path, configuration.model_dump(by_alias=True))
            self.configuration = configuration


class TrackStore:
    """Owns persisted declarative catalogues and their non-persisted runtime state."""

    def __init__(
        self,
        aircraft_types_path: Path = AIRCRAFT_TYPES_FILE,
        scenario_path: Path = SCENARIO_FILE,
        clock: Callable[[], float] = time.time,
        configuration: TracksConfiguration | None = None,
    ) -> None:
        self.aircraft_types_path = aircraft_types_path
        self.scenario_path = scenario_path
        self.clock = clock
        self.configuration = configuration or TracksConfiguration()
        self.lock = Lock()
        self.aircraft_types = self._load_catalogue(aircraft_types_path, AircraftTypeCatalogue, default_aircraft_types())
        self.scenario = self._load_catalogue(scenario_path, ScenarioCatalogue, default_scenario())
        self._validate_scenario(self.scenario, self.aircraft_types)
        self.runtime: dict[str, FlightRuntime] = {}
        self.last_tick = self.clock()
        self.simulation_seconds = 0.0
        self.simulation_running = True
        self.simulation_control_revision = 0
        self._reset_runtime()

    @staticmethod
    def _load_catalogue(
        path: Path,
        model: type[CatalogueModel],
        default: CatalogueModel,
    ) -> CatalogueModel:
        if not path.is_file():
            atomic_json_write(path, default.model_dump(mode="json"))
            return default
        try:
            return model.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, json.JSONDecodeError) as error:
            raise RuntimeError(f"tracks catalogue is invalid: cannot read {path.name}") from error

    @staticmethod
    def _validate_scenario(scenario: ScenarioCatalogue, types: AircraftTypeCatalogue) -> None:
        routes = {route.id: route for route in scenario.routes}
        type_by_id = {aircraft_type.id: aircraft_type for aircraft_type in types.types}
        for aircraft in scenario.aircraft:
            route = routes.get(aircraft.routeId)
            aircraft_type = type_by_id.get(aircraft.aircraftTypeId)
            if route is None:
                raise ValueError(f"aircraft {aircraft.id} references unknown route {aircraft.routeId}")
            if aircraft_type is None:
                raise ValueError(f"aircraft {aircraft.id} references unknown aircraft type {aircraft.aircraftTypeId}")
            limits = aircraft_type.limits
            values = [
                (aircraft.initialAltitudeMetres, limits.minimumAltitudeMetres, limits.maximumAltitudeMetres, "initial altitude"),
                (aircraft.initialSpeedMetresPerSecond, limits.minimumSpeedMetresPerSecond, limits.maximumSpeedMetresPerSecond, "initial speed"),
            ]
            values.extend(
                (leg.targetAltitudeMetres, limits.minimumAltitudeMetres, limits.maximumAltitudeMetres, f"target altitude for {leg.id}")
                for leg in route.legs
            )
            values.extend(
                (leg.targetSpeedMetresPerSecond, limits.minimumSpeedMetresPerSecond, limits.maximumSpeedMetresPerSecond, f"target speed for {leg.id}")
                for leg in route.legs
            )
            for value, minimum, maximum, label in values:
                if not minimum <= value <= maximum:
                    raise ValueError(f"{label} for aircraft {aircraft.id} is outside {aircraft_type.id} limits")

    def _revision(self, catalogue: BaseModel) -> str:
        return hashlib.sha256(json.dumps(catalogue.model_dump(mode="json"), separators=(",", ":"), sort_keys=True).encode("utf-8")).hexdigest()

    def _reset_runtime(self) -> None:
        routes = {route.id: route for route in self.scenario.routes}
        self.runtime = {}
        for aircraft in self.scenario.aircraft:
            first_waypoint = routes[aircraft.routeId].legs[0].waypoint
            _, initial_heading = self._distance_and_bearing(aircraft.initialPosition, first_waypoint)
            self.runtime[aircraft.id] = FlightRuntime(
                position=list(aircraft.initialPosition),
                legStartPosition=list(aircraft.initialPosition),
                altitudeMetres=aircraft.initialAltitudeMetres,
                speedMetresPerSecond=aircraft.initialSpeedMetresPerSecond,
                headingDegrees=initial_heading,
                state=aircraft.state,
                trail=[list(aircraft.initialPosition)],
            )

    def apply_configuration(self, configuration: TracksConfiguration) -> None:
        with self.lock:
            self.configuration = configuration

    def aircraft_types_response(self) -> dict[str, object]:
        with self.lock:
            return {"revision": self._revision(self.aircraft_types), **self.aircraft_types.model_dump(mode="json")}

    def scenario_response(self) -> dict[str, object]:
        with self.lock:
            return {"revision": self._revision(self.scenario), **self.scenario.model_dump(mode="json")}

    def update_aircraft_types(self, catalogue: AircraftTypeCatalogue, expected_revision: str | None) -> None:
        with self.lock:
            if expected_revision is not None and expected_revision != self._revision(self.aircraft_types):
                raise HTTPException(status_code=409, detail="aircraft-type catalogue revision is stale")
            try:
                self._validate_scenario(self.scenario, catalogue)
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
            atomic_json_write(self.aircraft_types_path, catalogue.model_dump(mode="json"))
            self.aircraft_types = catalogue

    def update_scenario(self, scenario: ScenarioCatalogue, expected_revision: str | None) -> None:
        with self.lock:
            if expected_revision is not None and expected_revision != self._revision(self.scenario):
                raise HTTPException(status_code=409, detail="scenario revision is stale")
            try:
                self._validate_scenario(scenario, self.aircraft_types)
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
            atomic_json_write(self.scenario_path, scenario.model_dump(mode="json"))
            self.scenario = scenario
            self._reset_runtime()
            self.last_tick = self.clock()
            self.simulation_seconds = 0.0

    def clear(self) -> None:
        self.update_scenario(ScenarioCatalogue(), None)

    def simulation_response(self) -> dict[str, object]:
        with self.lock:
            return {
                "revision": str(self.simulation_control_revision),
                "running": self.simulation_running,
                "elapsedSeconds": self.simulation_seconds,
            }

    def update_simulation(self, controls: SimulationControls, expected_revision: str | None) -> None:
        with self.lock:
            if expected_revision is not None and expected_revision != str(self.simulation_control_revision):
                raise HTTPException(status_code=409, detail="simulation control revision is stale")
            self._tick(self.clock())
            if controls.running != self.simulation_running:
                self.simulation_running = controls.running
                self.simulation_control_revision += 1

    def reset_simulation(self) -> None:
        with self.lock:
            self._reset_runtime()
            self.simulation_seconds = 0.0
            self.last_tick = self.clock()
            self.simulation_control_revision += 1

    @staticmethod
    def _distance_and_bearing(origin: Coordinate, destination: Coordinate) -> tuple[float, float]:
        latitude_scale = 111_320
        north = (destination[1] - origin[1]) * latitude_scale
        east = (destination[0] - origin[0]) * latitude_scale * math.cos(math.radians((origin[1] + destination[1]) / 2))
        return math.hypot(east, north), math.degrees(math.atan2(east, north)) % 360

    @staticmethod
    def _advance_position(origin: Coordinate, bearing: float, distance: float) -> list[float]:
        latitude = origin[1] + distance * math.cos(math.radians(bearing)) / 111_320
        longitude_scale = 111_320 * math.cos(math.radians((origin[1] + latitude) / 2))
        longitude = origin[0] + distance * math.sin(math.radians(bearing)) / longitude_scale
        return [longitude, latitude]

    @staticmethod
    def _move_toward(current: float, target: float, increase_limit: float, decrease_limit: float, elapsed: float) -> float:
        if target > current:
            return min(target, current + increase_limit * elapsed)
        return max(target, current - decrease_limit * elapsed)

    @staticmethod
    def _heading_difference(current: float, target: float) -> float:
        return (target - current + 540) % 360 - 180

    def _advance_leg(self, runtime: FlightRuntime, route: Route, aircraft: Aircraft, elapsed: float) -> None:
        if runtime.state != "running" or not route.legs:
            return
        limits = next(item.limits for item in self.aircraft_types.types if item.id == aircraft.aircraftTypeId)
        leg = route.legs[runtime.activeLegIndex]
        runtime.speedMetresPerSecond = self._move_toward(
            runtime.speedMetresPerSecond, leg.targetSpeedMetresPerSecond,
            limits.maximumAccelerationMetresPerSecondSquared, limits.maximumDecelerationMetresPerSecondSquared, elapsed,
        )
        runtime.altitudeMetres = self._move_toward(
            runtime.altitudeMetres, leg.targetAltitudeMetres,
            limits.maximumClimbRateMetresPerSecond, limits.maximumDescentRateMetresPerSecond, elapsed,
        )
        distance, desired_bearing = self._distance_and_bearing(runtime.position, leg.waypoint)
        step = runtime.speedMetresPerSecond * elapsed
        if distance <= step:
            runtime.position = list(leg.waypoint)
            self._complete_leg(runtime, route, aircraft)
        else:
            heading_change = self._heading_difference(runtime.headingDegrees, desired_bearing)
            maximum_change = limits.maximumTurnRateDegreesPerSecond * elapsed
            runtime.headingDegrees = (
                runtime.headingDegrees + max(-maximum_change, min(maximum_change, heading_change))
            ) % 360
            runtime.position = self._advance_position(runtime.position, runtime.headingDegrees, step)

    def _complete_leg(self, runtime: FlightRuntime, route: Route, aircraft: Aircraft) -> None:
        final_leg = runtime.activeLegIndex == (len(route.legs) - 1 if runtime.direction == 1 else 0)
        if not final_leg:
            runtime.activeLegIndex += runtime.direction
            runtime.legStartPosition = list(runtime.position)
            return
        if route.completionPolicy == "despawn":
            runtime.state = "paused"
        elif route.completionPolicy == "hold":
            runtime.state = "paused"
        elif route.completionPolicy == "restart":
            runtime.position = list(aircraft.initialPosition)
            runtime.altitudeMetres = aircraft.initialAltitudeMetres
            runtime.speedMetresPerSecond = aircraft.initialSpeedMetresPerSecond
            runtime.activeLegIndex = 0
            runtime.direction = 1
            runtime.legStartPosition = list(aircraft.initialPosition)
        elif route.completionPolicy == "loop":
            runtime.activeLegIndex = 0
            runtime.legStartPosition = list(runtime.position)
        else:
            runtime.direction = -1 if runtime.direction == 1 else 1
            runtime.activeLegIndex += runtime.direction
            runtime.legStartPosition = list(runtime.position)

    def _tick(self, now: float) -> None:
        elapsed = min(max(now - self.last_tick, 0), 10)
        self.last_tick = now
        if not self.simulation_running:
            return
        self.simulation_seconds += elapsed
        routes = {route.id: route for route in self.scenario.routes}
        for aircraft in self.scenario.aircraft:
            runtime = self.runtime[aircraft.id]
            self._advance_leg(runtime, routes[aircraft.routeId], aircraft, elapsed)
            runtime.trail.append(list(runtime.position))
            maximum_samples = self.configuration.trail_duration_seconds + 1
            if len(runtime.trail) > maximum_samples:
                del runtime.trail[:-maximum_samples]

    def response(self) -> dict[str, object]:
        with self.lock:
            self._tick(self.clock())
            routes = {route.id: route for route in self.scenario.routes}
            types = {item.id: item for item in self.aircraft_types.types}
            features: list[dict[str, object]] = []
            for route in self.scenario.routes:
                coordinates = [leg.waypoint for leg in route.legs]
                if route.completionPolicy == "loop" and len(coordinates) > 1:
                    coordinates = [*coordinates, coordinates[0]]
                if len(coordinates) > 1:
                    features.append({
                        "type": "Feature", "id": f"route:{route.id}",
                        "properties": {"name": route.name, "color": route.color, "kind": "track-route"},
                        "geometry": {"type": "LineString", "coordinates": coordinates},
                    })
                for leg in route.legs:
                    features.append({
                        "type": "Feature", "id": f"waypoint:{route.id}:{leg.id}",
                        "properties": {
                            "name": f"{route.name} - {leg.id}", "color": route.color, "kind": "route-waypoint",
                            "routeId": route.id, "legId": leg.id, "targetAltitudeMetres": leg.targetAltitudeMetres,
                            "targetSpeedMetresPerSecond": leg.targetSpeedMetresPerSecond,
                            "kody": {"selectable": True, "selectionLabel": f"{route.name} - {leg.id}"},
                        },
                        "geometry": {"type": "Point", "coordinates": leg.waypoint},
                    })
            for aircraft in self.scenario.aircraft:
                runtime = self.runtime[aircraft.id]
                route = routes[aircraft.routeId]
                leg = route.legs[runtime.activeLegIndex]
                distance_to_waypoint, desired_bearing = self._distance_and_bearing(runtime.position, leg.waypoint)
                leg_length, _ = self._distance_and_bearing(runtime.legStartPosition, leg.waypoint)
                leg_progress = 1.0 if leg_length == 0 else max(0.0, min(1.0, 1 - distance_to_waypoint / leg_length))
                eta_seconds = (
                    distance_to_waypoint / runtime.speedMetresPerSecond
                    if runtime.speedMetresPerSecond > 0
                    else None
                )
                features.extend([
                    {
                        "type": "Feature", "id": f"trail:{aircraft.id}",
                        "properties": {"name": f"{aircraft.name} trail", "color": aircraft.color, "kind": "track-trail", "fadeTrail": True},
                        "geometry": {"type": "LineString", "coordinates": runtime.trail},
                    },
                    {
                        "type": "Feature", "id": f"track:{aircraft.id}",
                        "properties": {
                            "name": aircraft.name, "color": aircraft.color, "kind": "track-position",
                            "symbol": types[aircraft.aircraftTypeId].symbol, "routeId": route.id,
                            "activeLegId": leg.id, "state": runtime.state,
                            "bearingDegrees": runtime.headingDegrees, "desiredBearingDegrees": desired_bearing,
                            "speedMetresPerSecond": runtime.speedMetresPerSecond,
                            "altitudeBarometricFeet": runtime.altitudeMetres * 3.28084,
                            "targetAltitudeMetres": leg.targetAltitudeMetres,
                            "targetSpeedMetresPerSecond": leg.targetSpeedMetresPerSecond,
                            "distanceToWaypointMetres": distance_to_waypoint,
                            "estimatedSecondsToWaypoint": eta_seconds,
                            "legProgress": leg_progress,
                            "simulationSeconds": self.simulation_seconds,
                            "kody": {"selectable": True, "selectionLabel": aircraft.name},
                        },
                        "geometry": {"type": "Point", "coordinates": runtime.position},
                    },
                ])
            collection: dict[str, object] = {"type": "FeatureCollection", "features": features}
            revision = hashlib.sha256(json.dumps(collection, separators=(",", ":"), sort_keys=True).encode("utf-8")).hexdigest()
            return {"revision": revision, **collection}


configuration_store = ConfigurationStore(CONFIG_FILE)
track_store = TrackStore(configuration=configuration_store.configuration)
app = FastAPI(title="Kody Track GeoJSON Service", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_methods=["GET", "PUT", "POST"],
    allow_headers=["Content-Type", "If-Match"],
)


@app.get("/api/kody/manifest")
def get_manifest() -> dict[str, object]:
    return {
        "kodyApiVersion": "1",
        "serviceId": "tracks",
        "displayName": "Live tracks",
        "dataEndpoint": {"path": "/api/geojson", "format": "geojson"},
        "capabilities": {
            "scenarioEditor": {
                "aircraftTypesPath": "/api/tracks/aircraft-types",
                "scenarioPath": "/api/tracks/scenario",
                "updateMethod": "PUT",
                "revisionHeader": "If-Match",
                "completionPolicies": ["despawn", "restart", "loop", "hold", "reverse"],
                "route": {
                    "geometry": "LineString",
                    "waypointFields": ["targetAltitudeMetres", "targetSpeedMetres"],
                },
            },
            "simulationControls": {
                "readPath": "/api/tracks/simulation",
                "writePath": "/api/tracks/simulation",
                "resetPath": "/api/tracks/simulation/reset",
                "revisionHeader": "If-Match",
            },
        },
        "configuration": {
            "readPath": "/api/config", "writePath": "/api/config",
            "fields": [{"key": "trailDurationSeconds", "label": "Trail duration (seconds)", "type": "number", "minimum": 30, "maximum": 1800, "step": 1}],
            "actions": [{"label": "Clear scenario", "path": "/api/actions/clear-local-data", "method": "POST", "confirmationMessage": "Clear all persisted tracks and routes? This cannot be undone."}],
        },
    }


@app.get("/api/config")
def get_configuration() -> dict[str, object]:
    return configuration_store.response()


@app.put("/api/config")
def update_configuration(configuration: TracksConfiguration, if_match: str | None = Header(default=None)) -> dict[str, object]:
    configuration_store.update(configuration, if_match)
    track_store.apply_configuration(configuration)
    return configuration_store.response()


@app.get("/api/tracks/aircraft-types")
def get_aircraft_types() -> dict[str, object]:
    return track_store.aircraft_types_response()


@app.put("/api/tracks/aircraft-types")
def update_aircraft_types(catalogue: AircraftTypeCatalogue = Body(...), if_match: str | None = Header(default=None)) -> dict[str, object]:
    track_store.update_aircraft_types(catalogue, if_match)
    return track_store.aircraft_types_response()


@app.get("/api/tracks/scenario")
def get_scenario() -> dict[str, object]:
    return track_store.scenario_response()


@app.put("/api/tracks/scenario")
def update_scenario(scenario: ScenarioCatalogue = Body(...), if_match: str | None = Header(default=None)) -> dict[str, object]:
    track_store.update_scenario(scenario, if_match)
    return track_store.scenario_response()


@app.get("/api/tracks/simulation")
def get_simulation() -> dict[str, object]:
    return track_store.simulation_response()


@app.put("/api/tracks/simulation")
def update_simulation(
    controls: SimulationControls = Body(...),
    if_match: str | None = Header(default=None),
) -> dict[str, object]:
    track_store.update_simulation(controls, if_match)
    return track_store.simulation_response()


@app.post("/api/tracks/simulation/reset")
def reset_simulation() -> dict[str, object]:
    track_store.reset_simulation()
    return track_store.simulation_response()


@app.post("/api/actions/clear-local-data")
def clear_local_data() -> dict[str, str]:
    track_store.clear()
    return {"message": "Cleared persisted Live tracks scenario data."}


@app.get("/api/geojson")
def get_track() -> dict[str, object]:
    return track_store.response()
