import { MapMath } from "./map-math.js";

const canvas = document.querySelector("#map");
let context = canvas.getContext("2d");
const container = document.querySelector("#map-container");
const coordinates = document.querySelector("#coordinates");
const gridSize = document.querySelector("#grid-size");
const gridSizeReadout = gridSize.parentElement;
const zoomLevel = document.querySelector("#zoom-level");
const upsLevel = document.querySelector("#ups-level");
const status = document.querySelector("#map-status");
const attribution = document.querySelector("#map-attribution");
let vectorLayer;
let imageryLayer;
let copernicusLayer;
let terrainLayer;
const mapServiceList = document.querySelector("#map-service-list");
const dataSourceList = document.querySelector("#data-source-list");
const liveSourceList = document.querySelector("#live-source-list");
const symbolSourceList = document.querySelector("#symbol-source-list");
const serviceConfigDialog = document.querySelector("#service-config-dialog");
const serviceConfigForm = document.querySelector("#service-config-form");
const serviceConfigTitle = document.querySelector("#service-config-title");
const serviceConfigFields = document.querySelector("#service-config-fields");
const serviceConfigStatus = document.querySelector("#service-config-status");
const serviceConfigActions = document.querySelector("#service-config-actions");
const serviceConfigProgress = document.querySelector("#service-config-progress");
const serviceConfigProgressLabel = document.querySelector("#service-config-progress-label");
const serviceConfigProgressValue = document.querySelector("#service-config-progress-value");
const serviceConfigProgressBar = document.querySelector("#service-config-progress-bar");
const serviceConfigCancel = document.querySelector("#service-config-cancel");
const serviceConfigSubmit = serviceConfigForm.querySelector('button[type="submit"]');
const mapContextMenu = document.querySelector("#map-context-menu");
const gridLayer = document.querySelector("#grid-layer");
const sidePanel = document.querySelector("#side-panel");
const panelResizeHandle = document.querySelector("#panel-resize-handle");
const configurationServiceUrl = "http://127.0.0.1:8001";

const camera = { x: 0.5, y: 0.5, zoom: 1.4 };
const tileCache = new Map();
const rasterTileCache = new Map();
const copernicusTileCache = new Map();
const terrainTileCache = new Map();
const requestedTileKeys = new Set();
const requestedRasterTileKeys = new Set();
const requestedCopernicusTileKeys = new Set();
const requestedTerrainTileKeys = new Set();
const tileWorker = new Worker("/tile-worker.js?v=20261020d");
let vectorMapServiceUrl = window.location.origin;
let satelliteMapServiceUrl = window.location.origin;
let copernicusServiceUrl = window.location.origin;
let terrainServiceUrl = window.location.origin;
let configuredMapServices = [];
let configuredServices = [];
let serviceReconciliationInProgress = false;
let pendingServiceIds = null;
let pendingServiceChecks = 0;
let configurationLoaded = false;
let dragging = false;
let lastPointer = null;
let tileRequestFrame = null;
const frameTimes = [];
let updatesPerSecond = 0;
let lastUpsDisplayUpdate = 0;
let resizingPanel = false;
let mapStateVersion = 0;
let vectorTileGeneration = 0;
let vectorTileRevision = null;
let vectorTileZooms = [0];
let savedMapStateVersion = 0;
let mapViewSaveInProgress = false;
let rasterRevision = null;
let copernicusRevision = null;
let terrainRevision = null;
let terrainPalette = null;
let viewportElevation = null;
let overlayServices = [];
let overlayRevision = null;
const overlayCollections = new Map();
let symbolServices = [];
const symbolDefinitions = new Map();
let shapes = [];
let airLanesShapeGroups = [];
let liveShapeFeatureGroups = [];
let contextActionFeatures = [];
let contextMenuPlugins = [];
let measureStart = null;
let measureEnd = null;
let configurationSession = null;
let serviceActivityTimer = null;
let viewportPublishTimer = null;
let viewportElevationTimer = null;
let viewportElevationRequest = 0;
const mapServices = new Map();
const layerOpacity = { satellite: 10, copernicus: 10, terrain: 8, vector: 10, grid: 10 };
let gridCrosshair = false;
const airLanesCacheCanvas = document.createElement("canvas");
const airLanesCacheContext = airLanesCacheCanvas.getContext("2d");
const tileLayerCanvas = document.createElement("canvas");
const tileLayerContext = tileLayerCanvas.getContext("2d");
const EARTH_RADIUS_KILOMETRES = 6_371.0088;
const AIRLANES_CACHE_PADDING = 96;
const AIRLANES_CACHE_SETTLE_DELAY = 180;
let airLanesCache = null;
let airLanesCacheRevision = null;
let airLanesCacheRebuildTimer = null;

async function registeredRunningServices(services) {
  const results = await Promise.all(services.map(async (service) => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 3000);
    try {
      const response = await fetch(`${service.url.replace(/\/$/, "")}/api/kody/manifest`, {
        signal: controller.signal,
      });
      if (!response.ok) {
        return null;
      }
      const manifest = await response.json();
      return { ...service, url: service.url.replace(/\/$/, ""), displayName: manifest.displayName || service.id };
    } catch {
      return null;
    } finally {
      window.clearTimeout(timeout);
    }
  }));
  return results.filter(Boolean);
}

function activeServiceIds() {
  return new Set([...mapServices.values(), ...overlayServices, ...symbolServices].map((service) => service.id));
}

async function reconcileRegisteredServices() {
  if (serviceReconciliationInProgress) {
    return;
  }
  serviceReconciliationInProgress = true;
  try {
    if (!configurationLoaded) {
      await loadConfiguration();
      return;
    }
    const liveServices = await registeredRunningServices(configuredServices);
    const liveIds = new Set(liveServices.map((service) => service.id));
    const activeIds = activeServiceIds();
    if (liveIds.size !== activeIds.size || [...liveIds].some((id) => !activeIds.has(id))) {
      const currentIds = [...liveIds].sort();
      if (pendingServiceIds?.every((id, index) => id === currentIds[index])
        && pendingServiceIds.length === currentIds.length) {
        pendingServiceChecks += 1;
      } else {
        pendingServiceIds = currentIds;
        pendingServiceChecks = 1;
      }
      if (pendingServiceChecks < 3) {
        return;
      }
      pendingServiceIds = null;
      pendingServiceChecks = 0;
      await loadConfiguration(false);
    } else {
      pendingServiceIds = null;
      pendingServiceChecks = 0;
    }
  } finally {
    serviceReconciliationInProgress = false;
  }
}

function mapLayerDetails(type, layers) {
  return {
    vecmap: { control: "vectorLayer", visible: layers?.vectorMap ?? true, opacity: "vector" },
    satmap: { control: "imageryLayer", visible: layers?.satelliteImagery ?? false, opacity: "satellite" },
    copernicus: { control: "copernicusLayer", visible: layers?.copernicus ?? false, opacity: "copernicus" },
    terrain: { control: "terrainLayer", visible: layers?.terrain ?? false, opacity: "terrain" },
  }[type];
}

function renderMapServiceControls(layers) {
  mapServiceList.replaceChildren();
  for (const service of mapServices.values()) {
    const details = mapLayerDetails(service.type, layers);
    if (!details) {
      continue;
    }
    const row = document.createElement("div");
    row.className = "map-layer-row";
    const label = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = details.visible;
    checkbox.addEventListener("change", () => {
      mapStateVersion += 1;
      refreshMap();
      if (service.type === "satmap") {
        refreshRasterRevision().catch((error) => { status.textContent = `Unable to refresh satellite imagery: ${error.message}`; });
      } else if (service.type === "copernicus") {
        refreshCopernicusRevision().catch((error) => { status.textContent = `Unable to refresh Copernicus imagery: ${error.message}`; });
      } else if (service.type === "terrain") {
        refreshTerrainRevision().catch((error) => { status.textContent = `Unable to refresh terrain: ${error.message}`; });
      }
    });
    label.append(checkbox, ` ${service.displayName}`);
    const opacity = document.createElement("div");
    opacity.className = "opacity-control";
    const decrease = document.createElement("button");
    decrease.type = "button";
    decrease.textContent = "−";
    const value = document.createElement("output");
    value.value = String(layerOpacity[details.opacity]).padStart(2, "0");
    const increase = document.createElement("button");
    increase.type = "button";
    increase.textContent = "+";
    const changeOpacity = (change) => {
      layerOpacity[details.opacity] = Math.max(1, Math.min(10, layerOpacity[details.opacity] + change));
      value.value = String(layerOpacity[details.opacity]).padStart(2, "0");
      mapStateVersion += 1;
      refreshMap();
    };
    decrease.addEventListener("click", () => changeOpacity(-1));
    increase.addEventListener("click", () => changeOpacity(1));
    opacity.append(decrease, value, increase);
    const configure = document.createElement("button");
    configure.type = "button";
    configure.className = "configure-service";
    configure.textContent = "⚙";
    configure.title = `Configure ${service.displayName}`;
    configure.addEventListener("click", () => configureService(service).catch((error) => { status.textContent = `Unable to configure ${service.displayName}: ${error.message}`; }));
    row.append(label, opacity, configure);
    mapServiceList.append(row);
    if (details.control === "vectorLayer") vectorLayer = checkbox;
    if (details.control === "imageryLayer") imageryLayer = checkbox;
    if (details.control === "copernicusLayer") copernicusLayer = checkbox;
    if (details.control === "terrainLayer") terrainLayer = checkbox;
  }
}

const layers = {
  vector: { isVisible: () => vectorLayer?.checked, draw: () => drawTileLayerWithOpacity(layerOpacity.vector, drawCountryLayer) },
  imagery: { isVisible: () => imageryLayer?.checked, draw: () => drawTileLayerWithOpacity(layerOpacity.satellite, drawImageryLayer) },
  copernicus: { isVisible: () => copernicusLayer?.checked, draw: () => drawTileLayerWithOpacity(layerOpacity.copernicus, drawCopernicusLayer) },
  terrain: { isVisible: () => terrainLayer?.checked, draw: () => drawTileLayerWithOpacity(layerOpacity.terrain, drawTerrainLayer) },
  shapes: { isVisible: () => shapes.length > 0 || contextActionFeatures.length > 0, draw: drawShapesLayer },
  grid: { isVisible: () => gridLayer.checked, draw: () => drawWithOpacity(layerOpacity.grid, drawGridLayer) },
  measurement: { isVisible: () => measureStart !== null, draw: drawMeasurement },
};

function scale() {
  return MapMath.TILE_SIZE * 2 ** camera.zoom;
}

function measureDistanceKilometres(start, end) {
  const latitudeDelta = (end.latitude - start.latitude) * Math.PI / 180;
  const longitudeDelta = (end.longitude - start.longitude) * Math.PI / 180;
  const startLatitude = start.latitude * Math.PI / 180;
  const endLatitude = end.latitude * Math.PI / 180;
  const haversine = Math.sin(latitudeDelta / 2) ** 2
    + Math.cos(startLatitude) * Math.cos(endLatitude) * Math.sin(longitudeDelta / 2) ** 2;
  return EARTH_RADIUS_KILOMETRES * 2 * Math.atan2(Math.sqrt(haversine), Math.sqrt(1 - haversine));
}

function greatCirclePoints(start, end) {
  const startLatitude = start.latitude * Math.PI / 180;
  const startLongitude = start.longitude * Math.PI / 180;
  const endLatitude = end.latitude * Math.PI / 180;
  const endLongitude = end.longitude * Math.PI / 180;
  const startVector = [
    Math.cos(startLatitude) * Math.cos(startLongitude),
    Math.cos(startLatitude) * Math.sin(startLongitude),
    Math.sin(startLatitude),
  ];
  const endVector = [
    Math.cos(endLatitude) * Math.cos(endLongitude),
    Math.cos(endLatitude) * Math.sin(endLongitude),
    Math.sin(endLatitude),
  ];
  const dotProduct = Math.max(-1, Math.min(1, startVector.reduce(
    (sum, component, index) => sum + component * endVector[index],
    0,
  )));
  const angle = Math.acos(dotProduct);
  const pointCount = Math.max(2, Math.ceil(angle * 180 / Math.PI) + 1);
  const sineAngle = Math.sin(angle);
  if (sineAngle < 1e-8) {
    return [start, end];
  }
  return Array.from({ length: pointCount }, (_, index) => {
    const ratio = index / (pointCount - 1);
    const startWeight = Math.sin((1 - ratio) * angle) / sineAngle;
    const endWeight = Math.sin(ratio * angle) / sineAngle;
    const x = startWeight * startVector[0] + endWeight * endVector[0];
    const y = startWeight * startVector[1] + endWeight * endVector[1];
    const z = startWeight * startVector[2] + endWeight * endVector[2];
    return {
      longitude: Math.atan2(y, x) * 180 / Math.PI,
      latitude: Math.atan2(z, Math.hypot(x, y)) * 180 / Math.PI,
    };
  });
}

