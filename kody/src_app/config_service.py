# Kody — General-Purpose Mapping Application
#
# A Python-based mapping application with a JavaScript/HTML web interface and a plugin architecture for extending functionality.
#
# Copyright (c) 2026 Factory74
#
# SPDX-License-Identifier: MIT
#
# Distributed under the MIT License. See LICENSE for details.
# Provided "AS IS", without warranty of any kind.

"""Standalone configuration microservice for Kody application state."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from threading import Lock
from typing import Annotated, Any, Callable, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, model_validator

ROOT_DIRECTORY = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT_DIRECTORY / "config" / "kody.json"
HTTP_URL_ADAPTER = TypeAdapter(HttpUrl)
LOGGER = logging.getLogger(__name__)

ServiceType = Literal[
    "vecmap",
    "satmap",
    "copernicus",
    "terrain",
    "esa-dem",
    "worldcover",
    "geojson",
    "shapes",
    "adsb",
    "ais",
    "airlanes",
    "symbols",
    "workspace",
]


class MapService(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$", max_length=64)
    type: ServiceType
    url: HttpUrl
    managed: bool = False


class ServiceConfiguration(BaseModel):
    services: list[MapService] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_map_services(self) -> "ServiceConfiguration":
        for service_type in ("vecmap", "satmap", "copernicus", "terrain", "esa-dem", "worldcover"):
            if sum(service.type == service_type for service in self.services) > 1:
                raise ValueError(f"only one {service_type} service may be configured")
        return self


class RuntimeMapService(BaseModel):
    """Map-facing identity for a local service launched by Kody."""

    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$", max_length=64)
    type: ServiceType


class RuntimeService(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$", max_length=64)
    display_name: str = Field(alias="displayName", min_length=1, max_length=128)
    module: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*:[a-zA-Z_][a-zA-Z0-9_]*$")
    working_directory: str = Field(alias="workingDirectory", min_length=1)
    port: int = Field(ge=1, le=65535)
    enabled: bool = True
    map_service: RuntimeMapService | None = Field(default=None, alias="mapService")


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
        map_service_ids = [
            service.map_service.id
            for service in self.services
            if service.map_service is not None
        ]
        if len(map_service_ids) != len(set(map_service_ids)):
            raise ValueError("runtime map service IDs must be unique")
        return self


class MapLayers(BaseModel):
    satellite_imagery: bool = Field(default=False, alias="satelliteImagery")
    copernicus: bool = False
    terrain: bool = False
    esa_dem: bool = Field(default=False, alias="esaDem")
    worldcover: bool = False
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
    worldcover_opacity: int = Field(default=8, alias="worldcoverOpacity", ge=1, le=10)
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


class ApplicationConfiguration(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    external_services: list[MapService] = Field(default_factory=list, alias="externalServices")
    map_view: MapView | None = Field(default=None, alias="mapView")
    data_directory: str = Field(default="data", alias="dataDirectory", min_length=1)
    runtime: RuntimeConfiguration

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_service_catalog(cls, value: Any) -> Any:
        """Move legacy local browser entries onto their matching runtime services."""
        if not isinstance(value, dict) or "externalServices" in value:
            return value
        legacy_services = value.get("services")
        runtime = value.get("runtime")
        if not isinstance(legacy_services, list) or not isinstance(runtime, dict):
            return value
        runtime_services = runtime.get("services")
        if not isinstance(runtime_services, list):
            return value

        migrated_runtime_services = [
            dict(service) if isinstance(service, dict) else service
            for service in runtime_services
        ]
        external_services: list[object] = []
        for legacy_service in legacy_services:
            if not isinstance(legacy_service, dict):
                external_services.append(legacy_service)
                continue
            raw_url = legacy_service.get("url")
            try:
                parsed_url = urlsplit(raw_url) if isinstance(raw_url, str) else None
            except ValueError:
                parsed_url = None
            matching_runtime_service = next(
                (
                    service
                    for service in migrated_runtime_services
                    if isinstance(service, dict)
                    and service.get("port") == (parsed_url.port if parsed_url is not None else None)
                    and parsed_url is not None
                    and parsed_url.hostname == runtime.get("host")
                ),
                None,
            )
            if matching_runtime_service is None:
                external_services.append(legacy_service)
                continue
            matching_runtime_service["mapService"] = {
                "id": legacy_service.get("id"),
                "type": legacy_service.get("type"),
            }

        migrated_runtime = dict(runtime)
        migrated_runtime["services"] = migrated_runtime_services
        migrated_value = dict(value)
        migrated_value.pop("services", None)
        migrated_value["externalServices"] = external_services
        migrated_value["runtime"] = migrated_runtime
        return migrated_value

    @model_validator(mode="after")
    def validate_external_services(self) -> "ApplicationConfiguration":
        managed_ids = {
            service.map_service.id
            for service in self.runtime.services
            if service.map_service is not None
        }
        external_ids = [service.id for service in self.external_services]
        if len(external_ids) != len(set(external_ids)):
            raise ValueError("external service IDs must be unique")
        if managed_ids.intersection(external_ids):
            raise ValueError("external service IDs must not duplicate runtime map service IDs")
        return self


class ConfigurationStore:
    """Persists validated configuration as a single inspectable JSON document."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = Lock()

    @staticmethod
    def revision_for(value: Any) -> str:
        """Return a stable strong revision for one independently editable value."""
        if isinstance(value, BaseModel):
            encoded = value.model_dump_json(by_alias=True)
        else:
            encoded = str(value)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _read_locked(self) -> ApplicationConfiguration:
        try:
            return ApplicationConfiguration.model_validate_json(
                self.path.read_text(encoding="utf-8")
            )
        except FileNotFoundError as error:
            raise RuntimeError(f"application configuration is missing: {self.path}") from error
        except ValueError as error:
            raise RuntimeError(f"application configuration is invalid: {self.path}") from error

    def _write_locked(self, configuration: ApplicationConfiguration) -> None:
        encoded = configuration.model_dump_json(indent=2, by_alias=True) + "\n"
        temporary_path = self.path.with_suffix(".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(encoded, encoding="utf-8")
        temporary_path.replace(self.path)

    def read(self) -> ApplicationConfiguration:
        """Return a validated configuration snapshot."""
        with self.lock:
            return self._read_locked()

    def snapshot(self) -> tuple[ApplicationConfiguration, str]:
        """Return a complete configuration snapshot and its strong revision token."""
        with self.lock:
            configuration = self._read_locked()
            return configuration, self.revision_for(configuration)

    def write(self, configuration: ApplicationConfiguration) -> None:
        """Persist a complete configuration for initialization and migration."""
        with self.lock:
            self._write_locked(configuration)

    def _mutate(
        self,
        expected_revision: str | None,
        selected_value: Callable[[ApplicationConfiguration], Any],
        mutation: Callable[[ApplicationConfiguration], ApplicationConfiguration],
    ) -> tuple[ApplicationConfiguration, str]:
        with self.lock:
            configuration = self._read_locked()
            actual_revision = self.revision_for(selected_value(configuration))
            if expected_revision is None:
                # Existing pages loaded before ETag support cannot send If-Match.
                # The atomic mutation still preserves unrelated configuration fields.
                LOGGER.warning("Allowing legacy configuration write without If-Match")
            elif expected_revision.strip('"') != actual_revision:
                raise HTTPException(status_code=409, detail="configuration revision is stale")
            updated_configuration = mutation(configuration)
            self._write_locked(updated_configuration)
            return updated_configuration, self.revision_for(selected_value(updated_configuration))

    @staticmethod
    def services(configuration: ApplicationConfiguration) -> ServiceConfiguration:
        """Combine runtime-derived local services with persisted external services."""
        managed_services = [
            MapService(
                id=service.map_service.id,
                type=service.map_service.type,
                url=HTTP_URL_ADAPTER.validate_python(
                    f"http://{configuration.runtime.host}:{service.port}/"
                ),
                managed=True,
            )
            for service in configuration.runtime.services
            if service.enabled and service.map_service is not None
        ]
        return ServiceConfiguration(services=[*managed_services, *configuration.external_services])

    def update_services(
        self,
        services: ServiceConfiguration,
        expected_revision: str | None,
    ) -> tuple[ServiceConfiguration, str]:
        """Replace external services while protecting runtime-derived local services."""
        def mutate(configuration: ApplicationConfiguration) -> ApplicationConfiguration:
            managed_by_id = {
                service.id: service
                for service in self.services(configuration).services
                if service.managed
            }
            external_services: list[MapService] = []
            for service in services.services:
                managed = managed_by_id.get(service.id)
                if managed is not None:
                    if service.type != managed.type or service.url != managed.url:
                        raise HTTPException(
                            status_code=422,
                            detail=f"runtime-managed service cannot be edited: {service.id}",
                        )
                    continue
                if service.managed:
                    raise HTTPException(
                        status_code=422,
                        detail=f"external service must not be marked managed: {service.id}",
                    )
                external_services.append(service)
            return configuration.model_copy(update={"external_services": external_services})

        updated_configuration, revision = self._mutate(
            expected_revision,
            self.services,
            mutate,
        )
        return self.services(updated_configuration), revision

    def update_map_view(
        self,
        map_view: MapView,
        expected_revision: str | None,
    ) -> tuple[MapView, str]:
        """Persist map state without losing concurrent configuration changes."""
        updated_configuration, revision = self._mutate(
            expected_revision,
            lambda configuration: configuration.map_view,
            lambda configuration: configuration.model_copy(update={"map_view": map_view}),
        )
        return updated_configuration.map_view or map_view, revision

    def update_runtime(
        self,
        runtime: RuntimeConfiguration,
        expected_revision: str | None,
    ) -> tuple[RuntimeConfiguration, str]:
        """Persist the runtime catalog as one revision-checked mutation."""
        updated_configuration, revision = self._mutate(
            expected_revision,
            lambda configuration: configuration.runtime,
            lambda configuration: configuration.model_copy(update={"runtime": runtime}),
        )
        return updated_configuration.runtime, revision

    def update_data_directory(
        self,
        data_directory: str,
        expected_revision: str | None,
    ) -> tuple[str, str]:
        """Persist the shared data directory as one revision-checked mutation."""
        updated_configuration, revision = self._mutate(
            expected_revision,
            lambda configuration: configuration.data_directory,
            lambda configuration: configuration.model_copy(update={"data_directory": data_directory}),
        )
        return updated_configuration.data_directory, revision


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
    allow_headers=["Content-Type", "If-Match"],
    expose_headers=["ETag"],
)


