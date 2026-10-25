"""Standalone configuration microservice for Kody application state."""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

ROOT_DIRECTORY = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT_DIRECTORY / "config" / "kody.json"


class MapService(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$", max_length=64)
    type: Literal["vecmap", "satmap", "copernicus", "terrain", "esa-dem", "geojson", "shapes", "adsb", "ais", "airlanes", "symbols", "workspace"]
    url: HttpUrl


class ServiceConfiguration(BaseModel):
    services: list[MapService] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_map_services(self) -> "ServiceConfiguration":
        for service_type in ("vecmap", "satmap", "copernicus", "terrain", "esa-dem"):
            if sum(service.type == service_type for service in self.services) > 1:
                raise ValueError(f"only one {service_type} service may be configured")
        return self


class RuntimeService(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$", max_length=64)
    display_name: str = Field(alias="displayName", min_length=1, max_length=128)
    module: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*:[a-zA-Z_][a-zA-Z0-9_]*$")
    working_directory: str = Field(alias="workingDirectory", min_length=1)
    port: int = Field(ge=1, le=65535)
    enabled: bool = True


class RuntimeConfiguration(BaseModel):
    host: str = "127.0.0.1"
    log_retention_minutes: int = Field(default=30, alias="logRetentionMinutes", ge=1, le=1440)
    services: list[RuntimeService] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_services(self) -> "RuntimeConfiguration":
        service_ids = [service.id for service in self.services]
        if len(service_ids) != len(set(service_ids)):
            raise ValueError("runtime service IDs must be unique")
        ports = [service.port for service in self.services if service.enabled]
        if len(ports) != len(set(ports)):
            raise ValueError("enabled runtime service ports must be unique")
        return self


class MapLayers(BaseModel):
    satellite_imagery: bool = Field(default=False, alias="satelliteImagery")
    copernicus: bool = False
    terrain: bool = False
    esa_dem: bool = Field(default=False, alias="esaDem")
    vector_map: bool = Field(default=True, alias="vectorMap")
    grid: bool = True
    grid_crosshair: bool = Field(default=False, alias="gridCrosshair")
    display_mini_time_control: bool = Field(
        default=False,
        alias="displayMiniTimeControl",
    )
    satellite_opacity: int = Field(default=10, alias="satelliteOpacity", ge=1, le=10)
    copernicus_opacity: int = Field(default=10, alias="copernicusOpacity", ge=1, le=10)
    terrain_opacity: int = Field(default=8, alias="terrainOpacity", ge=1, le=10)
    esa_dem_opacity: int = Field(default=8, alias="esaDemOpacity", ge=1, le=10)
    vector_opacity: int = Field(default=10, alias="vectorOpacity", ge=1, le=10)
    grid_opacity: int = Field(default=10, alias="gridOpacity", ge=1, le=10)
    data_sources: dict[str, bool] = Field(default_factory=dict, alias="dataSources")
    data_source_opacity: dict[str, Annotated[int, Field(ge=1, le=10)]] = Field(
        default_factory=dict,
        alias="dataSourceOpacity",
    )


class MapView(BaseModel):
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-85.05112878, le=85.05112878)
    zoom: float = Field(ge=0, le=18)
    layers: MapLayers = Field(default_factory=MapLayers)


class Viewport(BaseModel):
    west: float = Field(ge=-180, le=180)
    south: float = Field(ge=-85.05112878, le=85.05112878)
    east: float = Field(ge=-180, le=180)
    north: float = Field(ge=-85.05112878, le=85.05112878)
    zoom: float = Field(ge=0, le=18)

    @model_validator(mode="after")
    def validate_bounds(self) -> "Viewport":
        if self.west >= self.east:
            raise ValueError("viewport west must be less than east")
        if self.south >= self.north:
            raise ValueError("viewport south must be less than north")
        return self


class ApplicationConfiguration(ServiceConfiguration):
    model_config = ConfigDict(populate_by_name=True)

    map_view: MapView | None = Field(default=None, alias="mapView")
    data_directory: str = Field(default="data", alias="dataDirectory", min_length=1)
    runtime: RuntimeConfiguration


class ConfigurationStore:
    """Persists validated configuration as a single inspectable JSON document."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = Lock()

    def read(self) -> ApplicationConfiguration:
        with self.lock:
            try:
                return ApplicationConfiguration.model_validate_json(
                    self.path.read_text(encoding="utf-8")
                )
            except FileNotFoundError as error:
                raise RuntimeError(f"application configuration is missing: {self.path}") from error
            except ValueError as error:
                raise RuntimeError(f"application configuration is invalid: {self.path}") from error

    def write(self, configuration: ApplicationConfiguration) -> None:
        encoded = configuration.model_dump_json(indent=2, by_alias=True) + "\n"
        temporary_path = self.path.with_suffix(".tmp")
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(encoded, encoding="utf-8")
            temporary_path.replace(self.path)

    def update_services(self, services: ServiceConfiguration) -> ServiceConfiguration:
        configuration = self.read()
        updated_configuration = configuration.model_copy(update={"services": services.services})
        self.write(updated_configuration)
        return services

    def update_map_view(self, map_view: MapView) -> MapView:
        configuration = self.read()
        updated_configuration = configuration.model_copy(update={"map_view": map_view})
        self.write(updated_configuration)
        return map_view

    def update_runtime(self, runtime: RuntimeConfiguration) -> RuntimeConfiguration:
        configuration = self.read()
        updated_configuration = configuration.model_copy(update={"runtime": runtime})
        self.write(updated_configuration)
        return runtime

    def update_data_directory(self, data_directory: str) -> str:
        configuration = self.read()
        self.write(configuration.model_copy(update={"data_directory": data_directory}))
        return data_directory


class ViewportStore:
    """Shares the current browser viewport without persisting transient state."""

    def __init__(self) -> None:
        self.lock = Lock()
        self.viewport: Viewport | None = None

    def read(self) -> Viewport:
        with self.lock:
            if self.viewport is None:
                raise HTTPException(status_code=404, detail="map viewport is not available")
            return self.viewport

    def update(self, viewport: Viewport) -> Viewport:
        with self.lock:
            self.viewport = viewport
            return viewport


configuration_store = ConfigurationStore(CONFIG_FILE)
viewport_store = ViewportStore()
app = FastAPI(title="Kody Configuration Service", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_methods=["GET", "PUT"],
    allow_headers=["Content-Type"],
)


@app.get("/api/config/services", response_model=ServiceConfiguration)
def get_services() -> ServiceConfiguration:
    configuration = configuration_store.read()
    return ServiceConfiguration(services=configuration.services)


@app.put("/api/config/services", response_model=ServiceConfiguration)
def update_services(services: ServiceConfiguration) -> ServiceConfiguration:
    return configuration_store.update_services(services)


@app.get("/api/config/data-directory")
def get_data_directory() -> dict[str, str]:
    return {"dataDirectory": configuration_store.read().data_directory}


@app.put("/api/config/data-directory")
def update_data_directory(configuration: dict[str, str]) -> dict[str, str]:
    data_directory = configuration.get("dataDirectory", "").strip()
    if not data_directory:
        raise HTTPException(status_code=422, detail="dataDirectory must be a non-empty path")
    return {"dataDirectory": configuration_store.update_data_directory(data_directory)}


@app.get("/api/config/map-view", response_model=MapView | None)
def get_map_view() -> MapView | None:
    return configuration_store.read().map_view


@app.put("/api/config/map-view", response_model=MapView)
def update_map_view(map_view: MapView) -> MapView:
    return configuration_store.update_map_view(map_view)


@app.get("/api/config/runtime", response_model=RuntimeConfiguration)
def get_runtime() -> RuntimeConfiguration:
    return configuration_store.read().runtime


@app.put("/api/config/runtime", response_model=RuntimeConfiguration)
def update_runtime(runtime: RuntimeConfiguration) -> RuntimeConfiguration:
    return configuration_store.update_runtime(runtime)


@app.get("/api/context/viewport", response_model=Viewport)
def get_viewport() -> Viewport:
    return viewport_store.read()


@app.put("/api/context/viewport", response_model=Viewport)
def update_viewport(viewport: Viewport) -> Viewport:
    return viewport_store.update(viewport)