function formatMeasureDistance(distanceKilometres, latitude) {
  const metresPerPixel = Math.cos(latitude * Math.PI / 180)
    * 2 * Math.PI * 6_378_137 / scale();
  const resolutionKilometres = Math.max(metresPerPixel * 2 / 1_000, 0.0001);
  const decimals = Math.max(0, Math.min(3, Math.ceil(-Math.log10(resolutionKilometres))));
  return `${distanceKilometres.toFixed(decimals)} km`;
}

function drawMeasurePin(point, color) {
  context.beginPath();
  context.arc(point.x, point.y, 7, 0, 2 * Math.PI);
  context.fillStyle = color;
  context.fill();
  context.lineWidth = 2;
  context.strokeStyle = "#07111e";
  context.stroke();
  context.beginPath();
  context.moveTo(point.x, point.y + 7);
  context.lineTo(point.x, point.y + 15);
  context.strokeStyle = color;
  context.lineWidth = 3;
  context.stroke();
}

function drawMeasurement() {
  const start = screenPoint(measureStart.longitude, measureStart.latitude);
  context.save();
  drawMeasurePin(start, "#59dcff");
  if (measureEnd) {
    const end = screenPoint(measureEnd.longitude, measureEnd.latitude);
    const route = greatCirclePoints(measureStart, measureEnd);
    context.beginPath();
    route.forEach((point, index) => {
      const screen = screenPoint(point.longitude, point.latitude);
      if (index === 0) {
        context.moveTo(screen.x, screen.y);
      } else {
        context.lineTo(screen.x, screen.y);
      }
    });
    context.strokeStyle = "#ffbf69";
    context.lineWidth = 3;
    context.stroke();
    drawMeasurePin(end, "#ffbf69");
    const distance = measureDistanceKilometres(measureStart, measureEnd);
    const routeMidpoint = route[Math.floor(route.length / 2)];
    const midpoint = screenPoint(routeMidpoint.longitude, routeMidpoint.latitude);
    const label = formatMeasureDistance(
      distance,
      routeMidpoint.latitude,
    );
    context.font = "600 12px system-ui, sans-serif";
    context.textAlign = "center";
    context.textBaseline = "middle";
    const width = context.measureText(label).width;
    context.fillStyle = "rgb(5 18 29 / 85%)";
    context.fillRect(midpoint.x - width / 2 - 6, midpoint.y - 10, width + 12, 20);
    context.fillStyle = "#fff0cf";
    context.fillText(label, midpoint.x, midpoint.y);
  }
  context.restore();
}

function drawTerrainPalette() {
  if (!terrainLayer?.checked || !terrainPalette?.show || terrainPalette.stops.length < 2) {
    return;
  }
  const width = Math.min(560, Math.max(320, canvas.clientWidth * 0.42));
  const height = 14;
  const left = (canvas.clientWidth - width) / 2;
  const top = canvas.clientHeight - 42;
  const firstHeight = terrainPalette.stops[0].heightMetres;
  const lastHeight = terrainPalette.stops.at(-1).heightMetres;
  context.save();
  context.fillStyle = "rgb(5 18 29 / 82%)";
  context.fillRect(left - 8, top - 20, width + 16, height + 30);
  for (let index = 0; index < terrainPalette.stops.length; index += 1) {
    const stop = terrainPalette.stops[index];
    const nextStop = terrainPalette.stops[index + 1];
    const start = (stop.heightMetres - firstHeight) / (lastHeight - firstHeight);
    const end = nextStop
      ? (nextStop.heightMetres - firstHeight) / (lastHeight - firstHeight)
      : 1;
    context.fillStyle = stop.colour;
    context.fillRect(left + width * start, top, width * (end - start), height);
  }
  context.strokeStyle = "rgb(225 245 255 / 72%)";
  context.strokeRect(left, top, width, height);
  context.fillStyle = "#e4f3fb";
  context.font = "12px system-ui, sans-serif";
  context.textAlign = "center";
  for (const stop of terrainPalette.stops) {
    const position = left + width * (stop.heightMetres - firstHeight) / (lastHeight - firstHeight);
    context.fillText(`${stop.heightMetres} m`, position, top - 6);
  }
  context.restore();
}

function viewportCrosshairPoint() {
  const containerBounds = container.getBoundingClientRect();
  const panelBounds = sidePanel.getBoundingClientRect();
  const usableLeft = Math.max(0, panelBounds.right - containerBounds.left);
  return { x: usableLeft + (canvas.clientWidth - usableLeft) / 2, y: canvas.clientHeight / 2 };
}

async function refreshTerrainPalette() {
  const [configurationResponse, paletteResponse] = await Promise.all([
    fetch(`${terrainServiceUrl}/api/config`),
    fetch(`${terrainServiceUrl}/api/palette`),
  ]);
  if (!configurationResponse.ok || !paletteResponse.ok) {
    throw new Error("terrain service did not return palette metadata");
  }
  const configuration = await configurationResponse.json();
  const palette = await paletteResponse.json();
  terrainPalette = { show: Boolean(configuration.showPalette), stops: palette.stops };
}

function resizeCanvas() {
  const bounds = container.getBoundingClientRect();
  const density = window.devicePixelRatio || 1;
  canvas.width = Math.round(bounds.width * density);
  canvas.height = Math.round(bounds.height * density);
  canvas.style.width = `${bounds.width}px`;
  canvas.style.height = `${bounds.height}px`;
  context.setTransform(density, 0, 0, density, 0, 0);
  context.imageSmoothingEnabled = true;
  invalidateAirLanesCache();
}

function drawTile(target, { bitmap, zoom, x, y }) {
  const tileScale = scale() / 2 ** zoom;
  const left = (x / 2 ** zoom - camera.x) * scale() + canvas.clientWidth / 2;
  const top = (y / 2 ** zoom - camera.y) * scale() + canvas.clientHeight / 2;
  target.drawImage(bitmap, left - 0.5, top - 0.5, tileScale + 1, tileScale + 1);
}

function drawTerrainTile(target, { bitmap, zoom, x, y }) {
  const tileScale = scale() / 2 ** zoom;
  const left = (x / 2 ** zoom - camera.x) * scale() + canvas.clientWidth / 2;
  const top = (y / 2 ** zoom - camera.y) * scale() + canvas.clientHeight / 2;
  target.save();
  target.beginPath();
  target.rect(left, top, tileScale, tileScale);
  target.clip();
  target.drawImage(bitmap, left, top, tileScale, tileScale);
  target.restore();
}

function drawWithOpacity(value, drawLayer) {
  context.save();
  context.globalAlpha = value / 10;
  drawLayer();
  context.restore();
}

function drawTileLayerWithOpacity(value, drawLayer) {
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  if (tileLayerCanvas.width !== width || tileLayerCanvas.height !== height) {
    tileLayerCanvas.width = width;
    tileLayerCanvas.height = height;
  }
  tileLayerContext.clearRect(0, 0, width, height);
  drawLayer(tileLayerContext);
  context.save();
  context.globalAlpha = value / 10;
  context.drawImage(tileLayerCanvas, 0, 0);
  context.restore();
}

function drawCountryLayer(target) {
  const baseZoom = Math.max(0, Math.min(18, Math.floor(camera.zoom)));
  const detailZoom = Math.min(18, Math.ceil(camera.zoom));
  let tiles = nearestCachedTiles(baseZoom, tileCache, tileKey);
  if (tiles.length === 0) {
    tiles = cachedVectorTilesInViewport();
  }
  if (detailZoom > baseZoom) {
    const visibleDetailTiles = visibleTiles(detailZoom);
    const detailTiles = exactCachedTiles(detailZoom, tileCache, tileKey);
    if (detailTiles.length === visibleDetailTiles.length) {
      tiles = detailTiles;
    }
  }
  for (const tile of tiles) {
    drawTile(target, tile);
  }
  const attributions = new Set(
    tiles.map((tile) => tile.source?.attribution).filter((item) => item),
  );
  attribution.textContent = [...attributions].join(" | ");
}

function drawImageryLayer(target) {
  const baseZoom = Math.max(0, Math.min(18, Math.floor(camera.zoom)));
  const detailZoom = Math.min(18, Math.ceil(camera.zoom));
  for (const tile of nearestCachedTiles(baseZoom, rasterTileCache, rasterTileKey)) {
    drawTile(target, tile);
  }

  if (detailZoom > baseZoom) {
    for (const tile of exactCachedTiles(detailZoom, rasterTileCache, rasterTileKey)) {
      drawTile(target, tile);
    }
  }
}

function drawCopernicusLayer(target) {
  const baseZoom = Math.max(0, Math.min(18, Math.floor(camera.zoom)));
  const detailZoom = Math.min(18, Math.ceil(camera.zoom));
  for (const tile of nearestCachedTiles(baseZoom, copernicusTileCache, copernicusTileKey)) {
    drawTile(target, tile);
  }
  if (detailZoom > baseZoom) {
    for (const tile of exactCachedTiles(detailZoom, copernicusTileCache, copernicusTileKey)) {
      drawTile(target, tile);
    }
  }
}

function drawTerrainLayer(target) {
  const baseZoom = Math.max(0, Math.min(18, Math.floor(camera.zoom)));
  const detailZoom = Math.min(18, Math.ceil(camera.zoom));
  for (const tile of nearestCachedTiles(baseZoom, terrainTileCache, terrainTileKey)) {
    drawTerrainTile(target, tile);
  }
  if (detailZoom > baseZoom) {
    for (const tile of exactCachedTiles(detailZoom, terrainTileCache, terrainTileKey)) {
      drawTerrainTile(target, tile);
    }
  }
}

function clipLineSegment(start, end, bounds) {
  const deltaX = end.x - start.x;
  const deltaY = end.y - start.y;
  let startRatio = 0;
  let endRatio = 1;
  for (const [change, distance] of [
    [-deltaX, start.x - bounds.left],
    [deltaX, bounds.right - start.x],
    [-deltaY, start.y - bounds.top],
    [deltaY, bounds.bottom - start.y],
  ]) {
    if (change === 0) {
      if (distance < 0) {
        return null;
      }
      continue;
    }
    const ratio = distance / change;
    if (change < 0) {
      startRatio = Math.max(startRatio, ratio);
    } else {
      endRatio = Math.min(endRatio, ratio);
    }
    if (startRatio > endRatio) {
      return null;
    }
  }
  return {
    start: { x: start.x + deltaX * startRatio, y: start.y + deltaY * startRatio },
    end: { x: start.x + deltaX * endRatio, y: start.y + deltaY * endRatio },
    startRatio,
    endRatio,
  };
}

function appendClippedLineString(coordinates, bounds) {
  for (let index = 1; index < coordinates.length; index += 1) {
    const clipped = clipLineSegment(
      screenPoint(...coordinates[index - 1]),
      screenPoint(...coordinates[index]),
      bounds,
    );
    if (clipped) {
      context.moveTo(clipped.start.x, clipped.start.y);
      context.lineTo(clipped.end.x, clipped.end.y);
    }
  }
}

function appendShapeGeometry(geometry, clipBounds = null) {
  if (geometry.type === "Point") {
    const point = screenPoint(...geometry.coordinates);
    context.moveTo(point.x + 5, point.y);
    context.arc(point.x, point.y, 5, 0, 2 * Math.PI);
  } else if (geometry.type === "LineString") {
    if (clipBounds) {
      appendClippedLineString(geometry.coordinates, clipBounds);
    } else {
      geometry.coordinates.forEach(([longitude, latitude], index) => {
        const point = screenPoint(longitude, latitude);
        if (index === 0) {
          context.moveTo(point.x, point.y);
        } else {
          context.lineTo(point.x, point.y);
        }
      });
      drawTerrainPalette();
    }
  } else if (geometry.type === "MultiLineString") {
    geometry.coordinates.forEach((line) => appendShapeGeometry(
      { type: "LineString", coordinates: line },
      clipBounds,
    ));
  } else if (geometry.type === "Polygon") {
    geometry.coordinates.forEach((ring) => {
      appendShapeGeometry({ type: "LineString", coordinates: ring });
      context.closePath();
    });
  } else if (geometry.type === "MultiPolygon") {
    geometry.coordinates.forEach((polygon) => appendShapeGeometry({ type: "Polygon", coordinates: polygon }));
  }
}