def set_revision_header(response: Response, revision: str) -> None:
    """Expose a strong ETag that clients must return for configuration writes."""
    response.headers["ETag"] = f'"{revision}"'


@app.get("/api/config/services", response_model=ServiceConfiguration)
def get_services(response: Response) -> ServiceConfiguration:
    configuration, _ = configuration_store.snapshot()
    services = configuration_store.services(configuration)
    set_revision_header(response, configuration_store.revision_for(services))
    return services


@app.put("/api/config/services", response_model=ServiceConfiguration)
def update_services(
    services: ServiceConfiguration,
    response: Response,
    if_match: str | None = Header(default=None),
) -> ServiceConfiguration:
    updated_services, revision = configuration_store.update_services(services, if_match)
    set_revision_header(response, revision)
    return updated_services


@app.get("/api/config/data-directory")
def get_data_directory(response: Response) -> dict[str, str]:
    configuration, _ = configuration_store.snapshot()
    set_revision_header(response, configuration_store.revision_for(configuration.data_directory))
    return {"dataDirectory": configuration.data_directory}


@app.put("/api/config/data-directory")
def update_data_directory(
    configuration: dict[str, str],
    response: Response,
    if_match: str | None = Header(default=None),
) -> dict[str, str]:
    data_directory = configuration.get("dataDirectory", "").strip()
    if not data_directory:
        raise HTTPException(status_code=422, detail="dataDirectory must be a non-empty path")
    updated_data_directory, revision = configuration_store.update_data_directory(
        data_directory,
        if_match,
    )
    set_revision_header(response, revision)
    return {"dataDirectory": updated_data_directory}


