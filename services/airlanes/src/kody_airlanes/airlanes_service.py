"""Poll official NATS datasets and expose selected airspace as GeoJSON."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from html import unescape
from io import BytesIO
import json
import os
from math import cos, pi, sin
from pathlib import Path
import re
from threading import Event, Lock, Thread
from typing import Callable, Iterator, cast
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

ROOT_DIRECTORY = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get("KODY_DATA_DIRECTORY", ROOT_DIRECTORY / "data"))
CONFIG_FILE = ROOT_DIRECTORY / "config" / "airlanes.json"
CACHE_FILE = DATA_ROOT / "airlanes" / "airlanes.geojson"
NOTAM_CACHE_FILE = DATA_ROOT / "airlanes" / "notams.geojson"
DATASETS_PAGE = "https://nats-uk.ead-it.com/cms-nats/opencms/en/Publications/digital-datasets/"
NOTAM_PIB_URL = "https://pibs.nats.co.uk/operational/pibs/PIB.xml"
MAP_NOTAM_Q_CODE_PREFIXES = {"R", "W"}
MAP_NOTAM_MAX_RADIUS_NAUTICAL_MILES = 50
NOTAM_COORDINATE = re.compile(r"\b(\d{4,6})([NS])\s*(\d{5,7})([EW])\b")
DOWNLOAD_LINK = re.compile(r"""href\s*=\s*["'](?P<url>[^"']+\.(?:kml|kmz|zip)(?:\?[^"']*)?)["']""", re.IGNORECASE)
SECTOR_DATASET = re.compile(r"(?:LAC[_ -]?PC[_ -]?SECTOR|SECTOR[_ -]?DATA).*(?:KML|KMZ)", re.IGNORECASE)
AIRSPACE_DATASET = re.compile(r"(?:EG|UK)[_ -]?AIP.*(?:FULL).*?(?:KML|KMZ)", re.IGNORECASE)
CONTROLLED_CATEGORIES = ("CTA", "CTR", "Danger", "Prohibited", "Restricted")
CATEGORY_COLORS = {
    "CTA": "#38bdf8",
    "CTR": "#22c55e",
    "Danger": "#f59e0b",
    "Prohibited": "#ef4444",
    "Restricted": "#a855f7",
}


def initialize_data_directory() -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)


initialize_data_directory()


class AirLanesConfiguration(BaseModel):
    refresh_hours: int = Field(default=24, alias="refreshHours", ge=1, le=168)
    notam_refresh_hours: int = Field(default=1, alias="notamRefreshHours", ge=1, le=24)
    show_atc_sectors: bool = Field(default=True, alias="showAtcSectors")
    show_controlled_airspace: bool = Field(default=True, alias="showControlledAirspace")
    show_notams: bool = Field(default=False, alias="showNotams")


class ConfigurationStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = Lock()
        self.configuration = self._load()

    def _load(self) -> AirLanesConfiguration:
        return AirLanesConfiguration.model_validate_json(self.path.read_text(encoding="utf-8")) if self.path.is_file() else AirLanesConfiguration()

    def response(self) -> dict[str, object]:
        with self.lock:
            return self._response()

    def _response(self) -> dict[str, object]:
        encoded = self.configuration.model_dump_json(by_alias=True)
        return {"revision": sha256(encoded.encode()).hexdigest(), **self.configuration.model_dump(by_alias=True)}

    def update(self, configuration: AirLanesConfiguration, expected_revision: str | None) -> None:
        with self.lock:
            if expected_revision is not None and expected_revision != self._response()["revision"]:
                raise HTTPException(status_code=409, detail="configuration revision is stale")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(configuration.model_dump_json(by_alias=True, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.path)
            self.configuration = configuration


def download(url: str) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": "Kody-AirLanes/0.2"}), timeout=30) as response:
        return response.read()


def dataset_urls(page: str, today: datetime | None = None) -> dict[str, str]:
    """Find the currently effective NATS KML downloads on the datasets page."""
    candidates: dict[str, list[str]] = {"sectors": [], "controlledAirspace": []}
    for match in DOWNLOAD_LINK.finditer(page):
        url = unescape(match.group("url"))
        filename = url.rsplit("/", 1)[-1]
        if SECTOR_DATASET.search(filename):
            candidates["sectors"].append(url)
        elif AIRSPACE_DATASET.search(filename):
            candidates["controlledAirspace"].append(url)

    selected: dict[str, str] = {}
    current_day = (today or datetime.now(UTC)).date()
    for name, urls in candidates.items():
        dated_urls = []
        for url in urls:
            date_match = re.search(r"(\d{8})", url)
            if date_match:
                dated_urls.append((datetime.strptime(date_match.group(1), "%Y%m%d").date(), url))
        effective = [item for item in dated_urls if item[0] <= current_day]
        if effective:
            selected[name] = max(effective)[1]
        elif dated_urls:
            selected[name] = min(dated_urls)[1]
        elif urls:
            selected[name] = urls[0]
    missing = {"sectors", "controlledAirspace"} - selected.keys()
    if missing:
        raise RuntimeError(f"NATS Digital Datasets page did not publish KML downloads for {', '.join(sorted(missing))}")
    return {name: urljoin(DATASETS_PAGE, url) for name, url in selected.items()}


def local_name(element: ElementTree.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def parse_coordinates(text: str | None) -> list[list[float]]:
    coordinates: list[list[float]] = []
    for value in (text or "").split():
        values = value.split(",")
        if len(values) < 2:
            continue
        try:
            coordinates.append([float(values[0]), float(values[1])])
        except ValueError:
            continue
    if len(coordinates) >= 3 and coordinates[0] != coordinates[-1]:
        coordinates.append(coordinates[0])
    return coordinates


def placemarks_with_hierarchy(root: ElementTree.Element) -> Iterator[tuple[ElementTree.Element, tuple[str, ...]]]:
    def visit(container: ElementTree.Element, hierarchy: tuple[str, ...]) -> Iterator[tuple[ElementTree.Element, tuple[str, ...]]]:
        for child in container:
            tag = local_name(child)
            if tag in {"Document", "Folder"}:
                name = next((item.text or "" for item in child if local_name(item) == "name"), "").strip()
                yield from visit(child, hierarchy + ((name,) if name else ()))
            elif tag == "Placemark":
                yield child, hierarchy

    yield from visit(root, ())


def placemark_name(placemark: ElementTree.Element, default: str) -> str:
    return next((item.text or "" for item in placemark if local_name(item) == "name"), default).strip()


def placemark_polygons(placemark: ElementTree.Element) -> list[list[list[list[float]]]]:
    polygons: list[list[list[list[float]]]] = []
    for element in placemark.iter():
        if local_name(element) != "Polygon":
            continue
        rings: list[list[list[float]]] = []
        for boundary in element:
            if local_name(boundary) not in {"outerBoundaryIs", "innerBoundaryIs"}:
                continue
            coordinates = next((item for item in boundary.iter() if local_name(item) == "coordinates"), None)
            ring = parse_coordinates(coordinates.text if coordinates is not None else None)
            if len(ring) >= 4:
                rings.append(ring)
        if rings:
            polygons.append(rings)
    return polygons


def category_for_hierarchy(hierarchy: tuple[str, ...]) -> str | None:
    for category in CONTROLLED_CATEGORIES:
        if any(re.search(rf"\b{category.upper()}\b", name.upper()) for name in hierarchy):
            return category
    return None


def controlled_kml_to_geojson(kml: bytes, source_url: str = "") -> dict[str, object]:
    root = ElementTree.fromstring(kml)
    features: list[dict[str, object]] = []
    for placemark, hierarchy in placemarks_with_hierarchy(root):
        category = category_for_hierarchy(hierarchy)
        if category is None:
            continue
        polygons = placemark_polygons(placemark)
        if not polygons:
            continue
        geometry: dict[str, object]
        if len(polygons) == 1:
            geometry = {"type": "Polygon", "coordinates": polygons[0]}
        else:
            geometry = {"type": "MultiPolygon", "coordinates": polygons}
        features.append({
            "type": "Feature",
            "properties": {
                "name": placemark_name(placemark, f"Unnamed {category} airspace"),
                "kind": "controlled-airspace",
                "dataCategory": "controlled-airspace",
                "category": category,
                "color": CATEGORY_COLORS[category],
                "fillOpacity": 0.25,
                "source": "UK ICAO AIP Digital Dataset",
                "sourceUrl": source_url,
                "folderHierarchy": list(hierarchy),
            },
            "geometry": geometry,
        })
    return {"type": "FeatureCollection", "features": features}


def sector_kml_to_geojson(kml: bytes, source_url: str = "") -> dict[str, object]:
    root = ElementTree.fromstring(kml)
    features: list[dict[str, object]] = []
    for placemark in root.findall(".//{*}Placemark"):
        name = placemark.findtext("{*}name", default="Unnamed NATS ATC sector").strip()
        for polygon in placemark_polygons(placemark):
            points = polygon[0]
            features.append({
                "type": "Feature",
                "properties": {
                    "name": name,
                    "kind": "airlane-sector-boundary",
                    "dataCategory": "atc-sectors",
                    "color": "#8b5cf6",
                    "lineStyle": "dashed",
                    "source": "NATS LAC/PC Sector Digital Dataset",
                    "sourceUrl": source_url,
                },
                "geometry": {"type": "LineString", "coordinates": points},
            })
    return {"type": "FeatureCollection", "features": features}


def kml_to_geojson(kml: bytes) -> dict[str, object]:
    """Backward-compatible sector conversion entry point."""
    return sector_kml_to_geojson(kml)


def kmls_from_archive(archive: ZipFile) -> list[bytes]:
    kmls = [archive.read(name) for name in archive.namelist() if name.lower().endswith(".kml")]
    for name in archive.namelist():
        if name.lower().endswith(".kmz"):
            with ZipFile(BytesIO(archive.read(name))) as nested_archive:
                kmls.extend(kmls_from_archive(nested_archive))
    return kmls


def kml_from_archive(archive: ZipFile) -> bytes:
    kmls = kmls_from_archive(archive)
    if kmls:
        return kmls[0]
    raise RuntimeError("NATS archive does not contain KML")


def kml_documents(payload: bytes) -> list[bytes]:
    try:
        with ZipFile(BytesIO(payload)) as archive:
            return kmls_from_archive(archive)
    except BadZipFile:
        if b"<kml" in payload.lower():
            return [payload]
        raise RuntimeError("NATS download is not a KML, KMZ, or ZIP archive")


def collection_features(collection: dict[str, object]) -> list[dict[str, object]]:
    features = collection.get("features")
    if not isinstance(features, list):
        return []
    return cast(list[dict[str, object]], [feature for feature in features if isinstance(feature, dict)])


def feature_data_category(feature: dict[str, object]) -> object:
    properties = feature.get("properties")
    return properties.get("dataCategory") if isinstance(properties, dict) else None


def map_display_notam(feature: dict[str, object]) -> bool:
    properties = feature.get("properties")
    return isinstance(properties, dict) and properties.get("mapDisplay") is True


def text(element: ElementTree.Element, name: str) -> str:
    return (element.findtext(name) or "").strip()


def notam_degrees(value: str, hemisphere: str, degree_digits: int) -> float:
    degrees = int(value[:degree_digits])
    minutes = int(value[degree_digits:degree_digits + 2])
    seconds = int(value[degree_digits + 2:]) if len(value) > degree_digits + 2 else 0
    coordinate = degrees + minutes / 60 + seconds / 3600
    return -coordinate if hemisphere in {"S", "W"} else coordinate


def notam_coordinate_pair(latitude: str, north_south: str, longitude: str, east_west: str) -> list[float]:
    return [
        notam_degrees(longitude, east_west, 3),
        notam_degrees(latitude, north_south, 2),
    ]


def notam_circle_coordinates(coordinate: str, radius_nautical_miles: float) -> list[list[float]]:
    match = re.fullmatch(r"(\d{2})(\d{2})([NS])(\d{3})(\d{2})([EW])", coordinate)
    if match is None:
        raise ValueError(f"unsupported NOTAM coordinate {coordinate!r}")
    longitude, latitude = notam_coordinate_pair(
        f"{match.group(1)}{match.group(2)}",
        match.group(3),
        f"{match.group(4)}{match.group(5)}",
        match.group(6),
    )
    latitude_delta = radius_nautical_miles / 60
    longitude_delta = latitude_delta / max(cos(latitude * pi / 180), 0.01)
    coordinates = [
        [
            longitude + longitude_delta * sin(index * 2 * pi / 48),
            latitude + latitude_delta * cos(index * 2 * pi / 48),
        ]
        for index in range(48)
    ]
    coordinates.append(coordinates[0])
    return coordinates


def notam_polygon_coordinates(item_e: str) -> list[list[float]] | None:
    if not re.search(r"\b(?:AREA BOUNDED BY|STRAIGHT LINES JOINING)\b", item_e, re.IGNORECASE):
        return None
    coordinates = [
        notam_coordinate_pair(
            match.group(1),
            match.group(2),
            match.group(3),
            match.group(4),
        )
        for match in NOTAM_COORDINATE.finditer(item_e)
    ]
    if len(coordinates) < 3:
        return None
    if coordinates[0] != coordinates[-1]:
        coordinates.append(coordinates[0])
    return coordinates


def notam_pib_to_geojson(payload: bytes) -> dict[str, object]:
    root = ElementTree.fromstring(payload)
    features: list[dict[str, object]] = []
    for notice in root.findall(".//Notam"):
        coordinate = text(notice, "Coordinates")
        radius = text(notice, "Radius")
        if not coordinate or not radius:
            continue
        try:
            radius_nautical_miles = float(radius)
            coordinates = notam_circle_coordinates(coordinate, radius_nautical_miles)
        except ValueError:
            continue
        q_line = notice.find("QLine")
        q_code = f"{text(q_line, 'Code23')}{text(q_line, 'Code45')}" if q_line is not None else ""
        item_e = text(notice, "ItemE")
        polygon = notam_polygon_coordinates(item_e)
        identifier = f"{text(notice, 'Series')}{text(notice, 'Number')}/{text(notice, 'Year')}"
        features.append({
            "type": "Feature",
            "properties": {
                "name": identifier or "UK NOTAM",
                "kind": "notam-restriction",
                "dataCategory": "notams",
                "color": "#f97316",
                "fillOpacity": 0.08,
                "label": False,
                "mapDisplay": (
                    q_code[:1] in MAP_NOTAM_Q_CODE_PREFIXES
                    and radius_nautical_miles <= MAP_NOTAM_MAX_RADIUS_NAUTICAL_MILES
                ),
                "source": "NATS UK PIB XML",
                "sourceUrl": NOTAM_PIB_URL,
                "geometrySource": "item-e-polygon" if polygon is not None else "q-line-circle",
                "coordinate": coordinate,
                "radiusNauticalMiles": radius_nautical_miles,
                "validFrom": text(notice, "StartValidity"),
                "validTo": text(notice, "EndValidity"),
                "text": item_e,
                "qCode": {
                    "fir": text(q_line, "FIR") if q_line is not None else "",
                    "code": q_code,
                    "lower": text(q_line, "Lower") if q_line is not None else "",
                    "upper": text(q_line, "Upper") if q_line is not None else "",
                },
            },
            "geometry": {"type": "Polygon", "coordinates": [polygon or coordinates]},
        })
    return {"type": "FeatureCollection", "features": features}


class AirspaceStore:
    def __init__(self, cache_file: Path, fetch: Callable[[str], bytes] = download) -> None:
        self.cache_file = cache_file
        self.fetch = fetch
        self.lock = Lock()
        self.collection = self._load_cache()
        self.fetched_at: str | None = None
        self.last_error: str | None = None

    def _load_cache(self) -> dict[str, object]:
        if not self.cache_file.is_file():
            return {"type": "FeatureCollection", "features": []}
        collection = json.loads(self.cache_file.read_text(encoding="utf-8"))
        for feature in collection_features(collection):
            properties = feature.get("properties")
            if isinstance(properties, dict) and properties.get("kind") == "airlane-corridor":
                properties.setdefault("dataCategory", "atc-sectors")
        return collection

    def refresh(self) -> None:
        urls = dataset_urls(self.fetch(DATASETS_PAGE).decode("utf-8"))
        features: list[dict[str, object]] = []
        for kml in kml_documents(self.fetch(urls["sectors"])):
            features.extend(collection_features(sector_kml_to_geojson(kml, urls["sectors"])))
        for kml in kml_documents(self.fetch(urls["controlledAirspace"])):
            features.extend(collection_features(controlled_kml_to_geojson(kml, urls["controlledAirspace"])))
        if not features:
            raise RuntimeError("NATS KML downloads contain no supported airspace geometry")
        collection = {"type": "FeatureCollection", "features": features}
        with self.lock:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(collection, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.cache_file)
            self.collection = collection
            self.fetched_at = datetime.now(UTC).isoformat()
            self.last_error = None

    def response(self, configuration: AirLanesConfiguration) -> dict[str, object]:
        enabled_categories = []
        if configuration.show_atc_sectors:
            enabled_categories.append("atc-sectors")
        if configuration.show_controlled_airspace:
            enabled_categories.append("controlled-airspace")
        with self.lock:
            collection = {
                "type": "FeatureCollection",
                "features": [
                    feature for feature in collection_features(self.collection)
                    if feature_data_category(feature) in enabled_categories
                ],
            }
            revision = sha256(json.dumps(collection, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            return {
                "revision": revision,
                **collection,
                "enabledCategories": enabled_categories,
                "source": {"provider": "NATS Digital Datasets", "fetchedAt": self.fetched_at, "lastError": self.last_error},
            }


class NotamStore:
    def __init__(self, cache_file: Path, fetch: Callable[[str], bytes] = download) -> None:
        self.cache_file = cache_file
        self.fetch = fetch
        self.lock = Lock()
        self.collection = self._load_cache()
        self.fetched_at: str | None = None
        self.last_error: str | None = None

    def _load_cache(self) -> dict[str, object]:
        if not self.cache_file.is_file():
            return {"type": "FeatureCollection", "features": []}
        return json.loads(self.cache_file.read_text(encoding="utf-8"))

    def refresh(self) -> None:
        collection = notam_pib_to_geojson(self.fetch(NOTAM_PIB_URL))
        with self.lock:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(collection, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.cache_file)
            self.collection = collection
            self.fetched_at = datetime.now(UTC).isoformat()
            self.last_error = None

    def response(self, enabled: bool) -> dict[str, object]:
        with self.lock:
            features = [
                feature
                for feature in collection_features(self.collection)
                if map_display_notam(feature)
            ] if enabled else []
            return {
                "features": features,
                "source": {
                    "provider": "NATS UK PIB XML",
                    "url": NOTAM_PIB_URL,
                    "fetchedAt": self.fetched_at,
                    "lastError": self.last_error,
                },
            }


configuration_store = ConfigurationStore(CONFIG_FILE)
airspace_store = AirspaceStore(CACHE_FILE)
notam_store = NotamStore(NOTAM_CACHE_FILE)
stop_event = Event()
notam_poll_event = Event()
app = FastAPI(title="Kody AirLanes Service", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"], allow_methods=["GET", "PUT"], allow_headers=["Content-Type", "If-Match"])


def poll_airspace() -> None:
    while not stop_event.is_set():
        try:
            airspace_store.refresh()
        except (OSError, RuntimeError, ValueError, ElementTree.ParseError) as error:
            with airspace_store.lock:
                airspace_store.last_error = str(error)
        stop_event.wait(configuration_store.configuration.refresh_hours * 3600)


def poll_notams() -> None:
    while not stop_event.is_set():
        configuration = configuration_store.configuration
        if configuration.show_notams:
            try:
                notam_store.refresh()
            except (OSError, RuntimeError, ValueError, ElementTree.ParseError) as error:
                with notam_store.lock:
                    notam_store.last_error = str(error)
        notam_poll_event.wait(configuration.notam_refresh_hours * 3600)
        notam_poll_event.clear()


@app.on_event("startup")
def start_poller() -> None:
    stop_event.clear()
    Thread(target=poll_airspace, name="airlanes-poller", daemon=True).start()
    Thread(target=poll_notams, name="airlanes-notam-poller", daemon=True).start()


@app.on_event("shutdown")
def stop_poller() -> None:
    stop_event.set()


@app.get("/api/kody/manifest")
def get_manifest() -> dict[str, object]:
    return {
        "kodyApiVersion": "1",
        "serviceId": "airlanes",
        "displayName": "AirLanes",
        "dataEndpoint": {"path": "/api/geojson", "format": "geojson"},
        "configuration": {
            "readPath": "/api/config",
            "writePath": "/api/config",
            "fields": [
                {"key": "showAtcSectors", "label": "Show ATC sector boundaries", "type": "boolean"},
                {"key": "showControlledAirspace", "label": "Show controlled and special-use airspace", "type": "boolean"},
                {"key": "showNotams", "label": "Show NATS UK PIB NOTAMs", "type": "boolean"},
                {"key": "refreshHours", "label": "NATS poll interval (hours)", "type": "number", "minimum": 1, "maximum": 168, "step": 1},
                {"key": "notamRefreshHours", "label": "NATS PIB poll interval (hours)", "type": "number", "minimum": 1, "maximum": 24, "step": 1},
            ],
        },
    }


@app.get("/api/config")
def get_configuration() -> dict[str, object]:
    return configuration_store.response()


@app.put("/api/config")
def update_configuration(configuration: AirLanesConfiguration, if_match: str | None = Header(default=None)) -> dict[str, object]:
    configuration_store.update(configuration, if_match)
    notam_poll_event.set()
    return configuration_store.response()


@app.get("/api/geojson")
def get_airspace() -> dict[str, object]:
    configuration = configuration_store.configuration
    airspace = airspace_store.response(configuration)
    notams = notam_store.response(configuration.show_notams)
    airspace["features"] = collection_features(airspace) + notams["features"]
    enabled_categories = airspace["enabledCategories"]
    assert isinstance(enabled_categories, list)
    if configuration.show_notams:
        enabled_categories.append("notams")
    airspace["revision"] = sha256(
        json.dumps(airspace["features"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    airspace["source"] = {
        "staticAirspace": airspace["source"],
        "notams": notams["source"],
    }
    return airspace