function shapeLabelPosition(coordinates) {
  const positions = [];
  function collect(value) {
    if (typeof value[0] === "number") {
      positions.push(value);
    } else {
      value.forEach(collect);
    }
  }
  collect(coordinates);
  if (positions.length === 0) {
    return null;
  }
  return screenPoint(
    positions.reduce((total, [longitude]) => total + longitude, 0) / positions.length,
    positions.reduce((total, [, latitude]) => total + latitude, 0) / positions.length,
  );
}

function extendCoordinateBounds(coordinates, bounds) {
  if (typeof coordinates[0] === "number") {
    const [longitude, latitude] = coordinates;
    bounds.west = Math.min(bounds.west, longitude);
    bounds.south = Math.min(bounds.south, latitude);
    bounds.east = Math.max(bounds.east, longitude);
    bounds.north = Math.max(bounds.north, latitude);
    return;
  }
  coordinates.forEach((child) => extendCoordinateBounds(child, bounds));
}

function geometryBounds(geometry) {
  const bounds = { west: Infinity, south: Infinity, east: -Infinity, north: -Infinity };
  if (geometry.type === "GeometryCollection") {
    geometry.geometries.forEach((child) => {
      const childBounds = geometryBounds(child);
      bounds.west = Math.min(bounds.west, childBounds.west);
      bounds.south = Math.min(bounds.south, childBounds.south);
      bounds.east = Math.max(bounds.east, childBounds.east);
      bounds.north = Math.max(bounds.north, childBounds.north);
    });
  } else {
    extendCoordinateBounds(geometry.coordinates, bounds);
  }
  return bounds;
}

function isNearViewport(geometry, viewport) {
  const bounds = geometryBounds(geometry);
  if (!Number.isFinite(bounds.west)) {
    return false;
  }
  const longitudeMargin = (viewport.east - viewport.west) * 0.15;
  const latitudeMargin = (viewport.north - viewport.south) * 0.15;
  return bounds.east >= viewport.west - longitudeMargin
    && bounds.west <= viewport.east + longitudeMargin
    && bounds.north >= viewport.south - latitudeMargin
    && bounds.south <= viewport.north + latitudeMargin;
}

function rgbaColor(color, opacity) {
  const hexadecimal = color.startsWith("#") ? color.slice(1) : "";
  if (!/^[\da-f]{6}$/i.test(hexadecimal)) {
    return color;
  }
  const red = Number.parseInt(hexadecimal.slice(0, 2), 16);
  const green = Number.parseInt(hexadecimal.slice(2, 4), 16);
  const blue = Number.parseInt(hexadecimal.slice(4, 6), 16);
  return `rgb(${red} ${green} ${blue} / ${opacity})`;
}

function drawFadingTrail(coordinates, color, bounds) {
  if (!Array.isArray(coordinates)) {
    return;
  }
  const points = coordinates.filter((point) => (
    Array.isArray(point)
    && point.length >= 2
    && Number.isFinite(point[0])
    && Number.isFinite(point[1])
  ));
  for (let index = 1; index < points.length; index += 1) {
    const clipped = clipLineSegment(
      screenPoint(...points[index - 1]),
      screenPoint(...points[index]),
      bounds,
    );
    if (!clipped) {
      continue;
    }
    context.beginPath();
    context.moveTo(clipped.start.x, clipped.start.y);
    context.lineTo(clipped.end.x, clipped.end.y);
    context.strokeStyle = rgbaColor(
      color,
      (index - 0.5 + (clipped.startRatio + clipped.endRatio) / 2) / (points.length - 1),
    );
    context.stroke();
  }
}

function isAirLanesFeature(properties) {
  return ["airlane-corridor", "airlane-sector-boundary", "controlled-airspace"].includes(properties.kind);
}

function liveTrackLabelMinimumZoom(properties) {
  if (typeof properties.icao24 === "string" && properties.icao24) {
    return 9;
  }
  if ((typeof properties.mmsi === "string" && properties.mmsi)
    || (typeof properties.mmsi === "number" && Number.isFinite(properties.mmsi))) {
    return 10;
  }
  return null;
}

function paddedLabelBox(label, labelY, width) {
  return {
    left: label.x - width / 2 - 8,
    top: labelY - 11,
    right: label.x + width / 2 + 8,
    bottom: labelY + 11,
  };
}

function overlapsLabelBox(labelBox, boxes) {
  return boxes.some((box) => (
    labelBox.left < box.right && labelBox.right > box.left
    && labelBox.top < box.bottom && labelBox.bottom > box.top
  ));
}

function drawShapeFeatures(features, liveTrackLabelBoxes = null) {
  context.save();
  const viewport = viewportBounds();
  const clipBounds = {
    left: -128,
    top: -128,
    right: canvas.clientWidth + 128,
    bottom: canvas.clientHeight + 128,
  };
  context.font = "12px system-ui, sans-serif";
  context.textAlign = "center";
  context.textBaseline = "middle";
  const airlaneLabelBoxes = [];
  for (const feature of features) {
    const { geometry, properties } = feature;
    if (!geometry) {
      continue;
    }
    if (!isNearViewport(geometry, viewport)) {
      continue;
    }
    const color = properties.color;
    const fillOpacity = Math.max(0, Math.min(1, Number(properties.fillOpacity) || 0.25));
    context.fillStyle = `${color}${Math.round(fillOpacity * 255).toString(16).padStart(2, "0")}`;
    context.strokeStyle = color;
    context.lineWidth = 2;
    context.setLineDash(properties.lineStyle === "dotted" ? [4, 5] : []);
    if (geometry.type === "LineString" && properties.kind === "track-trail" && properties.fadeTrail) {
      drawFadingTrail(geometry.coordinates, color, clipBounds);
    } else {
      context.beginPath();
      appendShapeGeometry(
        geometry,
        ["LineString", "MultiLineString"].includes(geometry.type) ? clipBounds : null,
      );
      if (["Polygon", "MultiPolygon", "Point"].includes(geometry.type)) {
        context.fill("evenodd");
      }
      context.stroke();
    }
    drawFeatureSymbol(properties, geometry);
    const label = ["track-trail", "adsb-boundary"].includes(properties.kind)
      ? null
      : shapeLabelPosition(geometry.coordinates);
    if (label && properties.label !== false) {
      const labelY = geometry.type === "Point" && properties.symbol
        ? label.y + 22
        : label.y;
      if (isAirLanesFeature(properties)) {
        const minimumZoom = properties.kind === "airlane-sector-boundary" ? 9 : 7;
        const metrics = context.measureText(properties.name);
        const labelBox = paddedLabelBox(label, labelY, metrics.width);
        const overlaps = overlapsLabelBox(labelBox, airlaneLabelBoxes);
        if (camera.zoom < minimumZoom || overlaps) {
          context.setLineDash([]);
          continue;
        }
        airlaneLabelBoxes.push(labelBox);
      }
      const liveMinimumZoom = geometry.type === "Point"
        && properties.kind === "track-position"
        && liveTrackLabelBoxes
        ? liveTrackLabelMinimumZoom(properties)
        : null;
      if (liveMinimumZoom !== null) {
        const metrics = context.measureText(properties.name);
        const labelBox = paddedLabelBox(label, labelY, metrics.width);
        if (camera.zoom < liveMinimumZoom || overlapsLabelBox(labelBox, liveTrackLabelBoxes)) {
          context.setLineDash([]);
          continue;
        }
        liveTrackLabelBoxes.push(labelBox);
      }
      if (!["track-position", "airlane-corridor", "airlane-sector-boundary", "controlled-airspace"].includes(properties.kind)) {
        context.lineWidth = 3;
        context.strokeStyle = "white";
        context.strokeText(properties.name, label.x, labelY);
      }
      context.fillStyle = color;
      context.fillText(properties.name, label.x, labelY);
    }
    context.setLineDash([]);
  }
  context.restore();
}

function drawCachedAirLanes() {
  if (!airLanesCache) {
    return;
  }
  const { camera: cachedCamera, width, height, padding } = airLanesCache;
  const zoomScale = scale() / (MapMath.TILE_SIZE * 2 ** cachedCamera.zoom);
  const left = canvas.clientWidth / 2 - zoomScale * (width / 2 + padding)
    + (cachedCamera.x - camera.x) * scale();
  const top = canvas.clientHeight / 2 - zoomScale * (height / 2 + padding)
    + (cachedCamera.y - camera.y) * scale();
  context.drawImage(
    airLanesCacheCanvas,
    left,
    top,
    (width + padding * 2) * zoomScale,
    (height + padding * 2) * zoomScale,
  );
}

function rebuildAirLanesCache() {
  airLanesCacheRebuildTimer = null;
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  if (width === 0 || height === 0) {
    return;
  }
  const density = window.devicePixelRatio || 1;
  const cacheWidth = width + AIRLANES_CACHE_PADDING * 2;
  const cacheHeight = height + AIRLANES_CACHE_PADDING * 2;
  airLanesCacheCanvas.width = Math.round(cacheWidth * density);
  airLanesCacheCanvas.height = Math.round(cacheHeight * density);
  const previousContext = context;
  context = airLanesCacheContext;
  context.setTransform(density, 0, 0, density, 0, 0);
  context.clearRect(0, 0, cacheWidth, cacheHeight);
  context.save();
  context.translate(AIRLANES_CACHE_PADDING, AIRLANES_CACHE_PADDING);
  drawFeatureGroups(airLanesShapeGroups);
  context.restore();
  context = previousContext;
  airLanesCache = {
    camera: { ...camera },
    width,
    height,
    padding: AIRLANES_CACHE_PADDING,
    revision: airLanesCacheRevision,
  };
}

function invalidateAirLanesCache({ immediate = false } = {}) {
  if (airLanesCacheRebuildTimer !== null) {
    window.clearTimeout(airLanesCacheRebuildTimer);
  }
  if (immediate) {
    rebuildAirLanesCache();
    return;
  }
  airLanesCacheRebuildTimer = window.setTimeout(
    rebuildAirLanesCache,
    AIRLANES_CACHE_SETTLE_DELAY,
  );
}

function drawShapesLayer() {
  drawCachedAirLanes();
  drawFeatureGroups(liveShapeFeatureGroups, []);
  drawShapeFeatures(contextActionFeatures, []);
}

function drawFeatureGroups(groups, liveTrackLabelBoxes = null) {
  for (const { features, opacity } of groups) {
    drawWithOpacity(opacity, () => drawShapeFeatures(features, liveTrackLabelBoxes));
  }
}

function drawFeatureSymbol(properties, geometry) {
  if (geometry.type !== "Point" || !properties.symbol) {
    return;
  }
  const definition = symbolDefinitions.get(properties.symbol) || symbolDefinitions.get("undefined");
  const image = definition && symbolImage(definition, properties.color);
  if (!image?.complete) {
    return;
  }
  const point = screenPoint(...geometry.coordinates);
  if (point.x < -32 || point.x > canvas.clientWidth + 32 || point.y < -32 || point.y > canvas.clientHeight + 32) {
    return;
  }
  context.save();
  context.translate(point.x, point.y);
  if (definition.orientation === "orientable") {
    context.rotate((Number(properties.bearingDegrees) || 0) * Math.PI / 180);
  }
  const size = 28 * definition.scale;
  context.drawImage(image, -size / 2, -size / 2, size, size);
  context.restore();
}

function overlayKey(service) {
  return `${service.url}${service.endpoint}`;
}

function enabledOverlayServices() {
  return overlayServices.filter((service) => service.enabled);
}

function isLiveOverlayService(service) {
  return service.type === "adsb" || service.type === "ais";
}

async function setLiveOverlayActivity(service) {
  if (!isLiveOverlayService(service)) {
    return;
  }
  const response = await fetch(`${service.url}/api/activity`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ active: service.enabled }),
  });
  if (!response.ok) {
    throw new Error(`activity update returned ${response.status}`);
  }
}

async function synchronizeLiveOverlayActivity() {
  const results = await Promise.allSettled(
    overlayServices.filter(isLiveOverlayService).map(setLiveOverlayActivity),
  );
  const failures = results.filter((result) => result.status === "rejected");
  if (failures.length > 0) {
    throw new Error(failures.map((failure) => failure.reason.message).join("; "));
  }
}