@app.get("/api/config/map-view", response_model=MapView | None)
def get_map_view(response: Response) -> MapView | None:
    configuration, _ = configuration_store.snapshot()
    set_revision_header(response, configuration_store.revision_for(configuration.map_view))
    return configuration.map_view


@app.put("/api/config/map-view", response_model=MapView)
def update_map_view(
    map_view: MapView,
    response: Response,
    if_match: str | None = Header(default=None),
) -> MapView:
    updated_map_view, revision = configuration_store.update_map_view(map_view, if_match)
    set_revision_header(response, revision)
    return updated_map_view


@app.get("/api/config/runtime", response_model=RuntimeConfiguration)
def get_runtime(response: Response) -> RuntimeConfiguration:
    configuration, _ = configuration_store.snapshot()
    set_revision_header(response, configuration_store.revision_for(configuration.runtime))
    return configuration.runtime


@app.put("/api/config/runtime", response_model=RuntimeConfiguration)
def update_runtime(
    runtime: RuntimeConfiguration,
    response: Response,
    if_match: str | None = Header(default=None),
) -> RuntimeConfiguration:
    updated_runtime, revision = configuration_store.update_runtime(runtime, if_match)
    set_revision_header(response, revision)
    return updated_runtime


@app.get("/api/context/viewport", response_model=Viewport)
def get_viewport() -> Viewport:
    return viewport_store.read()


@app.put("/api/context/viewport", response_model=Viewport)
def update_viewport(viewport: Viewport) -> Viewport:
    return viewport_store.update(viewport)