function rebuildOverlayFeatures() {
  const collections = enabledOverlayServices()
    .map((service) => ({ service, collection: overlayCollections.get(overlayKey(service)) }))
    .filter(({ collection }) => collection);
  const revision = collections
    .map(({ service, collection }) => `${collection.revision}:${service.opacity}`)
    .join(":");
  const nextAirLanesRevision = overlayServices
    .filter((service) => service.enabled && service.type === "airlanes")
    .map((service) => {
      const collection = overlayCollections.get(overlayKey(service));
      return `${overlayKey(service)}:${collection?.revision ?? "unavailable"}:${service.opacity}`;
    })
    .join("|");
  if (revision !== overlayRevision) {
    shapes = collections.flatMap(({ collection }) => collection.features);
    airLanesShapeGroups = collections
      .map(({ service, collection }) => ({
        opacity: service.opacity,
        features: collection.features.filter((feature) => isAirLanesFeature(feature.properties)),
      }))
      .filter(({ features }) => features.length > 0);
    liveShapeFeatureGroups = collections
      .map(({ service, collection }) => ({
        opacity: service.opacity,
        features: collection.features.filter((feature) => !isAirLanesFeature(feature.properties)),
      }))
      .filter(({ features }) => features.length > 0);
    overlayRevision = revision;
  }
  if (nextAirLanesRevision !== airLanesCacheRevision) {
    airLanesCacheRevision = nextAirLanesRevision;
    invalidateAirLanesCache({ immediate: true });
  }
}

function overlayDisplayName(service) {
  if (service.type === "adsb") {
    return "ADS-B live flights";
  }
  if (service.type === "ais") {
    return "AIS vessels";
  }
  if (service.type === "airlanes") {
    return "AirLanes";
  }
  if (service.type === "shapes") {
    return `Shapes: ${service.id}`;
  }
  return `GeoJSON: ${service.id}`;
}

function overlayLayerSelections() {
  return Object.fromEntries(
    [...overlayServices, ...symbolServices].map((service) => [service.id, service.enabled]),
  );
}

function overlaySourceOpacities() {
  return Object.fromEntries(overlayServices.map((service) => [service.id, service.opacity]));
}

function updateOpacityControls() {
  for (const [layer, value] of Object.entries(layerOpacity)) {
    const output = document.querySelector(`#${layer}-opacity`);
    if (output) {
      output.value = String(value).padStart(2, "0");
    }
  }
}

function changeLayerOpacity(layer, change) {
  layerOpacity[layer] = Math.max(1, Math.min(10, layerOpacity[layer] + change));
  updateOpacityControls();
  mapStateVersion += 1;
  refreshMap();
}

function configurationInput(field, value) {
  const container = document.createElement("label");
  container.className = "service-config-field";
  if (field.type === "boolean") {
    container.classList.add("service-config-toggle");
  }
  if (field.type === "color") {
    container.classList.add("service-config-color");
  }
  container.append(field.label);
  if (field.type === "position") {
    const positionFields = document.createElement("div");
    positionFields.className = "position-fields";
    for (const key of ["longitude", "latitude"]) {
      const input = document.createElement("input");
      input.type = "number";
      input.step = "any";
      input.value = value?.[key] ?? "";
      input.dataset.configKey = `${field.key}.${key}`;
      input.required = true;
      positionFields.append(input);
    }
    container.append(positionFields);
  } else {
    const input = document.createElement("input");
    input.type = field.type === "boolean" ? "checkbox" : field.type;
    if (field.type === "password") {
      input.autocomplete = "new-password";
    }
    input.checked = field.type === "boolean" && Boolean(value);
    if (field.type !== "boolean") {
      input.value = value ?? "";
      input.required = field.required !== false;
      input.placeholder = field.placeholder ?? "";
    }
    input.min = field.minimum ?? "";
    input.max = field.maximum ?? field.maximumLength ?? "";
    input.step = field.step ?? (field.type === "number" ? "any" : "");
    input.dataset.configKey = field.key;
    container.append(input);
  }
  return container;
}

function setServiceConfigurationDialogMode({ reviewOnly = false } = {}) {
  serviceConfigDialog.classList.toggle("symbol-review-dialog", reviewOnly);
  serviceConfigCancel.textContent = reviewOnly ? "Close" : "Cancel";
  serviceConfigSubmit.hidden = reviewOnly;
}

function configureGrid() {
  setServiceConfigurationDialogMode();
  configurationSession = { kind: "grid" };
  serviceConfigTitle.textContent = "Configure Lat/Lon Grid";
  serviceConfigFields.replaceChildren(
    configurationInput(
      { key: "cursorCrosshair", label: "Show crosshair at viewport center", type: "boolean" },
      gridCrosshair,
    ),
  );
  serviceConfigActions.replaceChildren();
  serviceConfigStatus.textContent = "";
  serviceConfigProgress.hidden = true;
  serviceConfigDialog.showModal();
}

function symbolReviewCard(definition) {
  const card = document.createElement("article");
  card.className = "symbol-review-card";
  const image = document.createElement("img");
  const svg = definition.colorMode === "colorable"
    ? definition.svg.replace("<svg ", '<svg color="#54d8ff" ')
    : definition.svg;
  image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
  image.alt = `${definition.name} symbol`;
  const name = document.createElement("span");
  name.className = "symbol-review-name";
  name.textContent = definition.name;
  card.append(image, name);
  return card;
}

async function configureSymbolReview(service, manifest) {
  const response = await fetch(`${service.url}${manifest.dataEndpoint.path}`);
  if (!response.ok) {
    throw new Error(`symbol catalogue returned ${response.status}`);
  }
  const collection = await response.json();
  const definitions = (collection.symbols || []).filter(validateSymbolDefinition).sort((left, right) => left.name.localeCompare(right.name));
  if (definitions.length === 0) {
    throw new Error("symbol catalogue contains no valid definitions");
  }
  setServiceConfigurationDialogMode({ reviewOnly: true });
  configurationSession = { kind: "symbol-review" };
  serviceConfigTitle.textContent = `Review ${manifest.displayName}`;
  const grid = document.createElement("div");
  grid.className = "symbol-review-grid";
  grid.setAttribute("aria-label", `${manifest.displayName} catalogue`);
  grid.append(...definitions.map(symbolReviewCard));
  serviceConfigFields.replaceChildren(grid);
  serviceConfigActions.replaceChildren();
  serviceConfigStatus.textContent = `${definitions.length} symbols published by this service.`;
  serviceConfigProgress.hidden = true;
  serviceConfigDialog.showModal();
}

function configurationPayload(manifest) {
  const configuration = {};
  for (const field of manifest.configuration.fields) {
    if (field.type === "position") {
      configuration[field.key] = {
        longitude: Number(serviceConfigFields.querySelector(`[data-config-key="${field.key}.longitude"]`).value),
        latitude: Number(serviceConfigFields.querySelector(`[data-config-key="${field.key}.latitude"]`).value),
      };
    } else {
      const input = serviceConfigFields.querySelector(`[data-config-key="${field.key}"]`);
      configuration[field.key] = field.type === "boolean"
        ? input.checked
        : field.type === "number" ? Number(input.value) : input.value;
    }
  }
  return configuration;
}

async function saveServiceConfiguration() {
  const { service, manifest, revision } = configurationSession;
  const response = await fetch(`${service.url}${manifest.configuration.writePath}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", "If-Match": revision },
    body: JSON.stringify(configurationPayload(manifest)),
  });
  if (!response.ok) {
    throw new Error(`service configuration returned ${response.status}`);
  }
  const updated = await response.json();
  configurationSession.revision = updated.revision;
  if (service.type === "terrain") {
    await refreshTerrainPalette();
  }
  if (service.type === "vecmap") {
    clearVectorTileCache();
    await refreshVectorTileRevision();
    refreshMap();
  } else if (service.type === "copernicus") {
    await refreshCopernicusRevision();
    refreshMap();
  }
}

async function waitForServiceAction(service, action) {
  if (!action.statusPath) {
    return;
  }
  while (true) {
    await new Promise((resolve) => window.setTimeout(resolve, 1000));
    const response = await fetch(`${service.url}${action.statusPath}`);
    if (!response.ok) {
      throw new Error(`service action status returned ${response.status}`);
    }
    const result = await response.json();
    serviceConfigStatus.textContent = result.message || "Action status updated.";
    const progress = Math.max(0, Math.min(100, Number(result.progress) || 0));
    const processed = Math.max(0, Number(result.processed) || 0);
    const total = Math.max(0, Number(result.total) || 0);
    const retained = Math.max(0, Number(result.retained) || 0);
    const detail = total > 0
      ? `${processed.toLocaleString()} / ${total.toLocaleString()} · ${retained.toLocaleString()} retained`
      : "";
    serviceConfigProgress.hidden = false;
    serviceConfigProgressLabel.textContent = [result.message, detail].filter(Boolean).join(" ");
    serviceConfigProgressValue.textContent = detail || `${progress}%`;
    serviceConfigProgressBar.style.width = `${progress}%`;
    const active = ["running", "processing"].includes(result.status);
    if (!active) {
      serviceConfigProgress.hidden = true;
    }
    if (result.status === "failed") {
      throw new Error(result.message || "service action failed");
    }
    if (!active) {
      if (service.type === "terrain") {
        await refreshTerrainRevision();
      }
      return;
    }
  }
}

function stopServiceActivityPolling() {
  if (serviceActivityTimer !== null) {
    window.clearInterval(serviceActivityTimer);
    serviceActivityTimer = null;
  }
}

async function refreshServiceActivity(service, statusPath) {
  const response = await fetch(`${service.url}${statusPath}`);
  if (!response.ok) {
    throw new Error(`service activity status returned ${response.status}`);
  }
  const result = await response.json();
  if (result.status !== "processing") {
    serviceConfigProgress.hidden = true;
    return;
  }
  const progress = Math.max(0, Math.min(100, Number(result.progress) || 0));
  const processed = Math.max(0, Number(result.processed) || 0);
  const total = Math.max(0, Number(result.total) || 0);
  const queued = Math.max(0, Number(result.queued ?? result.retained) || 0);
  const detail = total > 0
    ? `${processed.toLocaleString()} / ${total.toLocaleString()} · ${queued.toLocaleString()} queued`
    : "";
  serviceConfigProgress.hidden = false;
  serviceConfigProgressLabel.textContent = [result.message, detail].filter(Boolean).join(" ");
  serviceConfigProgressValue.textContent = detail || `${progress}%`;
  serviceConfigProgressBar.style.width = `${progress}%`;
}

function startServiceActivityPolling(service, manifest) {
  stopServiceActivityPolling();
  const statusPath = manifest.configuration.activityStatusPath;
  if (!statusPath) {
    return;
  }
  const refresh = () => {
    refreshServiceActivity(service, statusPath).catch((error) => {
      serviceConfigStatus.textContent = `Unable to load service activity: ${error.message}`;
    });
  };
  refresh();
  serviceActivityTimer = window.setInterval(refresh, 500);
}

async function configureService(service) {
  const manifestResponse = await fetch(`${service.url}/api/kody/manifest`);
  if (!manifestResponse.ok) {
    throw new Error(`service manifest returned ${manifestResponse.status}`);
  }
  const manifest = await manifestResponse.json();
  if (manifest.dataEndpoint?.format === "kody-symbols") {
    await configureSymbolReview(service, manifest);
    return;
  }
  let configuration = manifest.configuration.initialConfiguration;
  if (!configuration) {
    const configurationResponse = await fetch(`${service.url}${manifest.configuration.readPath}`);
    if (!configurationResponse.ok) {
      throw new Error(`service configuration returned ${configurationResponse.status}`);
    }
    configuration = await configurationResponse.json();
  }
  setServiceConfigurationDialogMode();
  configurationSession = { service, manifest, revision: configuration.revision };
  serviceConfigTitle.textContent = `Configure ${manifest.displayName}`;
  const fields = manifest.configuration.fields.map((field) => {
    if (field.secretStoredKey && configuration[field.secretStoredKey]) {
      return configurationInput(
        {
          ...field,
          label: `${field.label} (saved locally)`,
          placeholder: "******** (enter a replacement to change it)",
        },
        configuration[field.key],
      );
    }
    return configurationInput(field, configuration[field.key]);
  });
  if (fields.length === 0) {
    const emptyState = document.createElement("p");
    emptyState.className = "empty-config-state";
    emptyState.textContent = "This service has no configurable settings.";
    serviceConfigFields.replaceChildren(emptyState);
  } else {
    serviceConfigFields.replaceChildren(...fields);
  }
  serviceConfigActions.replaceChildren(
    ...(manifest.configuration.actions || []).map((action) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = typeof action.confirmationMessage === "string"
        ? "danger-action"
        : "primary-action";
      button.textContent = action.label;
      button.addEventListener("click", async () => {
        try {
          if (
            typeof action.confirmationMessage === "string"
            && !window.confirm(action.confirmationMessage)
          ) {
            return;
          }
          if (typeof action.confirmationPath === "string") {
            const assessmentResponse = await fetch(`${service.url}${action.confirmationPath}`);
            if (!assessmentResponse.ok) {
              throw new Error(`service assessment returned ${assessmentResponse.status}`);
            }
            const assessment = await assessmentResponse.json();
            if (assessment.requiresConfirmation && !window.confirm(assessment.message)) {
              return;
            }
          }
          button.disabled = true;
          serviceConfigProgress.hidden = false;
          serviceConfigProgressLabel.textContent = "Submitting action...";
          serviceConfigProgressValue.textContent = "0%";
          serviceConfigProgressBar.style.width = "0%";
          await saveServiceConfiguration();
          const response = await fetch(`${service.url}${action.path}`, { method: action.method || "POST" });
          if (!response.ok) {
            throw new Error(`service action returned ${response.status}`);
          }
          const result = await response.json();
          serviceConfigStatus.textContent = result.message || `${action.label} started.`;
          await waitForServiceAction(service, action);
          if (service.type === "vecmap") {
            clearVectorTileCache();
            await refreshVectorTileRevision();
            refreshMap();
          }
        } catch (error) {
          serviceConfigProgress.hidden = true;
          serviceConfigStatus.textContent = `Unable to ${action.label.toLowerCase()}: ${error.message}`;
        } finally {
          button.disabled = false;
        }
      });
      return button;
    }),
  );
  serviceConfigStatus.textContent = "";
  serviceConfigProgress.hidden = true;
  serviceConfigDialog.showModal();
  startServiceActivityPolling(service, manifest);
}

function renderOverlaySourceControls(savedSelections = {}, savedOpacities = {}) {
  dataSourceList.replaceChildren();
  liveSourceList.replaceChildren();
  for (const service of overlayServices) {
    service.enabled = savedSelections[service.id] ?? true;
    service.opacity = savedOpacities[service.id] ?? 10;
    const row = document.createElement("div");
    row.className = "overlay-source-row";
    const label = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = service.enabled;
    checkbox.addEventListener("change", () => {
      service.enabled = checkbox.checked;
      overlayRevision = null;
      rebuildOverlayFeatures();
      saveLayerSelection();
      setLiveOverlayActivity(service).then(() => {
        if (!service.enabled) {
          return;
        }
        refreshShapes().catch((error) => {
          status.textContent = `Unable to refresh ${overlayDisplayName(service)}: ${error.message}`;
        });
      }).catch((error) => {
        status.textContent = `Unable to update ${overlayDisplayName(service)} activity: ${error.message}`;
      });
    });
    const opacity = document.createElement("div");
    opacity.className = "opacity-control";
    opacity.setAttribute("aria-label", `${overlayDisplayName(service)} opacity`);
    const decrease = document.createElement("button");
    decrease.type = "button";
    decrease.textContent = "−";
    decrease.setAttribute("aria-label", `Decrease ${overlayDisplayName(service)} opacity`);
    const value = document.createElement("output");
    value.value = String(service.opacity).padStart(2, "0");
    const increase = document.createElement("button");
    increase.type = "button";
    increase.textContent = "+";
    increase.setAttribute("aria-label", `Increase ${overlayDisplayName(service)} opacity`);
    const changeOpacity = (change) => {
      service.opacity = Math.max(1, Math.min(10, service.opacity + change));
      value.value = String(service.opacity).padStart(2, "0");
      rebuildOverlayFeatures();
      saveLayerSelection();
    };
    decrease.addEventListener("click", () => changeOpacity(-1));
    increase.addEventListener("click", () => changeOpacity(1));
    opacity.append(decrease, value, increase);
    const configure = document.createElement("button");
    configure.type = "button";
    configure.className = "configure-service";
    configure.textContent = "⚙";
    configure.title = `Configure ${overlayDisplayName(service)}`;
    configure.setAttribute("aria-label", configure.title);
    configure.addEventListener("click", () => {
      configureService(service).catch((error) => {
        status.textContent = `Unable to configure ${overlayDisplayName(service)}: ${error.message}`;
      });
    });
    label.append(checkbox, ` ${overlayDisplayName(service)}`);
    row.append(label, opacity, configure);
    (["shapes", "airlanes"].includes(service.type) ? dataSourceList : liveSourceList).append(row);
  }
}

function renderSymbolSourceControls(savedSelections = {}) {
  symbolSourceList.replaceChildren();
  for (const service of symbolServices) {
    service.enabled = savedSelections[service.id] ?? true;
    const row = document.createElement("div");
    row.className = "overlay-source-row";
    const label = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = service.enabled;
    checkbox.addEventListener("change", () => {
      service.enabled = checkbox.checked;
      symbolDefinitions.clear();
      saveLayerSelection();
      if (service.enabled) {
        refreshSymbols().catch((error) => {
          status.textContent = `Symbols unavailable: ${error.message}`;
        });
      }
    });
    const configure = document.createElement("button");
    configure.type = "button";
    configure.className = "configure-service";
    configure.textContent = "⚙";
    configure.title = `Review Symbols: ${service.id}`;
    configure.setAttribute("aria-label", configure.title);
    configure.addEventListener("click", () => {
      configureService(service).catch((error) => {
        status.textContent = `Unable to review Symbols: ${error.message}`;
      });
    });
    label.append(checkbox, ` Symbols: ${service.id}`);
    row.append(label, configure);
    symbolSourceList.append(row);
  }
}

serviceConfigCancel.addEventListener("click", () => {
  serviceConfigDialog.close();
});

serviceConfigForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!configurationSession) {
    return;
  }
  if (configurationSession.kind === "symbol-review") {
    serviceConfigDialog.close();
    return;
  }
  if (configurationSession.kind === "grid") {
    gridCrosshair = serviceConfigFields.querySelector('[data-config-key="cursorCrosshair"]').checked;
    mapStateVersion += 1;
    serviceConfigDialog.close();
    saveMapView();
    return;
  }
  try {
    await saveServiceConfiguration();
    serviceConfigDialog.close();
    await refreshShapes();
  } catch (error) {
    serviceConfigStatus.textContent = `Unable to save: ${error.message}`;
  }
});

function viewportBounds() {
  const halfWidth = canvas.clientWidth / (2 * scale());
  const halfHeight = canvas.clientHeight / (2 * scale());
  const northwest = MapMath.worldToLonLat(camera.x - halfWidth, camera.y - halfHeight);
  const southeast = MapMath.worldToLonLat(camera.x + halfWidth, camera.y + halfHeight);
  return {
    west: Math.max(-180, northwest.longitude),
    south: Math.max(-MapMath.MAX_LATITUDE, southeast.latitude),
    east: Math.min(180, southeast.longitude),
    north: Math.min(MapMath.MAX_LATITUDE, northwest.latitude),
  };
}

async function publishViewport() {
  const bounds = viewportBounds();
  try {
    const response = await fetch(`${configurationServiceUrl}/api/context/viewport`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...bounds, zoom: camera.zoom }),
    });
    if (!response.ok) {
      throw new Error(`configuration service returned ${response.status}`);
    }
  } catch (error) {
    status.textContent = `Unable to publish map viewport: ${error.message}`;
  }
}

function scheduleViewportPublish() {
  if (viewportPublishTimer !== null) {
    window.clearTimeout(viewportPublishTimer);
  }
  viewportPublishTimer = window.setTimeout(() => {
    viewportPublishTimer = null;
    publishViewport();
  }, 200);
  scheduleViewportElevation();
}

function viewportCursorLocation() {
  const point = viewportCrosshairPoint();
  return MapMath.worldToLonLat(
    camera.x + (point.x - canvas.clientWidth / 2) / scale(),
    camera.y + (point.y - canvas.clientHeight / 2) / scale(),
  );
}

function coordinateDecimalPlaces() {
  return Math.min(5, Math.floor(Math.max(0, camera.zoom) * 5 / 14));
}

async function refreshViewportElevation() {
  if (!terrainLayer?.checked || !mapServices.has("terrain")) {
    viewportElevation = null;
    return;
  }
  const request = ++viewportElevationRequest;
  const location = viewportCursorLocation();
  try {
    const response = await fetch(`${terrainServiceUrl}/api/elevation?longitude=${encodeURIComponent(location.longitude)}&latitude=${encodeURIComponent(location.latitude)}`);
    if (!response.ok) {
      throw new Error(`terrain service returned ${response.status}`);
    }
    const result = await response.json();
    if (request === viewportElevationRequest) {
      viewportElevation = typeof result.heightMetres === "number" ? result.heightMetres : null;
    }
  } catch (error) {
    if (request === viewportElevationRequest) {
      viewportElevation = null;
      status.textContent = `Unable to load terrain elevation: ${error.message}`;
    }
  }
}

function scheduleViewportElevation() {
  viewportElevation = null;
  if (viewportElevationTimer !== null) {
    window.clearTimeout(viewportElevationTimer);
  }
  viewportElevationTimer = window.setTimeout(() => {
    viewportElevationTimer = null;
    refreshViewportElevation();
  }, 200);
}

function screenPoint(longitude, latitude) {
  const point = MapMath.lonLatToWorld(longitude, latitude);
  return {
    x: (point.x - camera.x) * scale() + canvas.clientWidth / 2,
    y: (point.y - camera.y) * scale() + canvas.clientHeight / 2,
  };
}

function gridWorldInterval() {
  const span = canvas.clientWidth / scale();
  const magnitude = 10 ** Math.floor(Math.log10(span / 7.5));
  const candidates = [0.1, 0.2, 0.5, 1, 2, 5, 10].map((value) => value * magnitude);
  return candidates
    .filter((interval) => {
      const count = span / interval;
      return count >= 5 && count <= 10;
    })
    .sort((left, right) => Math.abs(span / left - 7.5) - Math.abs(span / right - 7.5))[0]
    || candidates.at(-1);
}

function formatDegrees(value, interval, positiveDirection, negativeDirection) {
  const precision = interval < 1 ? Math.ceil(-Math.log10(interval)) : 0;
  const degrees = Math.abs(value).toFixed(precision);
  if (value === 0) {
    return `${degrees}°`;
  }
  return `${degrees}°${value > 0 ? positiveDirection : negativeDirection}`;
}

function formatGridSize(value) {
  return Number(value.toPrecision(2)).toString();
}

function updateGridSizeReadout() {
  gridSizeReadout.hidden = !gridLayer.checked;
  if (!gridLayer.checked) {
    return;
  }
  const interval = gridWorldInterval();
  const centre = MapMath.worldToLonLat(camera.x, camera.y);
  const nextLatitude = MapMath.worldToLonLat(camera.x, camera.y + interval).latitude;
  const latitudeDegrees = Math.abs(nextLatitude - centre.latitude);
  const longitudeDegrees = interval * 360;
  const latitudeKilometres = latitudeDegrees * 111.32;
  const longitudeKilometres = longitudeDegrees * 111.32 * Math.cos(centre.latitude * Math.PI / 180);
  gridSize.textContent = `Lat: ${formatGridSize(latitudeDegrees)} deg, ${formatGridSize(latitudeKilometres)} km · Lon: ${formatGridSize(longitudeDegrees)} deg, ${formatGridSize(longitudeKilometres)} km`;
}

function drawGridLayer() {
  const interval = gridWorldInterval();
  const halfWidth = canvas.clientWidth / (2 * scale());
  const halfHeight = canvas.clientHeight / (2 * scale());
  const west = camera.x - halfWidth;
  const east = camera.x + halfWidth;
  const north = camera.y - halfHeight;
  const south = camera.y + halfHeight;
  context.save();
  context.strokeStyle = "#fff";
  context.fillStyle = "#fff";
  context.lineWidth = 1;
  context.font = "10px system-ui, sans-serif";
  context.textBaseline = "top";

  for (let worldX = Math.ceil(west / interval) * interval;
    worldX <= east;
    worldX += interval) {
    const point = { x: (worldX - camera.x) * scale() + canvas.clientWidth / 2 };
    const longitude = MapMath.worldToLonLat(worldX, camera.y).longitude;
    context.beginPath();
    context.moveTo(point.x, 0);
    context.lineTo(point.x, canvas.clientHeight);
    context.stroke();
    const label = formatDegrees(longitude, interval * 360, "E", "W");
    context.fillText(label, point.x + 3, 3);
    context.textBaseline = "bottom";
    context.fillText(label, point.x + 3, canvas.clientHeight - 3);
    context.textBaseline = "top";
  }

  for (let worldY = Math.ceil(north / interval) * interval;
    worldY <= south;
    worldY += interval) {
    const point = { y: (worldY - camera.y) * scale() + canvas.clientHeight / 2 };
    const latitude = MapMath.worldToLonLat(camera.x, worldY).latitude;
    const adjacentLatitude = MapMath.worldToLonLat(camera.x, worldY + interval).latitude;
    context.beginPath();
    context.moveTo(0, point.y);
    context.lineTo(canvas.clientWidth, point.y);
    context.stroke();
    const label = formatDegrees(latitude, Math.abs(adjacentLatitude - latitude), "N", "S");
    context.fillText(label, 3, point.y + 3);
    context.textAlign = "right";
    context.fillText(label, canvas.clientWidth - 3, point.y + 3);
    context.textAlign = "left";
  }
  if (gridCrosshair) {
    const definition = symbolDefinitions.get("crosshair");
    const image = definition && symbolImage(definition, "#ffffff");
    if (image?.complete) {
      const point = viewportCrosshairPoint();
      const size = 28 * definition.scale;
      context.drawImage(image, point.x - size / 2, point.y - size / 2, size, size);
    }
  }
  context.restore();
}

function draw() {
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  context.clearRect(0, 0, width, height);
  context.fillStyle = "#202020";
  context.fillRect(0, 0, width, height);
  Object.values(layers).forEach((layer) => {
    if (layer.isVisible()) {
      layer.draw();
    }
  });
  drawTerrainPalette();
  zoomLevel.value = `Zoom ${camera.zoom.toFixed(1)}`;
  const location = viewportCursorLocation();
  const decimalPlaces = coordinateDecimalPlaces();
  const elevation = viewportElevation === null ? "" : ` · ${Math.round(viewportElevation)} m`;
  coordinates.textContent = `${location.latitude.toFixed(decimalPlaces)}°, ${location.longitude.toFixed(decimalPlaces)}°${elevation}`;
  updateGridSizeReadout();
}

function visibleTiles(zoom = Math.max(0, Math.min(18, Math.floor(camera.zoom)))) {
  const tilesPerAxis = 2 ** zoom;
  const halfWidth = canvas.clientWidth / (2 * scale());
  const halfHeight = canvas.clientHeight / (2 * scale());
  const minimumX = Math.max(0, Math.floor((camera.x - halfWidth) * tilesPerAxis));
  const maximumX = Math.min(tilesPerAxis - 1, Math.floor((camera.x + halfWidth) * tilesPerAxis));
  const minimumY = Math.max(0, Math.floor((camera.y - halfHeight) * tilesPerAxis));
  const maximumY = Math.min(tilesPerAxis - 1, Math.floor((camera.y + halfHeight) * tilesPerAxis));
  const tiles = [];
  for (let y = minimumY; y <= maximumY; y += 1) {
    for (let x = minimumX; x <= maximumX; x += 1) {
      tiles.push({ zoom, x, y });
    }
  }
  return tiles;
}

function renderMap(timestamp) {
  draw();
  frameTimes.push(timestamp);
  while (frameTimes[0] < timestamp - 1000) {
    frameTimes.shift();
  }
  const previousUpdatesPerSecond = updatesPerSecond;
  updatesPerSecond = frameTimes.length;
  if (timestamp - lastUpsDisplayUpdate >= 1000) {
    upsLevel.value = `UPS ${updatesPerSecond}`;
    upsLevel.classList.toggle("below-target", updatesPerSecond < 50);
    lastUpsDisplayUpdate = timestamp;
  }
  if (previousUpdatesPerSecond < 50 && updatesPerSecond >= 50) {
    requestVisibleTiles();
  }
  requestAnimationFrame(renderMap);
}

function tileKey({ zoom, x, y }) {
  return `${vectorMapServiceUrl}/vector/${vectorTileRevision || "unversioned"}/false/${zoom}/${x}/${y}`;
}

function vectorTileZoom() {
  const cameraZoom = Math.floor(camera.zoom);
  return [...vectorTileZooms].reverse().find((zoom) => zoom <= cameraZoom)
    ?? vectorTileZooms[0];
}

function rasterTileKey({ zoom, x, y }) {
  return `${satelliteMapServiceUrl}/raster/${zoom}/${x}/${y}`;
}

function copernicusTileKey({ zoom, x, y }) {
  return `${copernicusServiceUrl}/raster/${zoom}/${x}/${y}`;
}

function terrainTileKey({ zoom, x, y }) {
  return `${terrainServiceUrl}/raster/${zoom}/${x}/${y}`;
}

function exactCachedTiles(zoom, cache, keyForTile) {
  return visibleTiles(zoom)
    .map((tile) => {
      const cached = cache.get(keyForTile(tile));
      if (!cached) {
        return null;
      }
      return {
        ...tile,
        bitmap: cached.bitmap || cached,
        source: cached.source,
      };
    })
    .filter((tile) => tile !== null);
}

function nearestCachedTiles(zoom, cache, keyForTile) {
  const nearestTiles = new Map();
  for (const tile of visibleTiles(zoom)) {
    for (let parentZoom = zoom; parentZoom >= 0; parentZoom -= 1) {
      const shift = zoom - parentZoom;
      const parent = {
        zoom: parentZoom,
        x: tile.x >> shift,
        y: tile.y >> shift,
      };
      const key = keyForTile(parent);
      const cached = cache.get(key);
      if (cached) {
        nearestTiles.set(key, { ...parent, ...(cached.bitmap ? cached : { bitmap: cached }) });
        break;
      }
    }
  }
  return [...nearestTiles.values()].sort((left, right) => left.zoom - right.zoom);
}

function cachedVectorTilesInViewport() {
  const halfWidth = canvas.clientWidth / (2 * scale());
  const halfHeight = canvas.clientHeight / (2 * scale());
  const viewport = {
    west: camera.x - halfWidth,
    east: camera.x + halfWidth,
    north: camera.y - halfHeight,
    south: camera.y + halfHeight,
  };
  return [...tileCache.values()]
    .filter((tile) => {
      const tileSpan = 1 / 2 ** tile.zoom;
      const west = tile.x * tileSpan;
      const north = tile.y * tileSpan;
      return west < viewport.east
        && west + tileSpan > viewport.west
        && north < viewport.south
        && north + tileSpan > viewport.north;
    })
    .sort((left, right) => left.zoom - right.zoom);
}

function clearRasterTileCache() {
  rasterTileCache.forEach((bitmap) => bitmap.close());
  rasterTileCache.clear();
  requestedRasterTileKeys.clear();
}

function clearCopernicusTileCache() {
  copernicusTileCache.forEach((bitmap) => bitmap.close());
  copernicusTileCache.clear();
  requestedCopernicusTileKeys.clear();
}

function clearVectorTileCache() {
  vectorTileGeneration += 1;
  tileCache.forEach((tile) => tile.bitmap.close());
  tileCache.clear();
  requestedTileKeys.clear();
}

function clearTerrainTileCache() {
  terrainTileCache.forEach((bitmap) => bitmap.close());
  terrainTileCache.clear();
  requestedTerrainTileKeys.clear();
}

async function refreshRasterRevision() {
  if (!mapServices.has("satmap") || !imageryLayer.checked) {
    return;
  }

  const response = await fetch(`${satelliteMapServiceUrl}/api/raster/revision`);
  if (!response.ok) {
    throw new Error(`satellite map service returned ${response.status} for raster revision`);
  }
  const { revision } = await response.json();
  if (rasterRevision !== null && rasterRevision !== revision) {
    clearRasterTileCache();
    requestVisibleTiles();
  }
  rasterRevision = revision;
}

async function refreshCopernicusRevision() {
  if (!mapServices.has("copernicus") || !copernicusLayer?.checked) {
    return;
  }
  const response = await fetch(`${copernicusServiceUrl}/api/raster/revision`);
  if (!response.ok) {
    throw new Error(`Copernicus service returned ${response.status} for raster revision`);
  }
  const { revision } = await response.json();
  if (copernicusRevision !== null && copernicusRevision !== revision) {
    clearCopernicusTileCache();
    requestVisibleTiles();
  }
  copernicusRevision = revision;
}

async function refreshVectorTileRevision() {
  if (!mapServices.has("vecmap") || !vectorLayer || !vectorLayer.checked) {
    return;
  }

  const response = await fetch(`${vectorMapServiceUrl}/api/tiles/revision`);
  if (!response.ok) {
    throw new Error(`vector map service returned ${response.status} for tile revision`);
  }
  const { revision, tileZooms } = await response.json();
  if (!Array.isArray(tileZooms) || tileZooms.length === 0
    || tileZooms.some((zoom) => !Number.isInteger(zoom) || zoom < 0 || zoom > 12)) {
    throw new Error("vector map service returned invalid tile zoom levels");
  }
  if (vectorTileRevision !== null && vectorTileRevision !== revision) {
    clearVectorTileCache();
    requestVisibleTiles();
  }
  vectorTileRevision = revision;
  vectorTileZooms = [...new Set(tileZooms)].sort((left, right) => left - right);
  requestVisibleTiles();
}

async function refreshTerrainRevision() {
  if (!mapServices.has("terrain") || !terrainLayer.checked) {
    return;
  }
  const response = await fetch(`${terrainServiceUrl}/api/raster/revision`);
  if (!response.ok) {
    throw new Error(`terrain service returned ${response.status} for raster revision`);
  }
  const { revision } = await response.json();
  if (terrainRevision !== null && terrainRevision !== revision) {
    clearTerrainTileCache();
    requestVisibleTiles();
  }
  terrainRevision = revision;
}

async function refreshShapes() {
  const services = enabledOverlayServices();
  if (services.length === 0) {
    return;
  }
  const results = await Promise.all(services.map(async (service) => {
    try {
      const response = await fetch(`${service.url}${service.endpoint}`);
      if (!response.ok) {
        throw new Error(`returned ${response.status}`);
      }
      return { service, collection: await response.json() };
    } catch (error) {
      return { service, error };
    }
  }));
  const unavailableServices = [];
  const unavailableLiveData = [];
  for (const result of results) {
    const key = overlayKey(result.service);
    if (result.error) {
      unavailableServices.push(result.service.url);
    } else {
      overlayCollections.set(key, result.collection);
      if (typeof result.collection.source?.lastError === "string") {
        unavailableLiveData.push(`${overlayDisplayName(result.service)}: ${result.collection.source.lastError}`);
      }
    }
  }
  if (unavailableLiveData.length > 0) {
    status.textContent = `Live data unavailable: ${unavailableLiveData.join("; ")}`;
    status.dataset.liveDataError = "true";
  } else if (status.dataset.liveDataError === "true") {
    status.textContent = "";
    delete status.dataset.liveDataError;
  } else if (unavailableServices.length > 0) {
    status.textContent = `Overlay unavailable: ${unavailableServices.join(", ")}`;
  }
  rebuildOverlayFeatures();
}

async function loadContextMenuActions() {
  const services = [...mapServices.values(), ...overlayServices, ...symbolServices];
  const manifests = await Promise.all(services.map(async (service) => {
    try {
      const response = await fetch(`${service.url}/api/kody/manifest`);
      if (!response.ok) {
        throw new Error(`returned ${response.status}`);
      }
      const manifest = await response.json();
      const actions = (manifest.contextMenuActions || [])
        .filter((action) => (
          typeof action.id === "string"
          && typeof action.label === "string"
          && typeof action.path === "string"
        ))
        .map((action) => ({ label: action.label, run: (point) => {
          runContextAction(service, action, point).catch((error) => {
            status.textContent = `Unable to run ${action.label}: ${error.message}`;
          });
        }}));
      return actions.length === 0
        ? []
        : [{ label: manifest.displayName || service.id, actions }];
    } catch {
      return [];
    }
  }));
  contextMenuPlugins = manifests.flat();
}

function closeContextMenu() {
  mapContextMenu.hidden = true;
  mapContextMenu.replaceChildren();
}

function showContextSubmenu(plugin, point) {
  mapContextMenu.querySelector(".context-menu-submenu")?.remove();
  const submenu = document.createElement("div");
  submenu.className = "context-menu-submenu";
  submenu.role = "menu";
  for (const action of plugin.actions) {
    const button = document.createElement("button");
    button.type = "button";
    button.role = "menuitem";
    button.textContent = action.label;
    button.addEventListener("click", () => action.run(point));
    submenu.append(button);
  }
  mapContextMenu.append(submenu);
}

function addContextMenuPlugin(container, plugin, point) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "context-menu-plugin";
  button.role = "menuitem";
  button.setAttribute("aria-haspopup", "menu");
  button.textContent = plugin.label;
  button.addEventListener("pointerenter", () => showContextSubmenu(plugin, point));
  button.addEventListener("focus", () => showContextSubmenu(plugin, point));
  button.addEventListener("click", () => showContextSubmenu(plugin, point));
  container.append(button);
}

function runMeasureFromHere(point) {
  measureStart = point;
  measureEnd = null;
  closeContextMenu();
  status.textContent = "Measurement start set. Right-click a destination and select Measure: To Here.";
  refreshMap();
}

function runMeasureToHere(point) {
  if (!measureStart) {
    closeContextMenu();
    status.textContent = "Select Measure: From Here before choosing a destination.";
    return;
  }
  measureEnd = point;
  closeContextMenu();
  status.textContent = `Measured ${formatMeasureDistance(
    measureDistanceKilometres(measureStart, measureEnd),
    (measureStart.latitude + measureEnd.latitude) / 2,
  )}.`;
  refreshMap();
}

function clearMeasurement() {
  measureStart = null;
  measureEnd = null;
  closeContextMenu();
  status.textContent = "Measurement cleared.";
  refreshMap();
}

function mapPointFromEvent(event) {
  const rectangle = canvas.getBoundingClientRect();
  const worldX = camera.x + (event.clientX - rectangle.left - canvas.clientWidth / 2) / scale();
  const worldY = camera.y + (event.clientY - rectangle.top - canvas.clientHeight / 2) / scale();
  return MapMath.worldToLonLat(worldX, worldY);
}

async function waitForContextAction(service, action) {
  if (!action.statusPath) {
    return;
  }
  for (let attempt = 0; attempt < 360; attempt += 1) {
    await new Promise((resolve) => window.setTimeout(resolve, 1000));
    const response = await fetch(`${service.url}${action.statusPath}`);
    if (!response.ok) {
      throw new Error(`context action status returned ${response.status}`);
    }
    const result = await response.json();
    status.textContent = result.message || "Context action status updated.";
    if (!["running", "processing"].includes(result.status)) {
      if (result.status === "failed") {
        throw new Error(result.message || "context action failed");
      }
      return;
    }
  }
  throw new Error("context action status timed out");
}

async function runContextAction(service, action, point) {
  closeContextMenu();
  status.textContent = `${action.label} requested.`;
  const response = await fetch(`${service.url}${action.path}`, {
    method: action.method || "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(point),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `${action.label} returned ${response.status}`);
  }
  await waitForContextAction(service, action);
  if (action.resultPath) {
    const resultResponse = await fetch(`${service.url}${action.resultPath}`);
    if (!resultResponse.ok) {
      throw new Error(`context action result returned ${resultResponse.status}`);
    }
    const collection = await resultResponse.json();
    if (!Array.isArray(collection.features)) {
      throw new Error("context action result is not GeoJSON");
    }
    contextActionFeatures = collection.features;
  }
  refreshMap();
}

function showContextMenu(event) {
  event.preventDefault();
  const point = mapPointFromEvent(event);
  const containerBounds = container.getBoundingClientRect();
  mapContextMenu.replaceChildren();
  const primaryMenu = document.createElement("div");
  primaryMenu.className = "context-menu-primary";
  const plugins = [
    {
      label: "Map",
      actions: [
        { label: "Measure: From Here", run: runMeasureFromHere },
        { label: "Measure: To Here", run: runMeasureToHere },
        { label: "Measure: Clear", run: clearMeasurement },
      ],
    },
    ...contextMenuPlugins,
  ];
  for (const plugin of plugins) {
    addContextMenuPlugin(primaryMenu, plugin, point);
  }
  mapContextMenu.append(primaryMenu);
  mapContextMenu.style.left = `${event.clientX - containerBounds.left}px`;
  mapContextMenu.style.top = `${event.clientY - containerBounds.top}px`;
  mapContextMenu.hidden = false;
}

function loadSymbolImage(svg, color) {
  const image = new Image();
  const colouredSvg = color
    ? svg.replace("<svg ", `<svg color="${color}" `)
    : svg;
  image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(colouredSvg)}`;
  return image;
}

function symbolImage(definition, featureColor) {
  const color = definition.colorMode === "colorable" && /^#[0-9a-fA-F]{6}$/.test(featureColor)
    ? featureColor
    : null;
  const key = color || "fixed";
  let image = definition.images.get(key);
  if (!image) {
    image = loadSymbolImage(definition.svg, color);
    image.addEventListener("load", refreshMap, { once: true });
    definition.images.set(key, image);
  }
  return image;
}

function validateSymbolDefinition(definition) {
  return ["orientable", "alwaysup"].includes(definition.orientation)
    && ["colorable", "fixed"].includes(definition.colorMode)
    && typeof definition.scale === "number"
    && Number.isFinite(definition.scale)
    && definition.scale > 0
    && typeof definition.name === "string"
    && typeof definition.svg === "string";
}

async function refreshSymbols() {
  const results = await Promise.all(symbolServices.filter((service) => service.enabled).map(async (service) => {
    const response = await fetch(`${service.url}/api/symbols`);
    if (!response.ok) {
      throw new Error(`${service.url} returned ${response.status}`);
    }
    return response.json();
  }));
  symbolDefinitions.clear();
  for (const collection of results) {
    for (const definition of collection.symbols || []) {
      if (!validateSymbolDefinition(definition)) {
        throw new Error("symbol service returned an invalid symbol definition");
      }
      symbolDefinitions.set(definition.name, {
        ...definition,
        images: new Map(),
      });
    }
  }
}

function missingTiles(zoom) {
  return visibleTiles(zoom).filter((tile) => {
    const key = tileKey(tile);
    if (requestedTileKeys.has(key)) {
      return false;
    }
    requestedTileKeys.add(key);
    return true;
  });
}

function prioritizeTiles(tiles) {
  return [...tiles].sort((left, right) => {
    const leftDistance = (left.x + 0.5 - camera.x * 2 ** left.zoom) ** 2
      + (left.y + 0.5 - camera.y * 2 ** left.zoom) ** 2;
    const rightDistance = (right.x + 0.5 - camera.x * 2 ** right.zoom) ** 2
      + (right.y + 0.5 - camera.y * 2 ** right.zoom) ** 2;
    return leftDistance - rightDistance;
  });
}

function missingRasterTiles(zoom) {
  return visibleTiles(zoom).filter((tile) => {
    const key = rasterTileKey(tile);
    if (requestedRasterTileKeys.has(key)) {
      return false;
    }
    requestedRasterTileKeys.add(key);
    return true;
  });
}

function missingCopernicusTiles(zoom) {
  return visibleTiles(zoom).filter((tile) => {
    const key = copernicusTileKey(tile);
    if (requestedCopernicusTileKeys.has(key)) {
      return false;
    }
    requestedCopernicusTileKeys.add(key);
    return true;
  });
}

function missingTerrainTiles(zoom) {
  return visibleTiles(zoom).filter((tile) => {
    const key = terrainTileKey(tile);
    if (requestedTerrainTileKeys.has(key)) {
      return false;
    }
    requestedTerrainTileKeys.add(key);
    return true;
  });
}

function requestVisibleTiles() {
  if (tileRequestFrame !== null) {
    return;
  }
  tileRequestFrame = requestAnimationFrame(() => {
    tileRequestFrame = null;
    const baseZoom = vectorTileZoom();
    const baseTiles = visibleTiles(baseZoom);
    const tiles = missingTiles(baseZoom);
    const detailZoom = Math.min(18, Math.ceil(camera.zoom));
    if (tiles.length > 0) {
      if (status.dataset.liveDataError !== "true") {
        status.textContent = "";
      }
      tileWorker.postMessage({
        type: "request-tiles",
        serviceUrl: vectorMapServiceUrl,
        revision: vectorTileRevision,
        tiles: prioritizeTiles(tiles),
        transparentBase: false,
        generation: vectorTileGeneration,
      });
    }
    if (mapServices.has("satmap") && imageryLayer.checked) {
      let rasterTiles = missingRasterTiles(baseZoom);
      const rasterBaseTilesReady = baseTiles.every((tile) => rasterTileCache.has(rasterTileKey(tile)));
      if (rasterBaseTilesReady && updatesPerSecond >= 50 && detailZoom > baseZoom) {
        rasterTiles = rasterTiles.concat(missingRasterTiles(detailZoom));
      }
      if (rasterTiles.length > 0) {
        tileWorker.postMessage({ type: "request-raster-tiles", serviceUrl: satelliteMapServiceUrl, tiles: rasterTiles });
      }
    }
    if (mapServices.has("copernicus") && copernicusLayer.checked) {
      let copernicusTiles = missingCopernicusTiles(baseZoom);
      const copernicusBaseTilesReady = baseTiles.every(
        (tile) => copernicusTileCache.has(copernicusTileKey(tile)),
      );
      if (copernicusBaseTilesReady && updatesPerSecond >= 50 && detailZoom > baseZoom) {
        copernicusTiles = copernicusTiles.concat(missingCopernicusTiles(detailZoom));
      }
      if (copernicusTiles.length > 0) {
        tileWorker.postMessage({
          type: "request-raster-tiles",
          serviceUrl: copernicusServiceUrl,
          tiles: copernicusTiles,
        });
      }
    }
    if (mapServices.has("terrain") && terrainLayer.checked) {
      let terrainTiles = missingTerrainTiles(baseZoom);
      const terrainBaseTilesReady = baseTiles.every((tile) => terrainTileCache.has(terrainTileKey(tile)));
      if (terrainBaseTilesReady && updatesPerSecond >= 50 && detailZoom > baseZoom) {
        terrainTiles = terrainTiles.concat(missingTerrainTiles(detailZoom));
      }
      if (terrainTiles.length > 0) {
        tileWorker.postMessage({ type: "request-raster-tiles", serviceUrl: terrainServiceUrl, tiles: terrainTiles });
      }
    }
  });
}

function refreshMap() {
  requestVisibleTiles();
}

function zoomAt(pointerX, pointerY, amount) {
  const previousScale = scale();
  const worldX = camera.x + (pointerX - canvas.clientWidth / 2) / previousScale;
  const worldY = camera.y + (pointerY - canvas.clientHeight / 2) / previousScale;
  camera.zoom = Math.max(0, Math.min(18, camera.zoom + amount));
  const nextScale = scale();
  camera.x = Math.max(0, Math.min(1, worldX - (pointerX - canvas.clientWidth / 2) / nextScale));
  camera.y = Math.max(0, Math.min(1, worldY - (pointerY - canvas.clientHeight / 2) / nextScale));
  mapStateVersion += 1;
  refreshMap();
  invalidateAirLanesCache();
  scheduleViewportPublish();
}

async function loadConfiguration(restoreMapView = true) {
  const [servicesResponse, mapViewResponse] = await Promise.all([
    fetch(`${configurationServiceUrl}/api/config/services`),
    fetch(`${configurationServiceUrl}/api/config/map-view`),
  ]);
  if (!servicesResponse.ok || !mapViewResponse.ok) {
    throw new Error("configuration service did not return the map settings");
  }
  const configuration = await servicesResponse.json();
  configuredServices = configuration.services;
  configuredMapServices = configuration.services.filter((service) => ["vecmap", "satmap", "copernicus", "terrain"].includes(service.type));
  const runningServices = await registeredRunningServices(configuration.services);
  const vectorService = runningServices.find((item) => item.type === "vecmap");
  const satelliteService = runningServices.find((item) => item.type === "satmap");
  const copernicusService = runningServices.find((item) => item.type === "copernicus");
  const terrainService = runningServices.find((item) => item.type === "terrain");
  mapServices.clear();
  [vectorService, satelliteService, copernicusService, terrainService].filter(Boolean).forEach((service) => {
    mapServices.set(service.type, service);
  });
  symbolServices = runningServices
    .filter((item) => item.type === "symbols")
    .map((item) => ({ ...item, enabled: true }));
  overlayServices = runningServices
    .filter((item) => ["shapes", "geojson", "adsb", "ais", "airlanes"].includes(item.type))
    .map((item) => ({
      id: item.id,
      type: item.type,
      url: item.url,
      endpoint: item.type === "shapes" ? "/api/shapes" : "/api/geojson",
      enabled: true,
    }));
  const activeOverlayKeys = new Set(
    overlayServices.map(overlayKey),
  );
  for (const key of overlayCollections.keys()) {
    if (!activeOverlayKeys.has(key)) {
      overlayCollections.delete(key);
    }
  }
  if (vectorService) {
    const nextVectorMapServiceUrl = vectorService.url.replace(/\/$/, "");
    if (vectorMapServiceUrl !== nextVectorMapServiceUrl) {
      clearVectorTileCache();
      vectorTileRevision = null;
    }
    vectorMapServiceUrl = nextVectorMapServiceUrl;
  }
  if (satelliteService) {
    const nextSatelliteMapServiceUrl = satelliteService.url.replace(/\/$/, "");
    if (satelliteMapServiceUrl !== nextSatelliteMapServiceUrl) {
      clearRasterTileCache();
      rasterRevision = null;
    }
    satelliteMapServiceUrl = nextSatelliteMapServiceUrl;
  }
  if (copernicusService) {
    const nextCopernicusServiceUrl = copernicusService.url.replace(/\/$/, "");
    if (copernicusServiceUrl !== nextCopernicusServiceUrl) {
      clearCopernicusTileCache();
      copernicusRevision = null;
    }
    copernicusServiceUrl = nextCopernicusServiceUrl;
  }
  if (terrainService) {
    const nextTerrainServiceUrl = terrainService.url.replace(/\/$/, "");
    if (terrainServiceUrl !== nextTerrainServiceUrl) {
      clearTerrainTileCache();
      terrainRevision = null;
      terrainPalette = null;
    }
    terrainServiceUrl = nextTerrainServiceUrl;
  }
  if (terrainService) {
    await refreshTerrainPalette();
  }
  overlayRevision = null;
  shapes = [];
  airLanesShapeGroups = [];
  liveShapeFeatureGroups = [];
  airLanesCacheRevision = null;
  airLanesCache = null;
  const mapView = await mapViewResponse.json();
  if (mapView && restoreMapView) {
    const world = MapMath.lonLatToWorld(mapView.longitude, mapView.latitude);
    camera.x = world.x;
    camera.y = world.y;
    camera.zoom = mapView.zoom;
    gridLayer.checked = mapView.layers?.grid ?? true;
    gridCrosshair = mapView.layers?.gridCrosshair ?? false;
    layerOpacity.satellite = mapView.layers?.satelliteOpacity ?? 10;
    layerOpacity.copernicus = mapView.layers?.copernicusOpacity ?? 10;
    layerOpacity.terrain = mapView.layers?.terrainOpacity ?? 8;
    layerOpacity.vector = mapView.layers?.vectorOpacity ?? 10;
    layerOpacity.grid = mapView.layers?.gridOpacity ?? 10;
  }
  configurationLoaded = true;
  renderMapServiceControls(mapView?.layers);
  if (vectorService) {
    await refreshVectorTileRevision();
  }
  updateOpacityControls();
  scheduleViewportPublish();
  renderOverlaySourceControls(mapView?.layers?.dataSources, mapView?.layers?.dataSourceOpacity);
  await synchronizeLiveOverlayActivity();
  renderSymbolSourceControls(mapView?.layers?.dataSources);
  if (imageryLayer?.checked) {
    await refreshRasterRevision();
    refreshMap();
  }
  if (copernicusLayer?.checked) {
    await refreshCopernicusRevision();
    refreshMap();
  }
  try {
    await refreshSymbols();
  } catch (error) {
    status.textContent = `Symbols unavailable: ${error.message}`;
  }
  await refreshShapes();
  await loadContextMenuActions();
}

async function saveMapView() {
  if (!configurationLoaded || mapViewSaveInProgress || savedMapStateVersion === mapStateVersion) {
    return;
  }
  mapViewSaveInProgress = true;
  const version = mapStateVersion;
  const center = MapMath.worldToLonLat(camera.x, camera.y);
  try {
    const response = await fetch(`${configurationServiceUrl}/api/config/map-view`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        longitude: center.longitude,
        latitude: center.latitude,
        zoom: camera.zoom,
        layers: {
          satelliteImagery: imageryLayer.checked,
          copernicus: copernicusLayer.checked,
          terrain: terrainLayer.checked,
          dataSources: overlayLayerSelections(),
          dataSourceOpacity: overlaySourceOpacities(),
          vectorMap: vectorLayer.checked,
          grid: gridLayer.checked,
          gridCrosshair,
          satelliteOpacity: layerOpacity.satellite,
          copernicusOpacity: layerOpacity.copernicus,
          terrainOpacity: layerOpacity.terrain,
          vectorOpacity: layerOpacity.vector,
          gridOpacity: layerOpacity.grid,
        },
      }),
    });
    if (!response.ok) {
      throw new Error(`configuration service returned ${response.status}`);
    }
    savedMapStateVersion = version;
  } catch (error) {
    status.textContent = `Unable to save map view: ${error.message}`;
  } finally {
    mapViewSaveInProgress = false;
  }
}

function saveMapViewOnExit() {
  if (!configurationLoaded || savedMapStateVersion === mapStateVersion) {
    return;
  }
  const center = MapMath.worldToLonLat(camera.x, camera.y);
  const payload = JSON.stringify({
    longitude: center.longitude,
    latitude: center.latitude,
    zoom: camera.zoom,
    layers: {
      satelliteImagery: imageryLayer.checked,
      copernicus: copernicusLayer.checked,
      terrain: terrainLayer.checked,
      dataSources: overlayLayerSelections(),
      dataSourceOpacity: overlaySourceOpacities(),
      vectorMap: vectorLayer.checked,
      grid: gridLayer.checked,
      gridCrosshair,
      satelliteOpacity: layerOpacity.satellite,
      copernicusOpacity: layerOpacity.copernicus,
      terrainOpacity: layerOpacity.terrain,
      vectorOpacity: layerOpacity.vector,
      gridOpacity: layerOpacity.grid,
    },
  });
  navigator.sendBeacon(
    `${configurationServiceUrl}/api/config/map-view`,
    new Blob([payload], { type: "application/json" }),
  );
}

tileWorker.addEventListener("message", (event) => {
  const { type, key, tile, bitmap, source, message, retryAfterMilliseconds } = event.data;
  if (type === "tile") {
    tileCache.set(key, { ...tile, bitmap, source });
    requestVisibleTiles();
  } else if (type === "tile-cancelled") {
    requestedTileKeys.delete(key);
    requestVisibleTiles();
  } else if (type === "tile-pending") {
    requestedTileKeys.delete(key);
    window.setTimeout(
      requestVisibleTiles,
      Math.max(100, Number(retryAfterMilliseconds) || 250),
    );
  } else if (type === "error") {
    requestedTileKeys.delete(key);
    status.textContent = `Unable to load map tile: ${message}`;
    window.setTimeout(requestVisibleTiles, 1000);
  } else if (type === "raster-tile") {
    if (key.startsWith(`${terrainServiceUrl}/`)) {
      terrainTileCache.set(key, bitmap);
    } else if (key.startsWith(`${copernicusServiceUrl}/`)) {
      copernicusTileCache.set(key, bitmap);
    } else {
      rasterTileCache.set(key, bitmap);
    }
    requestVisibleTiles();
  } else if (type === "raster-error") {
    if (key.startsWith(`${terrainServiceUrl}/`)) {
      requestedTerrainTileKeys.delete(key);
      status.textContent = `Unable to load terrain tile: ${message}`;
    } else if (key.startsWith(`${copernicusServiceUrl}/`)) {
      requestedCopernicusTileKeys.delete(key);
      status.textContent = `Unable to load Copernicus tile: ${message}`;
    } else {
      requestedRasterTileKeys.delete(key);
      status.textContent = `Unable to load satellite tile: ${message}`;
    }
  }
});
serviceConfigDialog.addEventListener("close", stopServiceActivityPolling);

canvas.addEventListener("pointerdown", (event) => {
  if (event.button !== 0) {
    return;
  }
  closeContextMenu();
  dragging = true;
  lastPointer = event;
  canvas.setPointerCapture(event.pointerId);
});

canvas.addEventListener("contextmenu", showContextMenu);
window.addEventListener("pointerdown", (event) => {
  if (!mapContextMenu.hidden && !mapContextMenu.contains(event.target)) {
    closeContextMenu();
  }
});

canvas.addEventListener("pointermove", (event) => {
  if (!dragging) {
    return;
  }
  camera.x = Math.max(0, Math.min(1, camera.x - (event.clientX - lastPointer.clientX) / scale()));
  camera.y = Math.max(0, Math.min(1, camera.y - (event.clientY - lastPointer.clientY) / scale()));
  lastPointer = event;
  mapStateVersion += 1;
  refreshMap();
  invalidateAirLanesCache();
  scheduleViewportPublish();
});

canvas.addEventListener("pointerup", () => {
  dragging = false;
  lastPointer = null;
  invalidateAirLanesCache();
});

panelResizeHandle.addEventListener("pointerdown", (event) => {
  resizingPanel = true;
  event.preventDefault();
});

window.addEventListener("pointermove", (event) => {
  if (!resizingPanel) {
    return;
  }
  sidePanel.style.width = `${Math.max(180, Math.min(480, event.clientX))}px`;
});

window.addEventListener("pointerup", () => {
  resizingPanel = false;
});

canvas.addEventListener("wheel", (event) => {
  event.preventDefault();
  const rectangle = canvas.getBoundingClientRect();
  const delta = event.deltaY * (event.deltaMode === WheelEvent.DOM_DELTA_LINE ? 16 : 1);
  zoomAt(event.clientX - rectangle.left, event.clientY - rectangle.top, -delta * 0.002);
}, { passive: false });

document.querySelector("#zoom-in").addEventListener("click", () => {
  const point = viewportCrosshairPoint();
  zoomAt(point.x, point.y, 0.5);
});
document.querySelector("#zoom-out").addEventListener("click", () => {
  const point = viewportCrosshairPoint();
  zoomAt(point.x, point.y, -0.5);
});
function saveLayerSelection() {
  mapStateVersion += 1;
  refreshMap();
}

gridLayer.addEventListener("change", saveLayerSelection);
document.querySelectorAll("[data-opacity-layer]").forEach((button) => {
  button.addEventListener("click", () => {
    changeLayerOpacity(button.dataset.opacityLayer, Number(button.dataset.opacityChange));
  });
});
document.querySelector("#configure-grid").addEventListener("click", configureGrid);
window.addEventListener("resize", () => {
  resizeCanvas();
  scheduleViewportPublish();
});
new ResizeObserver(() => {
  resizeCanvas();
  scheduleViewportPublish();
}).observe(container);
window.addEventListener("pagehide", saveMapViewOnExit);
window.setInterval(saveMapView, 5000);
window.setInterval(() => {
  refreshVectorTileRevision().catch((error) => {
    status.textContent = `Unable to refresh vector map tiles: ${error.message}`;
  });
}, 5000);
window.setInterval(() => {
  refreshRasterRevision().catch((error) => {
    status.textContent = `Unable to refresh satellite imagery: ${error.message}`;
  });
}, 5000);
window.setInterval(() => {
  refreshCopernicusRevision().catch((error) => {
    status.textContent = `Unable to refresh Copernicus imagery: ${error.message}`;
  });
}, 5000);
window.setInterval(() => {
  refreshTerrainRevision().catch((error) => {
    status.textContent = `Unable to refresh terrain: ${error.message}`;
  });
}, 5000);
window.setInterval(() => {
  reconcileRegisteredServices().catch((error) => {
    status.textContent = `Unable to reconcile registered services: ${error.message}`;
  });
}, 2000);
window.setInterval(() => {
  refreshShapes().catch((error) => {
    status.textContent = `Unable to refresh shapes: ${error.message}`;
  });
}, 1000);
requestAnimationFrame(renderMap);

loadConfiguration()
  .catch((error) => {
    status.textContent = `Unable to load configuration: ${error.message}`;
  })
  .finally(() => {
    resizeCanvas();
    refreshMap();
  });
