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
const miniTimeControl = document.querySelector("#mini-time-control");
const miniTimePlay = document.querySelector("#mini-time-play");
const miniTimePause = document.querySelector("#mini-time-pause");
const miniTimeReadout = document.querySelector("#mini-time-readout");
const miniTimeReset = document.querySelector("#mini-time-reset");
let vectorLayer;
let imageryLayer;
let copernicusLayer;
let terrainLayer;
let esaDemLayer;
const mapServiceList = document.querySelector("#map-service-list");
const dataSourceList = document.querySelector("#data-source-list");
const workspaceSourceList = document.querySelector("#workspace-source-list");
const symbolSourceList = document.querySelector("#symbol-source-list");
const layersTab = document.querySelector("#layers-tab");
const selectionTab = document.querySelector("#selection-tab");
const layersPanel = document.querySelector("#layers-panel");
const selectionPanel = document.querySelector("#selection-panel");
const selectionList = document.querySelector("#selection-list");
const selectionEmpty = document.querySelector("#selection-empty");
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
const trackEditorDialog = document.querySelector("#track-editor-dialog");
const trackEditorForm = document.querySelector("#track-editor-form");
const trackRouteSelect = document.querySelector("#track-route-select");
const trackRouteFields = document.querySelector("#track-route-fields");
const trackWaypointList = document.querySelector("#track-waypoint-list");
const trackAircraftList = document.querySelector("#track-aircraft-list");
const trackEditorStatus = document.querySelector("#track-editor-status");
const trackNewRoute = document.querySelector("#track-new-route");
const trackSimulationToggle = document.querySelector("#track-simulation-toggle");
const trackSimulationReset = document.querySelector("#track-simulation-reset");
const trackDeleteRoute = document.querySelector("#track-delete-route");
const trackCancel = document.querySelector("#track-cancel");
const workspaceDialog = document.querySelector("#workspace-dialog");
const workspaceEyebrow = document.querySelector("#workspace-eyebrow");
const workspaceTitle = document.querySelector("#workspace-title");
const workspaceClose = document.querySelector("#workspace-close");
const workspaceContent = document.querySelector("#workspace-content");
const workspaceNavigator = document.querySelector("#workspace-navigator");
const workspacePrimary = document.querySelector("#workspace-primary");
const workspaceInspector = document.querySelector("#workspace-inspector");
const workspaceTimeline = document.querySelector("#workspace-timeline");
const workspaceNavigatorResize = document.querySelector("#workspace-navigator-resize");
const workspaceInspectorResize = document.querySelector("#workspace-inspector-resize");
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
const esaDemTileCache = new Map();
const requestedTileKeys = new Set();
const requestedRasterTileKeys = new Set();
const requestedCopernicusTileKeys = new Set();
const requestedTerrainTileKeys = new Set();
const requestedEsaDemTileKeys = new Set();
const tileWorker = new Worker("/tile-worker.js?v=20261022a");
let vectorMapServiceUrl = window.location.origin;
let satelliteMapServiceUrl = window.location.origin;
let copernicusServiceUrl = window.location.origin;
let terrainServiceUrl = window.location.origin;
let esaDemServiceUrl = window.location.origin;
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
let esaDemRevision = null;
let terrainPalette = null;
let viewportElevation = null;
let overlayServices = [];
let workspaceServices = [];
let overlayRevision = null;
const overlayCollections = new Map();
let symbolServices = [];
const symbolDefinitions = new Map();
let shapes = [];
let airLanesShapeGroups = [];
let liveShapeFeatureGroups = [];
let contextActionFeatures = [];
let contextMenuPlugins = [];
const selectedFeatures = new Map();
let selectionPointer = null;
let selectionActionsKey = null;
let centeredSelectionKey = null;
let shapeDrawing = null;
let shapeMapEditing = null;
let shapeMapMove = null;
let trackEditorSession = null;
let trackRouteDrawing = null;
let trackWaypointMove = null;
let trackRouteEditing = null;
let terrainProfileDrawing = null;
let workspaceSession = null;
let workspaceResize = null;
let workspaceSimulationTimer = null;
let workspaceSimulationEvents = null;
let simTimeControlService = null;
let simTimeControlState = null;
let simTimeControlEvents = null;
let displayMiniTimeControl = false;
let measureStart = null;
let measureEnd = null;
let configurationSession = null;
let serviceActivityTimer = null;
let viewportPublishTimer = null;
let viewportElevationTimer = null;
let viewportElevationRequest = 0;
const mapServices = new Map();
const layerOpacity = { satellite: 10, copernicus: 10, terrain: 8, esaDem: 8, vector: 10, grid: 10 };
let gridCrosshair = false;
const airLanesCacheCanvas = document.createElement("canvas");
const airLanesCacheContext = airLanesCacheCanvas.getContext("2d");
const tileLayerCanvas = document.createElement("canvas");
const tileLayerContext = tileLayerCanvas.getContext("2d");
const EARTH_RADIUS_KILOMETRES = 6_371.0088;
const AIRLANES_CACHE_PADDING = 96;
const AIRLANES_CACHE_SETTLE_DELAY = 180;
const SELECTION_CAPTURE_RADIUS_PIXELS = 12;
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
      return {
        ...service,
        url: service.url.replace(/\/$/, ""),
        displayName: manifest.displayName || service.id,
        manifest,
      };
    } catch {
      return null;
    } finally {
      window.clearTimeout(timeout);
    }
  }));
  return results.filter(Boolean);
}

function activeServiceIds() {
  return new Set([...mapServices.values(), ...overlayServices, ...symbolServices, ...workspaceServices].map((service) => service.id));
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
    "esa-dem": { control: "esaDemLayer", visible: layers?.esaDem ?? false, opacity: "esaDem" },
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
      } else if (service.type === "esa-dem") {
        refreshEsaDemRevision().catch((error) => { status.textContent = `Unable to refresh ESA Download Terrain: ${error.message}`; });
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
    configure.dataset.symbol = "gear";
    configure.dataset.symbolFallback = "⚙";
    configure.textContent = "⚙";
    configure.title = `Configure ${service.displayName}`;
    renderButtonSymbol(configure);
    configure.addEventListener("click", () => {
      configureService(service).catch((error) => {
        status.textContent = `Unable to configure ${service.displayName}: ${error.message}`;
      });
    });
    row.append(label, opacity, configure);
    mapServiceList.append(row);
    if (details.control === "vectorLayer") vectorLayer = checkbox;
    if (details.control === "imageryLayer") imageryLayer = checkbox;
    if (details.control === "copernicusLayer") copernicusLayer = checkbox;
    if (details.control === "terrainLayer") terrainLayer = checkbox;
    if (details.control === "esaDemLayer") esaDemLayer = checkbox;
  }
}

function renderWorkspaceLaunchers() {
  workspaceSourceList.replaceChildren();
  const services = [...mapServices.values(), ...overlayServices, ...workspaceServices]
    .filter((service) => service.manifest?.capabilities?.workspace);
  for (const service of services) {
    const workspace = service.manifest.capabilities.workspace;
    const launcher = document.createElement("button");
    launcher.type = "button";
    launcher.className = "workspace-launcher";
    launcher.dataset.symbol = "rocket";
    launcher.dataset.symbolFallback = "🚀";
    launcher.textContent = "🚀";
    launcher.title = `Open ${workspace.label}`;
    launcher.setAttribute("aria-label", launcher.title);
    const label = document.createElement("span");
    label.dataset.symbolLabel = "true";
    label.textContent = workspace.label;
    launcher.append(label);
    renderButtonSymbol(launcher);
    launcher.addEventListener("click", () => {
      configureWorkspace(service).catch((error) => {
        status.textContent = `Unable to open ${workspace.label}: ${error.message}`;
      });
    });
    workspaceSourceList.append(launcher);
  }
}

const layers = {
  vector: { isVisible: () => vectorLayer?.checked, draw: () => drawTileLayerWithOpacity(layerOpacity.vector, drawCountryLayer) },
  imagery: { isVisible: () => imageryLayer?.checked, draw: () => drawTileLayerWithOpacity(layerOpacity.satellite, drawImageryLayer) },
  copernicus: { isVisible: () => copernicusLayer?.checked, draw: () => drawTileLayerWithOpacity(layerOpacity.copernicus, drawCopernicusLayer) },
  terrain: { isVisible: () => terrainLayer?.checked, draw: () => drawTileLayerWithOpacity(layerOpacity.terrain, drawTerrainLayer) },
  esaDem: { isVisible: () => esaDemLayer?.checked, draw: () => drawTileLayerWithOpacity(layerOpacity.esaDem, drawEsaDemLayer) },
  shapes: { isVisible: () => shapes.length > 0 || contextActionFeatures.length > 0 || terrainProfileDrawing !== null || shapeMapEditing !== null, draw: drawShapesLayer },
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
  let tiles = nearestCachedTiles(baseZoom, rasterTileCache, rasterTileKey);
  if (detailZoom > baseZoom) {
    const visibleDetailTiles = visibleTiles(detailZoom);
    const detailTiles = exactCachedTiles(detailZoom, rasterTileCache, rasterTileKey);
    if (detailTiles.length === visibleDetailTiles.length) {
      tiles = detailTiles;
    }
  }
  for (const tile of tiles) drawTile(target, tile);
}

function drawCopernicusLayer(target) {
  const baseZoom = Math.max(0, Math.min(18, Math.floor(camera.zoom)));
  const detailZoom = Math.min(18, Math.ceil(camera.zoom));
  let tiles = nearestCachedTiles(baseZoom, copernicusTileCache, copernicusTileKey);
  if (detailZoom > baseZoom) {
    const visibleDetailTiles = visibleTiles(detailZoom);
    const detailTiles = exactCachedTiles(detailZoom, copernicusTileCache, copernicusTileKey);
    if (detailTiles.length === visibleDetailTiles.length) {
      tiles = detailTiles;
    }
  }
  for (const tile of tiles) drawTile(target, tile);
}

function drawTerrainLayer(target) {
  const baseZoom = Math.max(0, Math.min(18, Math.floor(camera.zoom)));
  const detailZoom = Math.min(18, Math.ceil(camera.zoom));
  let tiles = nearestCachedTiles(baseZoom, terrainTileCache, terrainTileKey);
  if (detailZoom > baseZoom) {
    const visibleDetailTiles = visibleTiles(detailZoom);
    const detailTiles = exactCachedTiles(detailZoom, terrainTileCache, terrainTileKey);
    if (detailTiles.length === visibleDetailTiles.length) {
      tiles = detailTiles;
    }
  }
  for (const tile of tiles) drawTerrainTile(target, tile);
}

function drawEsaDemLayer(target) {
  const baseZoom = Math.max(0, Math.min(18, Math.floor(camera.zoom)));
  const detailZoom = Math.min(18, Math.ceil(camera.zoom));
  let tiles = nearestCachedTiles(baseZoom, esaDemTileCache, esaDemTileKey);
  if (detailZoom > baseZoom) {
    const visibleDetailTiles = visibleTiles(detailZoom);
    const detailTiles = exactCachedTiles(detailZoom, esaDemTileCache, esaDemTileKey);
    if (detailTiles.length === visibleDetailTiles.length) {
      tiles = detailTiles;
    }
  }
  for (const tile of tiles) drawTerrainTile(target, tile);
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
  const movingRouteId = trackWaypointMove?.routeId;
  const groups = movingRouteId
    ? liveShapeFeatureGroups.map(({ features, opacity }) => ({
      opacity,
      features: features.filter((feature) => (
        feature.properties?.routeId !== movingRouteId
        && feature.id !== `route:${movingRouteId}`
      )),
    }))
    : liveShapeFeatureGroups;
  drawFeatureGroups(groups, []);
  drawShapeFeatures(contextActionFeatures, []);
  drawTrackEditorPreview();
  drawTerrainProfilePreview();
  drawShapeMapEditorPreview();
  drawSelectedFeatureMarkers();
}

function drawTrackEditorPreview() {
  let points = trackRouteDrawing
    ? [...trackRouteDrawing.points, ...(trackRouteDrawing.preview ? [trackRouteDrawing.preview] : [])]
    : [];
  if (trackWaypointMove) {
    const route = trackEditorSession?.scenario.routes.find((item) => item.id === trackWaypointMove.routeId);
    if (route) {
      points = route.legs.map((leg) => (
        leg.id === trackWaypointMove.legId ? trackWaypointMove.preview : leg.waypoint
      ));
      if (route.completionPolicy === "loop" && points.length > 1) {
        points = [...points, points[0]];
      }
    }
    if (trackRouteEditing && !trackWaypointMove) {
      const route = trackEditorSession?.scenario.routes.find((item) => item.id === trackRouteEditing.routeId);
      if (route) {
        points = route.legs.map((leg) => leg.waypoint);
        if (route.completionPolicy === "loop" && points.length > 1) {
          points = [...points, points[0]];
        }
      }
    }
  }
  if (points.length === 0) return;
  context.save();
  context.strokeStyle = "#8df2ff";
  context.fillStyle = "#8df2ff";
  context.lineWidth = 2;
  context.setLineDash([5, 4]);
  context.beginPath();
  points.forEach((coordinate, index) => {
    const point = screenPoint(...coordinate);
    if (index === 0) context.moveTo(point.x, point.y);
    else context.lineTo(point.x, point.y);
  });
  context.stroke();
  context.setLineDash([]);
  const markerPoints = trackWaypointMove && points.length > 1
    ? points.slice(0, -1)
    : points;
  for (const coordinate of markerPoints) {
    const point = screenPoint(...coordinate);
    context.beginPath();
    context.arc(point.x, point.y, trackRouteEditing ? 6 : 4, 0, 2 * Math.PI);
    context.fill();
  }
  context.restore();
}

function drawShapeMapEditorPreview() {
  const draft = shapeMapEditing?.draft;
  if (!draft) return;
  const geometry = shapeDraftGeometry(draft);
  if (!geometry) return;
  context.save();
  context.strokeStyle = "#8df2ff";
  context.fillStyle = "rgb(141 242 255 / 16%)";
  context.lineWidth = 2;
  context.setLineDash([6, 4]);
  context.beginPath();
  appendShapeGeometry(geometry);
  context.fill("evenodd");
  context.stroke();
  context.setLineDash([]);
  for (const control of shapeMapControlPoints(draft)) {
    const point = screenPoint(...control.coordinate);
    context.beginPath();
    context.arc(point.x, point.y, control.id === shapeMapMove?.controlId ? 8 : 6, 0, 2 * Math.PI);
    context.fillStyle = "#8df2ff";
    context.fill();
    context.lineWidth = 2;
    context.strokeStyle = "#063849";
    context.stroke();
  }
  context.restore();
}

function drawTerrainProfilePreview() {
  if (!terrainProfileDrawing?.start) return;
  const points = [terrainProfileDrawing.start];
  if (terrainProfileDrawing.preview) points.push(terrainProfileDrawing.preview);
  context.save();
  context.strokeStyle = "#ffbf69";
  context.fillStyle = "#ffbf69";
  context.lineWidth = 2;
  context.setLineDash([6, 4]);
  context.beginPath();
  points.forEach((coordinate, index) => {
    const point = screenPoint(...coordinate);
    if (index === 0) context.moveTo(point.x, point.y);
    else context.lineTo(point.x, point.y);
  });
  context.stroke();
  context.setLineDash([]);
  for (const coordinate of points) {
    const point = screenPoint(...coordinate);
    context.beginPath();
    context.arc(point.x, point.y, 5, 0, 2 * Math.PI);
    context.fill();
  }
  context.restore();
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

function supportsLiveOverlayActivity(service) {
  return service.type === "adsb" || service.type === "ais";
}

async function setLiveOverlayActivity(service) {
  if (!supportsLiveOverlayActivity(service)) {
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
    overlayServices.filter(supportsLiveOverlayActivity).map(setLiveOverlayActivity),
  );
  const failures = results.filter((result) => result.status === "rejected");
  if (failures.length > 0) {
    throw new Error(failures.map((failure) => failure.reason.message).join("; "));
  }
}

function selectionKey(service, feature) {
  return `${service.id}:${feature.id}`;
}

function isSelectableFeature(feature) {
  return typeof feature?.id === "string"
    && feature.properties?.kody?.selectable === true;
}

function selectableDataFeatures() {
  return enabledOverlayServices()
    .flatMap((service) => {
      const features = overlayCollections.get(overlayKey(service))?.features;
      if (!Array.isArray(features)) {
        return [];
      }
      return features
        .filter(isSelectableFeature)
        .map((feature) => ({ service, feature }));
    });
}

function screenCoordinates(coordinates) {
  return coordinates.map(([longitude, latitude]) => screenPoint(longitude, latitude));
}

function pointNearSegment(point, start, end, radius = SELECTION_CAPTURE_RADIUS_PIXELS) {
  const deltaX = end.x - start.x;
  const deltaY = end.y - start.y;
  const lengthSquared = deltaX ** 2 + deltaY ** 2;
  const ratio = lengthSquared === 0 ? 0 : Math.max(0, Math.min(1, (
    (point.x - start.x) * deltaX + (point.y - start.y) * deltaY
  ) / lengthSquared));
  return Math.hypot(point.x - (start.x + ratio * deltaX), point.y - (start.y + ratio * deltaY)) <= radius;
}

function pointInRing(point, ring) {
  let inside = false;
  for (let index = 0, previous = ring.length - 1; index < ring.length; previous = index++) {
    const currentPoint = ring[index];
    const previousPoint = ring[previous];
    if (pointNearSegment(point, previousPoint, currentPoint, 0.001)) return true;
    if ((currentPoint.y > point.y) !== (previousPoint.y > point.y)
      && point.x < (previousPoint.x - currentPoint.x) * (point.y - currentPoint.y)
        / (previousPoint.y - currentPoint.y) + currentPoint.x) {
      inside = !inside;
    }
  }
  return inside;
}

function featureContainsScreenPoint(feature, point) {
  if (feature.geometry?.type === "Point") {
    const featurePoint = screenPoint(...feature.geometry.coordinates);
    return Math.hypot(featurePoint.x - point.x, featurePoint.y - point.y) <= SELECTION_CAPTURE_RADIUS_PIXELS;
  }
  const rings = feature.geometry?.type === "Polygon" ? feature.geometry.coordinates
    : feature.geometry?.type === "MultiPolygon" ? feature.geometry.coordinates.flat()
      : [];
  return rings.some((ring) => {
    const points = screenCoordinates(ring);
    return pointInRing(point, points)
      || points.some((current, index) => pointNearSegment(point, current, points[(index + 1) % points.length]));
  });
}

function segmentsIntersect(firstStart, firstEnd, secondStart, secondEnd) {
  const cross = (start, end, point) => (
    (end.x - start.x) * (point.y - start.y) - (end.y - start.y) * (point.x - start.x)
  );
  const first = cross(firstStart, firstEnd, secondStart);
  const second = cross(firstStart, firstEnd, secondEnd);
  const third = cross(secondStart, secondEnd, firstStart);
  const fourth = cross(secondStart, secondEnd, firstEnd);
  return first * second <= 0 && third * fourth <= 0;
}

function ringIntersectsRectangle(ring, rectangle) {
  const points = screenCoordinates(ring);
  if (points.some((point) => point.x >= rectangle.left && point.x <= rectangle.right
    && point.y >= rectangle.top && point.y <= rectangle.bottom)) return true;
  const corners = [
    { x: rectangle.left, y: rectangle.top }, { x: rectangle.right, y: rectangle.top },
    { x: rectangle.right, y: rectangle.bottom }, { x: rectangle.left, y: rectangle.bottom },
  ];
  if (corners.some((corner) => pointInRing(corner, points))) return true;
  return points.some((point, index) => corners.some((corner, cornerIndex) => (
    segmentsIntersect(point, points[(index + 1) % points.length], corner, corners[(cornerIndex + 1) % corners.length])
  )));
}

function featureIntersectsScreenRectangle(feature, rectangle) {
  if (feature.geometry?.type === "Point") {
    const point = screenPoint(...feature.geometry.coordinates);
    return point.x >= rectangle.left && point.x <= rectangle.right
      && point.y >= rectangle.top && point.y <= rectangle.bottom;
  }
  const rings = feature.geometry?.type === "Polygon" ? feature.geometry.coordinates
    : feature.geometry?.type === "MultiPolygon" ? feature.geometry.coordinates.flat()
      : [];
  return rings.some((ring) => ringIntersectsRectangle(ring, rectangle));
}

function selectionLabel(feature) {
  const { properties } = feature;
  return typeof properties.kody.selectionLabel === "string" && properties.kody.selectionLabel
    ? properties.kody.selectionLabel
    : feature.id;
}

function selectedTelemetry(properties) {
  const details = [];
  const altitudeFeet = Number(properties.altitudeBarometricFeet);
  if (Number.isFinite(altitudeFeet)) {
    details.push(`Alt ${Math.round(altitudeFeet).toLocaleString()} ft`);
  }
  const bearing = [
    properties.bearingDegrees,
    properties.trueHeadingDegrees,
    properties.trackDegrees,
    properties.courseDegrees,
  ].map(Number).find(Number.isFinite);
  if (Number.isFinite(bearing)) {
    details.push(`Brg ${Math.round((bearing % 360 + 360) % 360)}°`);
  }
  const speedKnots = [properties.groundSpeedKnots, properties.speedKnots]
    .map(Number)
    .find(Number.isFinite);
  if (Number.isFinite(speedKnots)) {
    details.push(`Spd ${Math.round(speedKnots)} kt`);
  } else {
    const speedMetresPerSecond = Number(properties.speedMetresPerSecond);
    if (Number.isFinite(speedMetresPerSecond)) {
      details.push(`Spd ${Math.round(speedMetresPerSecond * 3.6)} km/h`);
    }
  }
  return details.join(" · ");
}

function declaredFeatureActions(service, feature) {
  const actions = Array.isArray(service.manifest?.featureActions)
    ? service.manifest.featureActions
    : [];
  const allowedIds = Array.isArray(feature.properties.kodyActions)
    ? new Set(feature.properties.kodyActions)
    : null;
  return actions.filter((action) => (
    typeof action?.id === "string"
    && typeof action.label === "string"
    && typeof action.path === "string"
    && (allowedIds === null || allowedIds.has(action.id))
  ));
}

async function runSelectedFeatureAction(service, feature, action) {
  if (action.workspace?.mode && service.manifest?.capabilities?.workspace) {
    await configureWorkspace(service, { feature, action });
    return;
  }
  throw new Error(`Unsupported feature action: ${action.id}`);
}

function renderSelectedFeatures() {
  selectionList.replaceChildren();
  selectionEmpty.hidden = selectedFeatures.size > 0;
  for (const { service, feature } of selectedFeatures.values()) {
    const key = selectionKey(service, feature);
    const row = document.createElement("article");
    row.className = "selection-item";
    const item = document.createElement("div");
    item.className = "selection-item-row";
    const label = document.createElement("div");
    label.className = "selection-item-label";
    const name = document.createElement("strong");
    name.textContent = selectionLabel(feature);
    const metadata = document.createElement("small");
    const telemetry = selectedTelemetry(feature.properties);
    metadata.textContent = [service.displayName, telemetry].filter(Boolean).join(" · ");
    label.append(name, metadata);

    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "X";
    remove.setAttribute("aria-label", `Remove ${selectionLabel(feature)} from selection`);
    remove.addEventListener("click", () => {
      selectedFeatures.delete(key);
      if (centeredSelectionKey === key) {
        centeredSelectionKey = null;
      }
      if (selectionActionsKey === key) {
        selectionActionsKey = null;
      }
      renderSelectedFeatures();
    });

    const actions = document.createElement("button");
    actions.type = "button";
    actions.textContent = "→";
    actions.setAttribute("aria-label", `Actions for ${selectionLabel(feature)}`);
    actions.title = "Open item actions.";
    actions.addEventListener("click", () => {
      selectionActionsKey = selectionActionsKey === key ? null : key;
      renderSelectedFeatures();
    });
    item.append(label, remove, actions);
    row.append(item);
    if (selectionActionsKey === key) {
      const menu = document.createElement("div");
      menu.className = "selection-item-actions";
      const center = document.createElement("button");
      center.type = "button";
      center.textContent = centeredSelectionKey === key
        ? "Centering on screen"
        : "Center on screen";
      center.disabled = centeredSelectionKey === key;
      center.addEventListener("click", () => {
        centeredSelectionKey = key;
        centerOnSelectedFeature();
        renderSelectedFeatures();
      });
      menu.append(center);
      const serviceActions = declaredFeatureActions(service, feature);
      if (serviceActions.length === 0) {
        const unavailable = document.createElement("small");
        unavailable.textContent = "No service actions are registered.";
        menu.append(unavailable);
      } else {
        for (const action of serviceActions) {
          const actionButton = document.createElement("button");
          actionButton.type = "button";
          actionButton.textContent = action.label;
          actionButton.addEventListener("click", () => {
            runSelectedFeatureAction(service, feature, action).catch((error) => {
              status.textContent = `Unable to run ${action.label}: ${error.message}`;
            });
          });
          menu.append(actionButton);
        }
      }
      row.append(menu);
    }
    selectionList.append(row);
  }
}

function centerOnSelectedFeature() {
  const selected = centeredSelectionKey === null
    ? null
    : selectedFeatures.get(centeredSelectionKey);
  if (!selected) {
    centeredSelectionKey = null;
    return;
  }
  const [longitude, latitude] = featureFocusCoordinate(selected.feature);
  const { x, y } = MapMath.lonLatToWorld(longitude, latitude);
  const center = viewportCrosshairPoint();
  const nextCameraX = Math.max(
    0,
    Math.min(1, x - (center.x - canvas.clientWidth / 2) / scale()),
  );
  const nextCameraY = Math.max(
    0,
    Math.min(1, y - (center.y - canvas.clientHeight / 2) / scale()),
  );
  if (camera.x === nextCameraX && camera.y === nextCameraY) {
    return;
  }

  function featureFocusCoordinate(feature) {
    if (feature.geometry?.type === "Point") return feature.geometry.coordinates;
    const ring = feature.geometry?.type === "Polygon" ? feature.geometry.coordinates[0]
      : feature.geometry?.type === "MultiPolygon" ? feature.geometry.coordinates[0]?.[0]
        : null;
    if (!Array.isArray(ring) || ring.length === 0) return [0, 0];
    return [
      ring.reduce((total, [longitude]) => total + longitude, 0) / ring.length,
      ring.reduce((total, [, latitude]) => total + latitude, 0) / ring.length,
    ];
  }
  camera.x = nextCameraX;
  camera.y = nextCameraY;
  mapStateVersion += 1;
  refreshMap();
  invalidateAirLanesCache();
  scheduleViewportPublish();
}

function reconcileSelectedFeatures() {
  const available = new Map(
    selectableDataFeatures().map(({ service, feature }) => [selectionKey(service, feature), { service, feature }]),
  );
  for (const key of selectedFeatures.keys()) {
    const current = available.get(key);
    if (current) {
      selectedFeatures.set(key, current);
    } else {
      selectedFeatures.delete(key);
    }
  }
  centerOnSelectedFeature();
  renderSelectedFeatures();
}

function toggleSelectableFeaturesAt(point) {
  for (const { service, feature } of selectableDataFeatures()) {
    if (!featureContainsScreenPoint(feature, point)) continue;
    const key = selectionKey(service, feature);
    if (selectedFeatures.has(key)) {
      selectedFeatures.delete(key);
    } else {
      selectedFeatures.set(key, { service, feature });
    }
  }
  renderSelectedFeatures();
}

function toggleSelectableFeaturesInRectangle(start, end) {
  const left = Math.min(start.x, end.x);
  const right = Math.max(start.x, end.x);
  const top = Math.min(start.y, end.y);
  const bottom = Math.max(start.y, end.y);
  for (const { service, feature } of selectableDataFeatures()) {
    if (!featureIntersectsScreenRectangle(feature, { left, right, top, bottom })) continue;
    const key = selectionKey(service, feature);
    if (selectedFeatures.has(key)) {
      selectedFeatures.delete(key);
    } else {
      selectedFeatures.set(key, { service, feature });
    }
  }
  renderSelectedFeatures();
}

function drawSelectedFeatureMarkers() {
  context.save();
  context.strokeStyle = "#8df2ff";
  context.lineWidth = 2;
  for (const { feature } of selectedFeatures.values()) {
    context.beginPath();
    if (feature.geometry.type === "Point") {
      const point = screenPoint(...feature.geometry.coordinates);
      context.arc(point.x, point.y, 10, 0, 2 * Math.PI);
    } else {
      appendShapeGeometry(feature.geometry);
    }
    context.stroke();
  }
  if (selectionPointer) {
    context.setLineDash([3, 3]);
    context.beginPath();
    if (selectionPointer.isDragging) {
      context.rect(
        Math.min(selectionPointer.start.x, selectionPointer.current.x),
        Math.min(selectionPointer.start.y, selectionPointer.current.y),
        Math.abs(selectionPointer.current.x - selectionPointer.start.x),
        Math.abs(selectionPointer.current.y - selectionPointer.start.y),
      );
    } else {
      context.arc(
        selectionPointer.start.x,
        selectionPointer.start.y,
        SELECTION_CAPTURE_RADIUS_PIXELS,
        0,
        2 * Math.PI,
      );
    }
    context.stroke();
  }
  context.restore();
}

function showDisplayPanel(panel) {
  const showSelection = panel === "selection";
  layersTab.setAttribute("aria-selected", String(!showSelection));
  selectionTab.setAttribute("aria-selected", String(showSelection));
  layersPanel.hidden = showSelection;
  selectionPanel.hidden = !showSelection;
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
  reconcileSelectedFeatures();
}

function overlayDisplayName(service) {
  if (service.manifest?.capabilities?.workspace) {
    return service.displayName;
  }
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
  } else if (field.type === "select") {
    const input = document.createElement("select");
    input.dataset.configKey = field.key;
    input.required = field.required !== false;
    const options = field.options || [];
    if (field.placeholder) {
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = field.placeholder;
      placeholder.disabled = true;
      input.append(placeholder);
    }
    if (value && !options.some((option) => option.value === value)) {
      const savedChoice = document.createElement("option");
      savedChoice.value = value;
      savedChoice.textContent = "Saved selection (unavailable until service restart)";
      input.append(savedChoice);
    }
    for (const option of options) {
      const choice = document.createElement("option");
      choice.value = option.value;
      choice.textContent = option.label;
      input.append(choice);
    }
    input.value = value ?? "";
    container.append(input);
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
  const optionWarnings = [];
  const fields = await Promise.all(manifest.configuration.fields.map(async (field) => {
    let configuredField = field;
    if (field.optionsPath) {
      const optionsResponse = await fetch(`${service.url}${field.optionsPath}`);
      if (!optionsResponse.ok) {
        throw new Error(`${field.label} options returned ${optionsResponse.status}`);
      }
      const { options, warning } = await optionsResponse.json();
      if (!Array.isArray(options) || options.some((option) => (
        typeof option?.value !== "string" || typeof option.label !== "string"
      ))) {
        throw new Error(`${field.label} options are invalid`);
      }
      if (typeof warning === "string" && warning) optionWarnings.push(warning);
      configuredField = { ...field, options };
    }
    if (field.secretStoredKey && configuration[field.secretStoredKey]) {
      return configurationInput(
        {
          ...configuredField,
          label: `${configuredField.label} (saved locally)`,
          placeholder: "******** (enter a replacement to change it)",
        },
        configuration[field.key],
      );
    }
    return configurationInput(configuredField, configuration[field.key]);
  }));
  if (fields.length === 0) {
    const emptyState = document.createElement("p");
    emptyState.className = "empty-config-state";
    emptyState.textContent = "This service has no configurable settings.";
    serviceConfigFields.replaceChildren(emptyState);
  } else {
    serviceConfigFields.replaceChildren(...fields);
  }
  serviceConfigStatus.textContent = optionWarnings.join(" ");
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

function trackEditorInput(key, label, value, { type = "text", options = null } = {}) {
  const field = document.createElement("label");
  field.className = "service-config-field";
  field.textContent = label;
  const input = options ? document.createElement("select") : document.createElement("input");
  input.dataset.trackKey = key;
  if (options) {
    for (const option of options) {
      const element = document.createElement("option");
      element.value = option.value;
      element.textContent = option.label;
      element.selected = option.value === value;
      input.append(element);
    }
  } else {
    input.type = type;
    input.value = String(value);
    if (type === "number") input.step = "any";
  }
  field.append(input);
  return field;
}

function selectedTrackRoute() {
  return trackEditorSession?.scenario.routes.find((route) => route.id === trackRouteSelect.value) ?? null;
}

function applyTrackRouteForm() {
  const route = selectedTrackRoute();
  if (!route) return;
  route.name = trackRouteFields.querySelector('[data-track-key="name"]').value.trim();
  route.color = trackRouteFields.querySelector('[data-track-key="color"]').value;
  route.completionPolicy = trackRouteFields.querySelector('[data-track-key="completionPolicy"]').value;
  for (const leg of route.legs) {
    leg.targetAltitudeMetres = Number(trackWaypointList.querySelector(`[data-track-key="altitude:${leg.id}"]`).value);
    leg.targetSpeedMetresPerSecond = Number(trackWaypointList.querySelector(`[data-track-key="speed:${leg.id}"]`).value);
  }
  for (const aircraft of trackEditorSession.scenario.aircraft) {
    const assignment = trackAircraftList.querySelector(`[data-track-key="aircraft:${aircraft.id}"]`);
    if (assignment) aircraft.routeId = assignment.value;
  }
}

function renderTrackEditor() {
  const { scenario, capability } = trackEditorSession;
  const selectedRouteId = trackRouteSelect.value;
  trackRouteSelect.replaceChildren();
  for (const route of scenario.routes) {
    const option = document.createElement("option");
    option.value = route.id;
    option.textContent = route.name;
    option.selected = route.id === selectedRouteId;
    trackRouteSelect.append(option);
  }
  if (!trackRouteSelect.value && scenario.routes.length > 0) trackRouteSelect.value = scenario.routes[0].id;
  const route = selectedTrackRoute();
  trackRouteFields.replaceChildren();
  trackWaypointList.replaceChildren();
  trackAircraftList.replaceChildren();
  trackDeleteRoute.hidden = !route;
  trackSimulationToggle.textContent = trackEditorSession.simulation.running
    ? "Pause simulation"
    : "Resume simulation";
  if (!route) {
    trackRouteFields.append(Object.assign(document.createElement("p"), {
      className: "empty-config-state",
      textContent: "Draw a route to begin this scenario.",
    }));
    return;
  }
  trackRouteFields.append(
    trackEditorInput("name", "Route name", route.name),
    trackEditorInput("color", "Route color", route.color, { type: "color" }),
    trackEditorInput("completionPolicy", "Completion policy", route.completionPolicy, {
      options: capability.completionPolicies.map((policy) => ({
        value: policy,
        label: policy[0].toUpperCase() + policy.slice(1),
      })),
    }),
  );
  for (const leg of route.legs) {
    const item = document.createElement("div");
    item.className = "track-editor-item";
    const fields = document.createElement("div");
    fields.className = "track-editor-item-fields";
    fields.append(
      trackEditorInput(`altitude:${leg.id}`, `${leg.id} altitude (m)`, leg.targetAltitudeMetres, { type: "number" }),
      trackEditorInput(`speed:${leg.id}`, `${leg.id} speed (m/s)`, leg.targetSpeedMetresPerSecond, { type: "number" }),
    );
    const actions = document.createElement("div");
    actions.className = "track-editor-item-actions";
    const move = document.createElement("button");
    move.type = "button";
    move.textContent = "Move";
    move.addEventListener("click", () => {
      applyTrackRouteForm();
      trackWaypointMove = { routeId: route.id, legId: leg.id, preview: [...leg.waypoint] };
      trackEditorDialog.close();
      status.textContent = `Move ${leg.id}: drag its waypoint to a new position, or press Escape to cancel.`;
      refreshMap();
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Delete";
    remove.disabled = route.legs.length === 1;
    remove.addEventListener("click", () => {
      route.legs = route.legs.filter((candidate) => candidate.id !== leg.id);
      renderTrackEditor();
    });
    actions.append(move, remove);
    item.append(fields, actions);
    trackWaypointList.append(item);
  }
  for (const aircraft of scenario.aircraft) {
    const item = document.createElement("div");
    item.className = "track-editor-item";
    const assignment = trackEditorInput(`aircraft:${aircraft.id}`, aircraft.name, aircraft.routeId, {
      options: scenario.routes.map((candidate) => ({ value: candidate.id, label: candidate.name })),
    });
    item.append(assignment);
    trackAircraftList.append(item);
  }
}

function trackRouteId() {
  return `route-${crypto.randomUUID()}`;
}

function trackLegId(index) {
  return `waypoint-${index + 1}`;
}

function finishTrackRouteDrawing() {
  if (!trackRouteDrawing || trackRouteDrawing.points.length < 2) {
    status.textContent = "A route needs at least two waypoints.";
    return;
  }
  const route = {
    id: trackRouteId(),
    name: "New route",
    color: "#38c8ec",
    completionPolicy: "loop",
    legs: trackRouteDrawing.points.map((waypoint, index) => ({
      id: trackLegId(index),
      waypoint,
      targetAltitudeMetres: 1_000,
      targetSpeedMetresPerSecond: 80,
    })),
  };
  trackEditorSession.scenario.routes.push(route);
  trackRouteDrawing = null;
  trackRouteSelect.value = route.id;
  renderTrackEditor();
  trackEditorDialog.showModal();
  status.textContent = "Route drawn. Set its details and save the scenario.";
  refreshMap();
}

async function persistTrackScenario() {
  const { service, capability, scenario, revision } = trackEditorSession;
  const response = await fetch(`${service.url}${capability.scenarioPath}`, {
    method: capability.updateMethod || "PUT",
    headers: { "Content-Type": "application/json", [capability.revisionHeader]: revision },
    body: JSON.stringify(scenario),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `scenario update returned ${response.status}`);
  }
  const updated = await response.json();
  trackEditorSession.scenario = {
    schemaVersion: updated.schemaVersion,
    routes: updated.routes,
    aircraft: updated.aircraft,
  };
  trackEditorSession.revision = updated.revision;
  if (workspaceSession?.service === service && workspaceSession.scenario === scenario) {
    workspaceSession.scenarioRevision = updated.revision;
    await refreshWorkspaceDefinition();
  }
  await refreshShapes();
}

async function configureTrackEditor(service) {
  const capability = service.manifest.capabilities.scenarioEditor;
  const simulationCapability = service.manifest.capabilities.simulationControls;
  const [response, simulationResponse] = await Promise.all([
    fetch(`${service.url}${capability.scenarioPath}`),
    fetch(`${service.url}${simulationCapability.readPath}`),
  ]);
  if (!response.ok) throw new Error(`track scenario returned ${response.status}`);
  if (!simulationResponse.ok) throw new Error(`simulation state returned ${simulationResponse.status}`);
  const [scenario, simulation] = await Promise.all([response.json(), simulationResponse.json()]);
  trackEditorSession = {
    service,
    capability,
    revision: scenario.revision,
    scenario: {
      schemaVersion: scenario.schemaVersion,
      routes: scenario.routes,
      aircraft: scenario.aircraft,
    },
    simulationCapability,
    simulation,
  };
  trackEditorStatus.textContent = "";
  renderTrackEditor();
  trackEditorDialog.showModal();
}

function workspacePane(session) {
  return session.definition.panes.find((pane) => pane.id === session.activePane)
    ?? session.definition.panes[0];
}

function workspaceHeading(text) {
  const heading = document.createElement("h3");
  heading.className = "workspace-pane-heading";
  heading.textContent = text;
  return heading;
}

function workspaceSummary(pane) {
  return pane.summary ?? { metrics: [], items: [] };
}

function renderWorkspaceMetrics(metrics) {
  const container = document.createElement("div");
  container.className = "workspace-metrics";
  for (const metric of metrics) {
    const item = document.createElement("div");
    const value = document.createElement("strong");
    value.textContent = metric.value;
    const label = document.createElement("small");
    label.textContent = metric.label;
    item.append(value, label);
    container.append(item);
  }
  return container;
}

function formatSimulationElapsed(seconds) {
  const totalSeconds = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor(totalSeconds % 3600 / 60);
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(totalSeconds % 60).padStart(2, "0")}`;
}

function formatWallClockUtc(value) {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return "Unavailable";
  return timestamp.toLocaleTimeString("en-GB", {
    hour12: false,
    timeZone: "UTC",
  });
}

function renderMiniTimeControl() {
  const simulation = simTimeControlState;
  miniTimeControl.hidden = !displayMiniTimeControl || !simTimeControlService || !simulation;
  if (miniTimeControl.hidden) return;
  const simulationStatus = simulation.status || (simulation.running ? "running" : "paused");
  miniTimeReadout.textContent = formatSimulationElapsed(simulation.elapsedSeconds);
  miniTimePlay.disabled = simulationStatus === "running";
  miniTimePause.disabled = simulationStatus !== "running";
  miniTimeReset.disabled = simulationStatus === "stopped" && Number(simulation.elapsedSeconds) === 0;
}

function isSimTimeControlService(service) {
  return service.manifest?.capabilities?.workspace?.id === "sim-time-control"
    && Boolean(service.manifest?.capabilities?.simulationControls);
}

function setSimTimeControlState(service, simulation) {
  if (!isSimTimeControlService(service)) return;
  simTimeControlService = service;
  simTimeControlState = simulation;
  if (workspaceSession?.service === service) workspaceSession.simulation = simulation;
  renderMiniTimeControl();
}

async function sendMiniTimeControlAction(action) {
  if (!simTimeControlService || !simTimeControlState) {
    throw new Error("SimTimeControl is unavailable");
  }
  const capability = simTimeControlService.manifest.capabilities.simulationControls;
  const isReset = action === "reset";
  const response = await fetch(
    `${simTimeControlService.url}${isReset ? capability.resetPath : capability.writePath}`,
    isReset
      ? { method: "POST" }
      : {
        method: "PUT",
        headers: { "Content-Type": "application/json", [capability.revisionHeader]: simTimeControlState.revision },
        body: JSON.stringify({ action }),
      },
  );
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `simulation-time ${action} returned ${response.status}`);
  }
  setSimTimeControlState(simTimeControlService, await response.json());
  if (workspaceSession?.service === simTimeControlService) renderWorkspace();
}

function startMiniTimeControlUpdates() {
  const service = workspaceServices.find(isSimTimeControlService) ?? null;
  if (simTimeControlService !== service && simTimeControlEvents !== null) {
    simTimeControlEvents.close();
    simTimeControlEvents = null;
    simTimeControlState = null;
  }
  simTimeControlService = service;
  if (!service) {
    renderMiniTimeControl();
    return;
  }
  const capability = service.manifest.capabilities.simulationControls;
  fetch(`${service.url}${capability.readPath}`)
    .then((response) => {
      if (!response.ok) throw new Error(`simulation time returned ${response.status}`);
      return response.json();
    })
    .then((simulation) => setSimTimeControlState(service, simulation))
    .catch((error) => {
      if (displayMiniTimeControl) status.textContent = `Unable to load simulation time: ${error.message}`;
    });
  if (simTimeControlEvents !== null) return;
  simTimeControlEvents = new EventSource(`${service.url}${capability.eventStreamPath}`);
  for (const eventType of ["state", "sync", "started", "paused", "resumed", "stopped", "reset"]) {
    simTimeControlEvents.addEventListener(eventType, (event) => {
      try {
        const simulation = JSON.parse(event.data);
        if (
          typeof simulation.running !== "boolean"
          || typeof simulation.elapsedSeconds !== "number"
          || typeof simulation.revision !== "string"
          || typeof simulation.wallClockUtc !== "string"
        ) {
          throw new Error("event payload has an invalid simulation state");
        }
        setSimTimeControlState(service, simulation);
        if (workspaceSession?.service === service && !workspaceSession.editing) renderWorkspace();
      } catch (error) {
        status.textContent = `Unable to process simulation-time event: ${error.message}`;
      }
    });
  }
  simTimeControlEvents.addEventListener("error", () => {
    if (displayMiniTimeControl) status.textContent = "Simulation-time event stream disconnected; reconnecting.";
  });
}

function workspaceButton(label, handler, className = "secondary-action") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  button.addEventListener("click", handler);
  return button;
}

function workspaceField(label, value, options = {}) {
  const field = document.createElement("label");
  field.className = "service-config-field";
  field.textContent = label;
  const input = options.options ? document.createElement("select") : document.createElement("input");
  if (options.options) {
    for (const option of options.options) {
      const element = document.createElement("option");
      element.value = option.value;
      element.textContent = option.label;
      element.selected = option.value === value;
      input.append(element);
    }
  } else {
    input.type = options.type ?? "text";
    input.value = String(value);
    if (input.type === "number") input.step = "any";
  }
  input.dataset.workspaceField = options.key;
  input.disabled = Boolean(options.disabled);
  field.append(input);
  return field;
}

function workspaceManagementItems(pane) {
  if (pane.id === "aircraftTypes") return workspaceSession.aircraftTypes.types;
  if (pane.id === "routes") return workspaceSession.scenario.routes;
  if (pane.id === "aircraft") return workspaceSession.scenario.aircraft;
  return [];
}

function workspaceItemTitle(kind, item) {
  return kind === "aircraftTypes" ? item.displayName : kind === "routes" ? item.name : item.name;
}

function workspaceItemDescription(kind, item) {
  if (kind === "aircraftTypes") {
    return `${item.limits.minimumSpeedMetresPerSecond}–${item.limits.maximumSpeedMetresPerSecond} m/s · ${item.limits.maximumTurnRateDegreesPerSecond}°/s turn`;
  }
  if (kind === "routes") return `${item.legs.length} waypoints · ${item.completionPolicy}`;
  const aircraftType = workspaceSession.aircraftTypes.types.find((candidate) => candidate.id === item.aircraftTypeId);
  const route = workspaceSession.scenario.routes.find((candidate) => candidate.id === item.routeId);
  return `${aircraftType?.displayName ?? item.aircraftTypeId} · ${route?.name ?? item.routeId}`;
}

function waypointDefaultName(index) {
  return `WP${String(index + 1).padStart(2, "0")}`;
}

function newRouteWaypointPosition(horizontalOffset) {
  const crosshair = viewportCrosshairPoint();
  const containerBounds = container.getBoundingClientRect();
  const panelBounds = sidePanel.getBoundingClientRect();
  const usableLeft = Math.max(0, panelBounds.right - containerBounds.left);
  const usableWidth = canvas.clientWidth - usableLeft;
  const point = MapMath.worldToLonLat(
    camera.x + (crosshair.x + usableWidth * horizontalOffset - canvas.clientWidth / 2) / scale(),
    camera.y + (crosshair.y - canvas.clientHeight / 2) / scale(),
  );
  return [point.longitude, point.latitude];
}

function workspaceCreateItem(kind) {
  const id = `${kind === "aircraftTypes" ? "type" : kind === "routes" ? "route" : "aircraft"}-${crypto.randomUUID()}`;
  if (kind === "aircraftTypes") {
    return {
      id,
      displayName: "New aircraft type",
      symbol: "plane",
      limits: {
        minimumSpeedMetresPerSecond: 40,
        maximumSpeedMetresPerSecond: 120,
        minimumAltitudeMetres: 0,
        maximumAltitudeMetres: 10_000,
        maximumClimbRateMetresPerSecond: 8,
        maximumDescentRateMetresPerSecond: 8,
        maximumTurnRateDegreesPerSecond: 3,
        maximumBankAngleDegrees: 30,
        maximumAccelerationMetresPerSecondSquared: 4,
        maximumDecelerationMetresPerSecondSquared: 4,
      },
    };
  }
  if (kind === "routes") {
    const left = newRouteWaypointPosition(-0.25);
    const right = newRouteWaypointPosition(0.25);
    return {
      id,
      name: "New route",
      color: "#38c8ec",
      completionPolicy: "loop",
      legs: [
        { id: "waypoint-1", name: "WP01", waypoint: left, targetAltitudeMetres: 1_000, targetSpeedMetresPerSecond: 80 },
        { id: "waypoint-2", name: "WP02", waypoint: right, targetAltitudeMetres: 1_000, targetSpeedMetresPerSecond: 80 },
      ],
    };
  }
  const type = workspaceSession.aircraftTypes.types[0];
  const route = workspaceSession.scenario.routes[0];
  if (!type || !route) throw new Error("Create an aircraft type and route before adding an aircraft.");
  return {
    id,
    name: "New aircraft",
    aircraftTypeId: type.id,
    routeId: route.id,
    color: "#ff9d42",
    initialPosition: [...route.legs[0].waypoint],
    initialAltitudeMetres: route.legs[0].targetAltitudeMetres,
    initialSpeedMetresPerSecond: Math.max(type.limits.minimumSpeedMetresPerSecond, route.legs[0].targetSpeedMetresPerSecond),
    state: "running",
  };
}

function workspaceEditItem(kind, item) {
  workspaceSession.editing = { kind, id: item.id, draft: structuredClone(item), isNew: false };
  renderWorkspace();
}

function workspaceAddItem(kind) {
  try {
    const item = workspaceCreateItem(kind);
    workspaceSession.editing = { kind, id: item.id, draft: item, isNew: true };
    renderWorkspace();
  } catch (error) {
    status.textContent = error.message;
  }
}

async function removeWorkspaceItem(kind, item) {
  if (kind === "aircraftTypes") {
    if (workspaceSession.aircraftTypes.types.length === 1) throw new Error("At least one aircraft type is required.");
    if (workspaceSession.scenario.aircraft.some((aircraft) => aircraft.aircraftTypeId === item.id)) {
      throw new Error("Reassign or remove aircraft using this type before removing it.");
    }
    workspaceSession.aircraftTypes.types = workspaceSession.aircraftTypes.types.filter((candidate) => candidate.id !== item.id);
    await persistWorkspaceAircraftTypes();
  } else if (kind === "routes") {
    if (workspaceSession.scenario.aircraft.some((aircraft) => aircraft.routeId === item.id)) {
      throw new Error("Reassign or remove aircraft using this route before removing it.");
    }
    workspaceSession.scenario.routes = workspaceSession.scenario.routes.filter((candidate) => candidate.id !== item.id);
    await persistWorkspaceScenario();
  } else {
    workspaceSession.scenario.aircraft = workspaceSession.scenario.aircraft.filter((candidate) => candidate.id !== item.id);
    await persistWorkspaceScenario();
  }
  workspaceSession.editing = null;
  status.textContent = `${workspaceItemTitle(kind, item)} removed.`;
}

async function persistWorkspaceAircraftTypes() {
  const { service, aircraftTypes, aircraftTypesRevision } = workspaceSession;
  const response = await fetch(`${service.url}${workspaceSession.definition.resources.aircraftTypes}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", "If-Match": aircraftTypesRevision },
    body: JSON.stringify(aircraftTypes),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `aircraft type update returned ${response.status}`);
  }
  const updated = await response.json();
  workspaceSession.aircraftTypes = { schemaVersion: updated.schemaVersion, types: updated.types };
  workspaceSession.aircraftTypesRevision = updated.revision;
  await refreshWorkspaceDefinition();
}

async function persistWorkspaceScenario() {
  const { service, scenario, scenarioRevision, scenarioCapability } = workspaceSession;
  const response = await fetch(`${service.url}${scenarioCapability.scenarioPath}`, {
    method: scenarioCapability.updateMethod || "PUT",
    headers: { "Content-Type": "application/json", [scenarioCapability.revisionHeader]: scenarioRevision },
    body: JSON.stringify(scenario),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `scenario update returned ${response.status}`);
  }
  const updated = await response.json();
  workspaceSession.scenario = { schemaVersion: updated.schemaVersion, routes: updated.routes, aircraft: updated.aircraft };
  workspaceSession.scenarioRevision = updated.revision;
  await refreshWorkspaceDefinition();
  await refreshShapes();
}

async function saveWorkspaceItem({ editRouteOnMap = false } = {}) {
  const editing = workspaceSession.editing;
  if (!editing) return;
  const fields = workspaceInspector.querySelectorAll("[data-workspace-field]");
  const values = Object.fromEntries([...fields].map((field) => [field.dataset.workspaceField, field.value]));
  const { draft } = editing;
  if (editing.kind === "aircraftTypes") {
    draft.displayName = values.displayName.trim();
    draft.symbol = values.symbol.trim();
    for (const key of Object.keys(draft.limits)) draft.limits[key] = Number(values[`limits.${key}`]);
    if (editing.isNew) workspaceSession.aircraftTypes.types.push(draft);
    else Object.assign(workspaceSession.aircraftTypes.types.find((item) => item.id === editing.id), draft);
    await persistWorkspaceAircraftTypes();
  } else if (editing.kind === "routes") {
    draft.name = values.name.trim();
    draft.color = values.color;
    draft.completionPolicy = values.completionPolicy;
    for (const [index, leg] of draft.legs.entries()) {
      leg.name = values[`name.${leg.id}`].trim();
      const longitude = Number(values[`longitude.${leg.id}`]);
      const latitude = Number(values[`latitude.${leg.id}`]);
      if (!Number.isFinite(longitude) || longitude < -180 || longitude > 180) {
        throw new Error(`${leg.name || waypointDefaultName(index)} longitude must be between -180 and 180.`);
      }
      if (!Number.isFinite(latitude) || latitude < -90 || latitude > 90) {
        throw new Error(`${leg.name || waypointDefaultName(index)} latitude must be between -90 and 90.`);
      }
      leg.waypoint = [longitude, latitude];
      leg.targetAltitudeMetres = Number(values[`altitude.${leg.id}`]);
      leg.targetSpeedMetresPerSecond = Number(values[`speed.${leg.id}`]);
    }
    if (editing.isNew) workspaceSession.scenario.routes.push(draft);
    else Object.assign(workspaceSession.scenario.routes.find((item) => item.id === editing.id), draft);
    await persistWorkspaceScenario();
    if (editRouteOnMap) {
      const route = workspaceSession.scenario.routes.find((item) => item.id === editing.id);
      if (!route) throw new Error("Saved route is no longer available for map editing.");
      workspaceSession.editing = null;
      startWorkspaceRouteMapEditing(route);
      return;
    }
  } else {
    draft.name = values.name.trim();
    draft.aircraftTypeId = values.aircraftTypeId;
    draft.routeId = values.routeId;
    draft.color = values.color;
    draft.initialPosition = [Number(values.longitude), Number(values.latitude)];
    draft.initialAltitudeMetres = Number(values.initialAltitudeMetres);
    draft.initialSpeedMetresPerSecond = Number(values.initialSpeedMetresPerSecond);
    draft.state = values.state;
    if (editing.isNew) workspaceSession.scenario.aircraft.push(draft);
    else Object.assign(workspaceSession.scenario.aircraft.find((item) => item.id === editing.id), draft);
    await persistWorkspaceScenario();
  }
  workspaceSession.editing = null;
  status.textContent = `${workspaceItemTitle(editing.kind, draft)} saved.`;
  renderWorkspace();
}

function renderWorkspaceEditor() {
  const editing = workspaceSession.editing;
  if (!editing) return false;
  const { kind, draft } = editing;
  const editorPane = kind === "routes" ? workspacePrimary : workspaceInspector;
  editorPane.replaceChildren(workspaceHeading(editing.isNew ? `Add ${kind === "aircraftTypes" ? "aircraft type" : kind.slice(0, -1)}` : `Edit ${workspaceItemTitle(kind, draft)}`));
  const fields = document.createElement("div");
  fields.className = "workspace-editor-fields";
  if (kind === "aircraftTypes") {
    fields.append(workspaceField("Name", draft.displayName, { key: "displayName" }), workspaceField("Symbol", draft.symbol, { key: "symbol" }));
    for (const [key, value] of Object.entries(draft.limits)) fields.append(workspaceField(key.replace(/([A-Z])/g, " $1"), value, { key: `limits.${key}`, type: "number" }));
  } else if (kind === "routes") {
    const routeDetails = document.createElement("div");
    routeDetails.className = "workspace-route-details";
    routeDetails.append(
      workspaceField("Route name", draft.name, { key: "name" }),
      workspaceField("Route color", draft.color, { key: "color", type: "color" }),
      workspaceField("Completion policy", draft.completionPolicy, { key: "completionPolicy", options: workspaceSession.scenarioCapability.completionPolicies.map((value) => ({ value, label: value })) }),
    );
    fields.append(routeDetails);
    const mapEdit = workspaceButton("Edit waypoints on map", () => {
      saveWorkspaceItem({ editRouteOnMap: true }).catch((error) => {
        status.textContent = `Unable to save route for map editing: ${error.message}`;
      });
    });
    fields.append(mapEdit);
    for (const [index, leg] of draft.legs.entries()) {
      const group = document.createElement("div");
      group.className = "workspace-waypoint-fields";
      const details = document.createElement("div");
      details.className = "workspace-waypoint-fields-details";
      details.append(
        workspaceField("Waypoint name", leg.name || waypointDefaultName(index), { key: `name.${leg.id}` }),
        workspaceField("Longitude", leg.waypoint[0], { key: `longitude.${leg.id}`, type: "number" }),
        workspaceField("Latitude", leg.waypoint[1], { key: `latitude.${leg.id}`, type: "number" }),
        workspaceField("Altitude (m)", leg.targetAltitudeMetres, { key: `altitude.${leg.id}`, type: "number" }),
        workspaceField("Speed (m/s)", leg.targetSpeedMetresPerSecond, { key: `speed.${leg.id}`, type: "number" }),
      );
      const actions = document.createElement("div");
      actions.className = "workspace-waypoint-actions";
      const next = draft.legs[index + 1];
      if (next) {
        const insert = workspaceButton("Insert after", () => {
          draft.legs.splice(index + 1, 0, {
            id: `waypoint-${crypto.randomUUID()}`,
            name: waypointDefaultName(index + 1),
            waypoint: [(leg.waypoint[0] + next.waypoint[0]) / 2, (leg.waypoint[1] + next.waypoint[1]) / 2],
            targetAltitudeMetres: leg.targetAltitudeMetres,
            targetSpeedMetresPerSecond: leg.targetSpeedMetresPerSecond,
          });
          renderWorkspace();
        });
        actions.append(insert);
      }
      const remove = workspaceButton("Remove", () => {
        if (draft.legs.length === 2) {
          status.textContent = "A route needs at least two waypoints.";
          return;
        }
        draft.legs = draft.legs.filter((candidate) => candidate.id !== leg.id);
        renderWorkspace();
      }, "danger-action");
      actions.append(remove);
      group.append(details, actions);
      fields.append(group);
    }
  } else {
    fields.append(
      workspaceField("Name", draft.name, { key: "name" }),
      workspaceField("Aircraft type", draft.aircraftTypeId, { key: "aircraftTypeId", options: workspaceSession.aircraftTypes.types.map((item) => ({ value: item.id, label: item.displayName })) }),
      workspaceField("Route", draft.routeId, { key: "routeId", options: workspaceSession.scenario.routes.map((item) => ({ value: item.id, label: item.name })) }),
      workspaceField("Color", draft.color, { key: "color", type: "color" }),
      workspaceField("Longitude", draft.initialPosition[0], { key: "longitude", type: "number" }),
      workspaceField("Latitude", draft.initialPosition[1], { key: "latitude", type: "number" }),
      workspaceField("Initial altitude (m)", draft.initialAltitudeMetres, { key: "initialAltitudeMetres", type: "number" }),
      workspaceField("Initial speed (m/s)", draft.initialSpeedMetresPerSecond, { key: "initialSpeedMetresPerSecond", type: "number" }),
      workspaceField("State", draft.state, { key: "state", options: [{ value: "running", label: "Running" }, { value: "stopped", label: "Stopped" }] }),
    );
  }
  const actions = document.createElement("div");
  actions.className = "workspace-editor-actions";
  actions.append(workspaceButton("Cancel", () => { workspaceSession.editing = null; renderWorkspace(); }), workspaceButton("Save", () => {
    saveWorkspaceItem().catch((error) => { status.textContent = `Unable to save: ${error.message}`; });
  }, "primary-action"));
  editorPane.append(fields, actions);
  return true;
}

function startWorkspaceRouteMapEditing(route) {
  const savedRoute = workspaceSession.scenario.routes.find((candidate) => candidate.id === route.id);
  if (!savedRoute) throw new Error("Save the route before editing its waypoints on the map.");
  trackEditorSession = {
    service: workspaceSession.service,
    capability: workspaceSession.scenarioCapability,
    revision: workspaceSession.scenarioRevision,
    scenario: workspaceSession.scenario,
  };
  trackRouteEditing = { routeId: savedRoute.id };
  workspaceDialog.close();
  status.textContent = `Editing ${savedRoute.name}: drag any cyan waypoint handle. Press Escape when finished.`;
  refreshMap();
}

function formatTerrainCoordinate(point) {
  return `${point.latitude.toFixed(5)}°, ${point.longitude.toFixed(5)}°`;
}

function formatTerrainMetres(value) {
  return value === null || value === undefined ? "No data" : `${Math.round(value)} m`;
}

function renderTerrainProfileChart(profile) {
  const chart = document.createElement("canvas");
  chart.className = "terrain-profile-chart";
  chart.width = 720;
  chart.height = 280;
  chart.setAttribute("aria-label", "Vertical terrain elevation profile");
  const chartContext = chart.getContext("2d");
  const padding = { top: 22, right: 18, bottom: 34, left: 56 };
  const width = chart.width - padding.left - padding.right;
  const height = chart.height - padding.top - padding.bottom;
  const samples = profile.samples;
  const heights = samples
    .map((sample) => sample.heightMetres)
    .filter((height) => typeof height === "number");
  chartContext.fillStyle = "#07111e";
  chartContext.fillRect(0, 0, chart.width, chart.height);
  if (heights.length === 0) {
    chartContext.fillStyle = "#a8cbd9";
    chartContext.font = "13px system-ui, sans-serif";
    chartContext.textAlign = "center";
    chartContext.fillText("No terrain coverage along this profile.", chart.width / 2, chart.height / 2);
    return chart;
  }
  const minimum = Math.min(...heights);
  const maximum = Math.max(...heights);
  const span = Math.max(1, maximum - minimum);
  const xFor = (sample) => padding.left + width * sample.distanceMetres / Math.max(1, profile.distanceMetres);
  const yFor = (elevation) => padding.top + height * (1 - (elevation - minimum) / span);
  chartContext.strokeStyle = "rgb(137 214 246 / 25%)";
  chartContext.lineWidth = 1;
  chartContext.beginPath();
  chartContext.moveTo(padding.left, padding.top);
  chartContext.lineTo(padding.left, padding.top + height);
  chartContext.lineTo(padding.left + width, padding.top + height);
  chartContext.stroke();
  chartContext.fillStyle = "#8fbacb";
  chartContext.font = "11px system-ui, sans-serif";
  chartContext.textAlign = "right";
  chartContext.fillText(`${Math.round(maximum)} m`, padding.left - 8, padding.top + 4);
  chartContext.fillText(`${Math.round(minimum)} m`, padding.left - 8, padding.top + height);
  chartContext.textAlign = "left";
  chartContext.fillText("Start", padding.left, chart.height - 10);
  chartContext.textAlign = "right";
  chartContext.fillText(`${Math.round(profile.distanceMetres / 1000 * 10) / 10} km`, padding.left + width, chart.height - 10);
  chartContext.strokeStyle = "#ffbf69";
  chartContext.lineWidth = 2;
  let drawing = false;
  for (const sample of samples) {
    if (typeof sample.heightMetres !== "number") {
      drawing = false;
      continue;
    }
    const x = xFor(sample);
    const y = yFor(sample.heightMetres);
    if (!drawing) {
      chartContext.beginPath();
      chartContext.moveTo(x, y);
      drawing = true;
    } else {
      chartContext.lineTo(x, y);
    }
    chartContext.stroke();
  }
  return chart;
}

function startTerrainProfileDrawing() {
  terrainProfileDrawing = { start: null, preview: null };
  workspaceDialog.close();
  status.textContent = "Choose the terrain-profile start point on the map.";
  refreshMap();
}

async function finishTerrainProfileDrawing(end) {
  const drawing = terrainProfileDrawing;
  if (!drawing?.start || drawing.submitting || !workspaceSession) return;
  drawing.submitting = true;
  drawing.preview = null;
  refreshMap();
  try {
    const response = await fetch(`${workspaceSession.service.url}${workspaceSession.definition.resources.profilePath}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        start: { longitude: drawing.start[0], latitude: drawing.start[1] },
        end: { longitude: end[0], latitude: end[1] },
      }),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `terrain profile returned ${response.status}`);
    }
    workspaceSession.terrainProfile = await response.json();
    workspaceSession.activePane = "profiles";
    terrainProfileDrawing = null;
    renderWorkspace();
    workspaceDialog.showModal();
    status.textContent = "Terrain profile created.";
  } catch (error) {
    drawing.submitting = false;
    throw error;
  }
  refreshMap();
}

function renderTerrainAnalysisWorkspace() {
  const { definition } = workspaceSession;
  const pane = workspacePane(workspaceSession);
  workspaceContent.classList.remove("is-route-editing");
  workspaceContent.classList.add("is-terrain-analysis");
  workspaceNavigator.replaceChildren();
  for (const item of definition.panes) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "workspace-nav-item";
    button.textContent = item.label;
    button.setAttribute("aria-current", String(item.id === pane.id ? "page" : false));
    button.addEventListener("click", () => {
      workspaceSession.activePane = item.id;
      renderWorkspace();
    });
    workspaceNavigator.append(button);
  }
  workspacePrimary.replaceChildren(workspaceHeading(pane.label));
  workspacePrimary.append(Object.assign(document.createElement("p"), {
    className: "workspace-copy",
    textContent: pane.description,
  }));
  const actions = document.createElement("div");
  actions.className = "workspace-pane-toolbar";
  actions.append(workspaceButton("Draw terrain profile", startTerrainProfileDrawing, "primary-action"));
  workspacePrimary.append(actions);
  const profile = workspaceSession.terrainProfile;
  if (profile) {
    const details = document.createElement("div");
    details.className = "terrain-profile-details";
    const statistics = [
      ["Start", formatTerrainCoordinate(profile.start)],
      ["End", formatTerrainCoordinate(profile.end)],
      ["Distance", `${Math.round(profile.distanceMetres)} m`],
      ["Minimum", formatTerrainMetres(profile.statistics.minimumMetres)],
      ["Maximum", formatTerrainMetres(profile.statistics.maximumMetres)],
      ["Gaps", String(profile.statistics.missingSampleCount)],
    ];
    for (const [label, value] of statistics) {
      const item = document.createElement("div");
      item.append(
        Object.assign(document.createElement("small"), { textContent: label }),
        Object.assign(document.createElement("strong"), { textContent: value }),
      );
      details.append(item);
    }
    workspacePrimary.append(details, renderTerrainProfileChart(profile));
  } else {
    workspacePrimary.append(Object.assign(document.createElement("p"), {
      className: "workspace-placeholder",
      textContent: "Draw a line on the map to create a vertical terrain profile.",
    }));
  }
  workspaceInspector.replaceChildren();
  workspaceTimeline.replaceChildren(Object.assign(document.createElement("p"), {
    className: "workspace-copy",
    textContent: profile
      ? "Profile samples show local elevation coverage and preserve gaps where no terrain is available."
      : "Choose a start point and an end point to begin terrain analysis.",
  }));
}

function renderSimTimeControlWorkspace() {
  const { definition } = workspaceSession;
  const pane = workspacePane(workspaceSession);
  workspaceContent.classList.remove("is-route-editing", "is-terrain-analysis");
  workspaceContent.classList.add("is-sim-time-control");
  workspaceNavigator.replaceChildren();
  for (const item of definition.panes) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "workspace-nav-item";
    button.textContent = item.label;
    button.setAttribute("aria-current", String(item.id === pane.id ? "page" : false));
    button.addEventListener("click", () => {
      workspaceSession.activePane = item.id;
      renderWorkspace();
    });
    workspaceNavigator.append(button);
  }
  workspacePrimary.replaceChildren(
    workspaceHeading(pane.label),
    Object.assign(document.createElement("p"), { className: "workspace-copy", textContent: pane.description }),
  );
  const simulation = workspaceSession.simulation;
  if (!simulation) {
    workspacePrimary.append(Object.assign(document.createElement("p"), {
      className: "workspace-placeholder",
      textContent: "Loading simulation time controls.",
    }));
  } else {
    const simulationStatus = simulation.status || (simulation.running ? "running" : "paused");
    workspacePrimary.append(renderWorkspaceMetrics([
      { label: "Wall clock (UTC)", value: formatWallClockUtc(simulation.wallClockUtc) },
      { label: "Sim seconds", value: `${Number(simulation.elapsedSeconds).toFixed(3)} s` },
      { label: "Sim time", value: formatSimulationElapsed(simulation.elapsedSeconds) },
      { label: "State", value: simulationStatus },
    ]));
    const controls = document.createElement("div");
    controls.className = "workspace-editor-actions";
    for (const [label, action, enabled] of [
      ["Start", "start", simulationStatus === "stopped"],
      ["Pause", "pause", simulationStatus === "running"],
      ["Resume", "resume", simulationStatus === "paused"],
      ["Stop", "stop", simulationStatus !== "stopped"],
    ]) {
      const button = workspaceButton(label, () => {
        updateSimTimeControl(action).catch((error) => {
          status.textContent = `Unable to ${action} simulation time: ${error.message}`;
        });
      }, action === "start" || action === "resume" ? "primary-action" : "secondary-action");
      button.disabled = !enabled;
      controls.append(button);
    }
    const reset = workspaceButton("Reset", () => {
        resetWorkspaceSimulation().catch((error) => {
          status.textContent = `Unable to reset simulation time: ${error.message}`;
        });
      });
    controls.append(reset);
    const miniTimeToggle = document.createElement("label");
    miniTimeToggle.className = "workspace-checkbox";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = displayMiniTimeControl;
    checkbox.addEventListener("change", () => {
      displayMiniTimeControl = checkbox.checked;
      saveLayerSelection();
      renderMiniTimeControl();
    });
    miniTimeToggle.append(checkbox, " Display Mini Time Control");
    workspacePrimary.append(controls, miniTimeToggle);
  }
  workspaceInspector.replaceChildren();
  workspaceTimeline.replaceChildren(Object.assign(document.createElement("p"), {
    className: "workspace-copy",
    textContent: definition.timeline.description,
  }));
}


function shapeWorkspaceField(label, key, value, options = {}) {
  return workspaceField(label, value, { key: `shape:${key}`, ...options });
}

function defaultShapeDraft() {
  const point = MapMath.worldToLonLat(camera.x, camera.y);
  return {
    type: "circle", name: "New shape", color: "#38c8ec",
    center: [point.longitude, point.latitude], radius: 1000,
  };
}

function setShapeDraftType(type) {
  const previous = workspaceSession.shapeEditing.draft;
  const next = { ...defaultShapeDraft(), type, name: previous.name, color: previous.color };
  if (type === "rectangle") Object.assign(next, { origin: previous.origin ?? previous.center ?? next.center, width: 1000, height: 1000, angle: 0 });
  if (type === "annularSector") Object.assign(next, { center: previous.center ?? previous.origin ?? next.center, innerRadius: 0, outerRadius: 1000, startAngle: 0, endAngle: 90 });
  if (type === "polygon") Object.assign(next, { coordinates: previous.coordinates ?? [] });
  workspaceSession.shapeEditing.draft = next;
  renderWorkspace();
}

function shapeWorkspacePayload() {
  const draft = structuredClone(workspaceSession.shapeEditing.draft);
  const fields = workspaceInspector.querySelectorAll("[data-workspace-field^=\"shape:\"]");
  for (const field of fields) {
    const key = field.dataset.workspaceField.slice(6);
    if (key === "type") continue;
    draft[key] = field.type === "number" ? Number(field.value) : field.value;
  }
  if (draft.type === "circle" || draft.type === "annularSector") {
    draft.center = [Number(draft.centerLongitude), Number(draft.centerLatitude)];
    delete draft.centerLongitude; delete draft.centerLatitude;
  }
  if (draft.type === "rectangle") {
    draft.origin = [Number(draft.originLongitude), Number(draft.originLatitude)];
    delete draft.originLongitude; delete draft.originLatitude;
  }
  if (draft.type === "polygon") {
    draft.coordinates = JSON.parse(draft.coordinates);
  }
  return draft;
}

function shapeInspectorFields(draft) {
  const fields = [
    shapeWorkspaceField("Shape type", "type", draft.type, { options: ["circle", "rectangle", "annularSector", "polygon"].map((value) => ({ value, label: value === "annularSector" ? "Annular sector" : value[0].toUpperCase() + value.slice(1) })) }),
    shapeWorkspaceField("Name", "name", draft.name),
    shapeWorkspaceField("Color", "color", draft.color, { type: "color" }),
  ];
  if (draft.type === "circle" || draft.type === "annularSector") {
    fields.push(shapeWorkspaceField("Center longitude", "centerLongitude", draft.center?.[0] ?? 0, { type: "number" }), shapeWorkspaceField("Center latitude", "centerLatitude", draft.center?.[1] ?? 0, { type: "number" }));
  }
  if (draft.type === "rectangle") {
    fields.push(shapeWorkspaceField("Origin longitude", "originLongitude", draft.origin?.[0] ?? 0, { type: "number" }), shapeWorkspaceField("Origin latitude", "originLatitude", draft.origin?.[1] ?? 0, { type: "number" }));
  }
  if (draft.type === "circle") fields.push(shapeWorkspaceField("Radius (metres)", "radius", draft.radius ?? 1000, { type: "number" }));
  if (draft.type === "rectangle") fields.push(shapeWorkspaceField("Width (metres)", "width", draft.width ?? 1000, { type: "number" }), shapeWorkspaceField("Height (metres)", "height", draft.height ?? 1000, { type: "number" }), shapeWorkspaceField("Angle clockwise from north (degrees)", "angle", draft.angle ?? 0, { type: "number" }));
  if (draft.type === "annularSector") fields.push(shapeWorkspaceField("Inner radius (metres)", "innerRadius", draft.innerRadius ?? 0, { type: "number" }), shapeWorkspaceField("Outer radius (metres)", "outerRadius", draft.outerRadius ?? 1000, { type: "number" }), shapeWorkspaceField("Start bearing (degrees)", "startAngle", draft.startAngle ?? 0, { type: "number" }), shapeWorkspaceField("End bearing (degrees)", "endAngle", draft.endAngle ?? 90, { type: "number" }));
  if (draft.type === "polygon") {
    const field = document.createElement("label");
    field.className = "service-config-field";
    field.textContent = "Coordinates ([longitude, latitude] entries)";
    const input = document.createElement("textarea");
    input.rows = 6; input.dataset.workspaceField = "shape:coordinates";
    input.value = JSON.stringify(draft.coordinates ?? [], null, 2);
    field.append(input); fields.push(field);
  }
  return fields;
}

async function refreshShapesWorkspaceCatalog() {
  const { service, shapeCapability } = workspaceSession;
  const response = await fetch(`${service.url}${shapeCapability.readPath}`);
  if (!response.ok) throw new Error(`shape catalogue returned ${response.status}`);
  workspaceSession.shapeCatalog = await response.json();
}

async function persistShapeDraft(editing) {
  const { service, shapeCapability, shapeCatalog } = workspaceSession;
  const path = editing.isNew
    ? shapeCapability.createPath
    : shapeCapability.itemPath.replace("{id}", encodeURIComponent(editing.id));
  const response = await fetch(`${service.url}${path}`, {
    method: editing.isNew ? "POST" : shapeCapability.updateMethod,
    headers: {
      "Content-Type": "application/json",
      [shapeCapability.revisionHeader]: shapeCatalog.revision,
    },
    body: JSON.stringify(editing.draft),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `shape save returned ${response.status}`);
  }
  await refreshShapesWorkspaceCatalog();
  await refreshShapes();
}

function renderShapesWorkspace() {
  const catalog = workspaceSession.shapeCatalog;
  const editing = workspaceSession.shapeEditing;
  workspaceContent.classList.add("is-shapes-catalog");
  workspaceContent.classList.remove("is-route-editing", "is-terrain-analysis", "is-sim-time-control");
  workspaceNavigator.replaceChildren(workspaceHeading("Catalog"));
  for (const shape of catalog.shapes) {
    const navigatorItem = workspaceButton(shape.name, () => {
      workspaceSession.shapeEditing = { id: shape.id, isNew: false, draft: structuredClone(shape) };
      renderWorkspace();
    }, "workspace-nav-item");
    navigatorItem.setAttribute("aria-current", String(editing?.id === shape.id ? "page" : false));
    workspaceNavigator.append(navigatorItem);
  }
  workspacePrimary.replaceChildren(workspaceHeading("Catalog"));
  workspacePrimary.append(renderWorkspaceMetrics([{ label: "Shapes", value: String(catalog.shapes.length) }]));
  const toolbar = document.createElement("div"); toolbar.className = "workspace-pane-toolbar";
  toolbar.append(workspaceButton("New shape", () => { workspaceSession.shapeEditing = { isNew: true, draft: defaultShapeDraft() }; renderWorkspace(); }, "primary-action"));
  workspacePrimary.append(toolbar);
  const list = document.createElement("div"); list.className = "workspace-summary-cards";
  for (const shape of catalog.shapes) {
    const card = document.createElement("button"); card.type = "button"; card.className = "workspace-summary-card";
    card.setAttribute("aria-pressed", String(editing?.id === shape.id));
    card.append(Object.assign(document.createElement("strong"), { textContent: shape.name }), Object.assign(document.createElement("small"), { textContent: shape.type }));
    card.addEventListener("click", () => { workspaceSession.shapeEditing = { id: shape.id, isNew: false, draft: structuredClone(shape) }; renderWorkspace(); });
    list.append(card);
  }
  workspacePrimary.append(list);
  if (catalog.shapes.length === 0) workspacePrimary.append(Object.assign(document.createElement("p"), { className: "workspace-placeholder", textContent: "No shapes yet. Create a shape or draw one on the map." }));
  workspaceInspector.replaceChildren(workspaceHeading("Inspector"));
  if (editing) {
    const fields = document.createElement("div"); fields.className = "workspace-editor-fields"; fields.append(...shapeInspectorFields(editing.draft));
    fields.querySelector('[data-workspace-field="shape:type"]').addEventListener("change", (event) => {
      try {
        workspaceSession.shapeEditing.draft = shapeWorkspacePayload();
        setShapeDraftType(event.target.value);
      } catch (error) {
        status.textContent = `Unable to change shape type: ${error.message}`;
      }
    });
    const actions = document.createElement("div"); actions.className = "workspace-editor-actions";
    actions.append(workspaceButton("Draw on map", () => {
      try {
        workspaceSession.shapeEditing.draft = shapeWorkspacePayload();
        shapeDrawing = { type: editing.draft.type, points: [] };
        workspaceDialog.close();
        status.textContent = editing.draft.type === "polygon"
          ? "Click polygon vertices, then double-click to finish."
          : "Drag on the map to draw the shape.";
      } catch (error) {
        status.textContent = `Unable to draw shape: ${error.message}`;
      }
    }, "primary-action"));
    actions.append(workspaceButton("Edit on map", () => {
      try {
        workspaceSession.shapeEditing.draft = shapeWorkspacePayload();
        workspaceSession.shapeEditing.mapEditOrigin = "workspace";
        shapeMapEditing = workspaceSession.shapeEditing;
        workspaceDialog.close();
        status.textContent = "Drag cyan controls to edit this draft. Press Escape to return to the Shapes inspector.";
        refreshMap();
      } catch (error) {
        status.textContent = `Unable to edit shape on map: ${error.message}`;
      }
    }));
    if (!editing.isNew) actions.append(workspaceButton("Delete", () => {
      (async () => {
        const response = await fetch(`${workspaceSession.service.url}${workspaceSession.shapeCapability.itemPath.replace("{id}", encodeURIComponent(editing.id))}`, {
          method: workspaceSession.shapeCapability.deleteMethod,
          headers: { [workspaceSession.shapeCapability.revisionHeader]: catalog.revision },
        });
        if (!response.ok) throw new Error(`shape delete returned ${response.status}`);
        await refreshShapesWorkspaceCatalog();
        workspaceSession.shapeEditing = null;
        renderWorkspace();
        await refreshShapes();
      })().catch((error) => { status.textContent = `Unable to delete shape: ${error.message}`; });
    }, "danger-action"));
    actions.append(
      workspaceButton("Cancel", () => { workspaceSession.shapeEditing = null; renderWorkspace(); }),
      workspaceButton("Save", () => {
        (async () => {
          editing.draft = shapeWorkspacePayload();
          await persistShapeDraft(editing);
          workspaceSession.shapeEditing = null;
          renderWorkspace();
        })().catch((error) => { status.textContent = `Unable to save shape: ${error.message}`; });
      }, "primary-action"),
    );
    workspaceInspector.append(fields, actions);
  } else workspaceInspector.append(Object.assign(document.createElement("p"), { className: "workspace-copy", textContent: "Select a shape to edit it, or create a new definition." }));
  workspaceTimeline.replaceChildren(Object.assign(document.createElement("p"), { className: "workspace-copy", textContent: workspaceSession.definition.timeline.description }));
}

function renderWorkspace() {
  const { definition } = workspaceSession;
  if (definition.kind === "terrain-analysis") {
    renderTerrainAnalysisWorkspace();
    return;
  }
  if (definition.kind === "sim-time-control") {
    renderSimTimeControlWorkspace();
    return;
  }
  if (definition.kind === "shapes-catalog") {
    renderShapesWorkspace();
    return;
  }
  const pane = workspacePane(workspaceSession);
  workspaceContent.classList.remove("is-terrain-analysis", "is-sim-time-control", "is-shapes-catalog");
  workspaceContent.classList.toggle("is-route-editing", workspaceSession.editing?.kind === "routes");
  workspaceNavigator.replaceChildren();
  for (const item of definition.panes) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "workspace-nav-item";
    button.textContent = item.label;
    button.setAttribute("aria-current", String(item.id === pane.id ? "page" : false));
    button.addEventListener("click", () => {
      workspaceSession.activePane = item.id;
      renderWorkspace();
    });
    workspaceNavigator.append(button);
  }

  workspacePrimary.replaceChildren(workspaceHeading(pane.label));
  const description = document.createElement("p");
  description.className = "workspace-copy";
  description.textContent = pane.description;
  const summary = workspaceSummary(pane);
  workspacePrimary.append(description, renderWorkspaceMetrics(summary.metrics));
  const managedPane = ["aircraftTypes", "routes", "aircraft"].includes(pane.id);
  if (managedPane) {
    const toolbar = document.createElement("div");
    toolbar.className = "workspace-pane-toolbar";
    const singular = pane.id === "aircraftTypes" ? "aircraft type" : pane.id === "routes" ? "route" : "aircraft";
    toolbar.append(workspaceButton(`Add ${singular}`, () => workspaceAddItem(pane.id), "primary-action"));
    workspacePrimary.append(toolbar);
    const cards = document.createElement("div");
    cards.className = "workspace-summary-cards";
    for (const item of workspaceManagementItems(pane)) {
      const card = document.createElement("article");
      card.className = "workspace-summary-card";
      const title = document.createElement("strong");
      title.textContent = workspaceItemTitle(pane.id, item);
      const itemDescription = document.createElement("small");
      itemDescription.textContent = workspaceItemDescription(pane.id, item);
      const actions = document.createElement("div");
      actions.className = "workspace-card-actions";
      actions.append(
        workspaceButton("Edit", () => workspaceEditItem(pane.id, item)),
        workspaceButton("Remove", () => {
          removeWorkspaceItem(pane.id, item).catch((error) => { status.textContent = `Unable to remove: ${error.message}`; });
        }, "danger-action"),
      );
      card.append(title, itemDescription, actions);
      cards.append(card);
    }
    if (cards.childElementCount > 0) workspacePrimary.append(cards);
    else workspacePrimary.append(Object.assign(document.createElement("p"), { className: "workspace-placeholder", textContent: `No ${pane.label.toLowerCase()} yet.` }));
  } else {
    const cards = document.createElement("div");
    cards.className = "workspace-summary-cards";
    for (const item of summary.items) {
      const card = document.createElement("button");
      card.type = "button";
      card.className = "workspace-summary-card";
      card.setAttribute("aria-pressed", String(workspaceSession.activeItem?.id === item.id));
      const title = document.createElement("strong");
      title.textContent = item.title;
      const itemDescription = document.createElement("small");
      itemDescription.textContent = item.description;
      card.append(title, itemDescription);
      card.addEventListener("click", () => {
        workspaceSession.activeItem = item;
        renderWorkspace();
      });
      cards.append(card);
    }
    if (summary.items.length > 0) workspacePrimary.append(cards);
    if (summary.items.length === 0 && pane.id !== "timeline") {
      workspacePrimary.append(Object.assign(document.createElement("p"), { className: "workspace-placeholder", textContent: "No items are currently published for this workspace region." }));
    }
  }

  if (!renderWorkspaceEditor()) {
    workspaceInspector.replaceChildren(workspaceHeading("Inspector"));
    const inspectorCopy = document.createElement("p");
    inspectorCopy.className = "workspace-copy";
    inspectorCopy.textContent = workspaceSession.activeItem
      ? workspaceSession.activeItem.description
      : `Choose Edit on a ${pane.label.toLowerCase()} card to change it.`;
    if (workspaceSession.activeItem) {
      workspaceInspector.append(
        Object.assign(document.createElement("h4"), {
          className: "workspace-inspector-title",
          textContent: workspaceSession.activeItem.title,
        }),
      );
    }
    const resources = document.createElement("ul");
    resources.className = "workspace-resource-list";
    for (const [name, path] of Object.entries(definition.resources)) {
      const item = document.createElement("li");
      const label = document.createTextNode(`${name}: `);
      const code = document.createElement("code");
      code.textContent = path;
      item.append(label, code);
      resources.append(item);
    }
    workspaceInspector.append(inspectorCopy, resources);
  }

  workspaceTimeline.replaceChildren();
  const timelineRow = document.createElement("div");
  timelineRow.className = "workspace-timeline-row";
  const timelineCopy = document.createElement("div");
  timelineCopy.append(
    Object.assign(document.createElement("h3"), { textContent: "Timeline" }),
    Object.assign(document.createElement("p"), { textContent: definition.timeline.description }),
  );
  timelineRow.append(timelineCopy);
  if (workspaceSession.simulation) {
    const controls = document.createElement("div");
    controls.className = "workspace-timeline-controls";
    const elapsed = document.createElement("output");
    elapsed.textContent = formatSimulationElapsed(workspaceSession.simulation.elapsedSeconds);
    elapsed.title = `${Number(workspaceSession.simulation.elapsedSeconds).toFixed(1)} simulation seconds`;
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.textContent = workspaceSession.simulation.running ? "Pause" : "Resume";
    toggle.addEventListener("click", () => {
      updateWorkspaceSimulation(!workspaceSession.simulation.running).catch((error) => {
        status.textContent = `Unable to update workspace simulation: ${error.message}`;
      });
    });
    const reset = document.createElement("button");
    reset.type = "button";
    reset.textContent = "Reset";
    reset.addEventListener("click", () => {
      resetWorkspaceSimulation().catch((error) => {
        status.textContent = `Unable to reset workspace simulation: ${error.message}`;
      });
    });
    controls.append(elapsed, toggle, reset);
    timelineRow.append(controls);
  }
  workspaceTimeline.append(timelineRow);
}

async function refreshWorkspaceSimulation() {
  if (!workspaceSession?.simulationCapability) return;
  const { service, simulationCapability } = workspaceSession;
  const response = await fetch(`${service.url}${simulationCapability.readPath}`);
  if (!response.ok) throw new Error(`workspace simulation returned ${response.status}`);
  workspaceSession.simulation = await response.json();
  setSimTimeControlState(workspaceSession.service, workspaceSession.simulation);
  const timelinePane = workspaceSession.definition.panes.find((pane) => pane.id === "timeline");
  if (timelinePane) {
    timelinePane.summary.metrics = [
      { label: "State", value: workspaceSession.simulation.running ? "Running" : "Paused" },
      { label: "Elapsed", value: `${workspaceSession.simulation.elapsedSeconds.toFixed(1)} s` },
    ];
  }
  if (!workspaceSession.editing) renderWorkspace();
}

async function updateWorkspaceSimulation(running) {
  const { service, simulationCapability, simulation } = workspaceSession;
  const response = await fetch(`${service.url}${simulationCapability.writePath}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", [simulationCapability.revisionHeader]: simulation.revision },
    body: JSON.stringify({ running }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `workspace simulation update returned ${response.status}`);
  }
  workspaceSession.simulation = await response.json();
  await refreshWorkspaceSimulation();
  await refreshShapes();
}

async function updateSimTimeControl(action) {
  const { service, simulationCapability, simulation } = workspaceSession;
  const response = await fetch(`${service.url}${simulationCapability.writePath}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", [simulationCapability.revisionHeader]: simulation.revision },
    body: JSON.stringify({ action }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `simulation-time ${action} returned ${response.status}`);
  }
  workspaceSession.simulation = await response.json();
  setSimTimeControlState(service, workspaceSession.simulation);
  renderWorkspace();
}

async function resetWorkspaceSimulation() {
  const { service, simulationCapability } = workspaceSession;
  const response = await fetch(`${service.url}${simulationCapability.resetPath}`, { method: "POST" });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `workspace simulation reset returned ${response.status}`);
  }
  workspaceSession.simulation = await response.json();
  setSimTimeControlState(workspaceSession.service, workspaceSession.simulation);
  await refreshWorkspaceSimulation();
  await refreshShapes();
}

function startWorkspaceSimulationPolling() {
  if (!workspaceSession?.simulationCapability) return;
  if (workspaceSimulationTimer !== null) window.clearInterval(workspaceSimulationTimer);
  if (workspaceSimulationEvents !== null) {
    workspaceSimulationEvents.close();
    workspaceSimulationEvents = null;
  }
  const { service, simulationCapability } = workspaceSession;
  if (simulationCapability.eventStreamPath) {
    workspaceSimulationEvents = new EventSource(`${service.url}${simulationCapability.eventStreamPath}`);
    for (const eventType of ["state", "sync", "started", "paused", "resumed", "stopped", "reset"]) {
      workspaceSimulationEvents.addEventListener(eventType, (event) => {
        try {
          const simulation = JSON.parse(event.data);
          if (
            typeof simulation.running !== "boolean"
            || typeof simulation.elapsedSeconds !== "number"
            || typeof simulation.revision !== "string"
            || typeof simulation.wallClockUtc !== "string"
          ) {
            throw new Error("event payload has an invalid simulation state");
          }
          workspaceSession.simulation = simulation;
          if (!workspaceSession.editing) renderWorkspace();
        } catch (error) {
          status.textContent = `Unable to process simulation-time event: ${error.message}`;
        }
      });
    }
    workspaceSimulationEvents.addEventListener("error", () => {
      status.textContent = "Simulation-time event stream disconnected; reconnecting.";
    });
    return;
  }
  workspaceSimulationTimer = window.setInterval(() => {
    refreshWorkspaceSimulation().catch((error) => {
      status.textContent = `Unable to refresh workspace simulation: ${error.message}`;
    });
  }, 1000);
}

function stopWorkspaceSimulationPolling() {
  if (workspaceSimulationTimer !== null) window.clearInterval(workspaceSimulationTimer);
  workspaceSimulationTimer = null;
  if (workspaceSimulationEvents !== null) {
    workspaceSimulationEvents.close();
    workspaceSimulationEvents = null;
  }
}

function validateWorkspaceDefinition(definition) {
  if (
    !Array.isArray(definition.panes)
    || definition.panes.some((pane) => (
      typeof pane?.id !== "string"
      || typeof pane.label !== "string"
      || typeof pane.description !== "string"
      || !Array.isArray(pane.summary?.metrics)
      || !Array.isArray(pane.summary?.items)
    ))
    || !definition.resources
    || typeof definition.resources !== "object"
    || typeof definition.timeline?.description !== "string"
  ) {
    throw new Error("workspace returned an invalid definition");
  }
}

async function refreshWorkspaceDefinition() {
  const response = await fetch(`${workspaceSession.service.url}${workspaceSession.capability.openPath}`);
  if (!response.ok) throw new Error(`workspace returned ${response.status}`);
  const definition = await response.json();
  validateWorkspaceDefinition(definition);
  workspaceSession.definition = definition;
}

async function loadWorkspaceCatalogues() {
  const { service, definition, scenarioCapability } = workspaceSession;
  if (!scenarioCapability || !definition.resources.aircraftTypes || !definition.resources.scenario) return;
  const [typesResponse, scenarioResponse] = await Promise.all([
    fetch(`${service.url}${definition.resources.aircraftTypes}`),
    fetch(`${service.url}${definition.resources.scenario}`),
  ]);
  if (!typesResponse.ok) throw new Error(`aircraft types returned ${typesResponse.status}`);
  if (!scenarioResponse.ok) throw new Error(`scenario returned ${scenarioResponse.status}`);
  const [types, scenario] = await Promise.all([typesResponse.json(), scenarioResponse.json()]);
  workspaceSession.aircraftTypes = { schemaVersion: types.schemaVersion, types: types.types };
  workspaceSession.aircraftTypesRevision = types.revision;
  workspaceSession.scenario = { schemaVersion: scenario.schemaVersion, routes: scenario.routes, aircraft: scenario.aircraft };
  workspaceSession.scenarioRevision = scenario.revision;
}

async function configureWorkspace(service, selectedFeatureAction = null) {
  const capability = service.manifest.capabilities.workspace;
  const response = await fetch(`${service.url}${capability.openPath}`);
  if (!response.ok) throw new Error(`workspace returned ${response.status}`);
  const definition = await response.json();
  validateWorkspaceDefinition(definition);
  const simulationCapability = service.manifest.capabilities?.simulationControls;
  workspaceSession = {
    service,
    capability,
    definition,
    activePane: definition.panes[0]?.id,
    activeItem: null,
    simulationCapability,
    simulation: null,
    scenarioCapability: service.manifest.capabilities?.scenarioEditor,
    aircraftTypes: { schemaVersion: 1, types: [] },
    aircraftTypesRevision: null,
    scenario: { schemaVersion: 1, routes: [], aircraft: [] },
    scenarioRevision: null,
    editing: null,
    terrainProfile: null,
  };
  await loadWorkspaceCatalogues();
  if (definition.kind === "shapes-catalog") {
    workspaceSession.shapeCapability = service.manifest.capabilities.shapeEditor;
    await refreshShapesWorkspaceCatalog();
    workspaceSession.shapeEditing = null;
    const workspaceAction = selectedFeatureAction?.action.workspace;
    if (workspaceAction?.resource === "definitions") {
      const shapeId = selectedFeatureAction.feature.properties?.[workspaceAction.featureIdProperty];
      const shape = workspaceSession.shapeCatalog.shapes.find((item) => item.id === shapeId);
      if (!shape) throw new Error("Selected shape is no longer in the catalogue.");
      workspaceSession.shapeEditing = {
        id: shape.id,
        isNew: false,
        draft: structuredClone(shape),
        mapEditOrigin: "selection",
      };
      if (workspaceAction.mode === "edit-on-map") {
        shapeMapEditing = workspaceSession.shapeEditing;
      }
    }
  }
  if (simulationCapability) await refreshWorkspaceSimulation();
  workspaceEyebrow.textContent = service.displayName;
  workspaceTitle.textContent = definition.title;
  renderWorkspace();
  if (shapeMapEditing?.mapEditOrigin !== "selection") {
    workspaceDialog.showModal();
  }
  if (shapeMapEditing) {
    if (workspaceDialog.open) workspaceDialog.close();
    status.textContent = "Drag cyan controls to edit this draft. Press Escape to return to the Shapes inspector.";
    refreshMap();
  }
  startWorkspaceSimulationPolling();
}

function mapPosition(event) {
  const point = mapPointFromEvent(event);
  return [point.longitude, point.latitude];
}

function waypointAtMapEvent(event) {
  if (!trackRouteEditing || !trackEditorSession) return null;
  const route = trackEditorSession.scenario.routes.find((item) => item.id === trackRouteEditing.routeId);
  if (!route) return null;
  const bounds = canvas.getBoundingClientRect();
  const pointer = { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
  return route.legs.find((leg) => {
    const point = screenPoint(...leg.waypoint);
    return Math.hypot(point.x - pointer.x, point.y - pointer.y) <= 28;
  }) ?? null;
}

function metresBetween(first, second) {
  return measureDistanceKilometres(
    { longitude: first[0], latitude: first[1] },
    { longitude: second[0], latitude: second[1] },
  ) * 1000;
}

function destinationCoordinate(center, bearing, distance) {
  const [longitude, latitude] = center.map((value) => value * Math.PI / 180);
  const angularDistance = distance / (EARTH_RADIUS_KILOMETRES * 1000);
  const angle = bearing * Math.PI / 180;
  const destinationLatitude = Math.asin(
    Math.sin(latitude) * Math.cos(angularDistance)
    + Math.cos(latitude) * Math.sin(angularDistance) * Math.cos(angle),
  );
  const destinationLongitude = longitude + Math.atan2(
    Math.sin(angle) * Math.sin(angularDistance) * Math.cos(latitude),
    Math.cos(angularDistance) - Math.sin(latitude) * Math.sin(destinationLatitude),
  );
  return [
    ((destinationLongitude * 180 / Math.PI + 540) % 360) - 180,
    destinationLatitude * 180 / Math.PI,
  ];
}

function bearingBetween(first, second) {
  const [firstLongitude, firstLatitude] = first.map((value) => value * Math.PI / 180);
  const [secondLongitude, secondLatitude] = second.map((value) => value * Math.PI / 180);
  const longitudeDelta = secondLongitude - firstLongitude;
  const bearing = Math.atan2(
    Math.sin(longitudeDelta) * Math.cos(secondLatitude),
    Math.cos(firstLatitude) * Math.sin(secondLatitude)
      - Math.sin(firstLatitude) * Math.cos(secondLatitude) * Math.cos(longitudeDelta),
  ) * 180 / Math.PI;
  return (bearing + 360) % 360;
}

function shapeDraftGeometry(draft) {
  if (draft.type === "circle") {
    const coordinates = Array.from({ length: 65 }, (_, index) => (
      destinationCoordinate(draft.center, index * 360 / 64, draft.radius)
    ));
    return { type: "Polygon", coordinates: [coordinates] };
  }
  if (draft.type === "rectangle") {
    const radians = draft.angle * Math.PI / 180;
    const coordinates = [];
    for (const [east, north] of [
      [-draft.width / 2, -draft.height / 2], [draft.width / 2, -draft.height / 2],
      [draft.width / 2, draft.height / 2], [-draft.width / 2, draft.height / 2],
    ]) {
      const rotatedEast = east * Math.cos(radians) + north * Math.sin(radians);
      const rotatedNorth = -east * Math.sin(radians) + north * Math.cos(radians);
      coordinates.push(destinationCoordinate(
        draft.origin,
        Math.atan2(rotatedEast, rotatedNorth) * 180 / Math.PI,
        Math.hypot(rotatedEast, rotatedNorth),
      ));
    }
    coordinates.push(coordinates[0]);
    return { type: "Polygon", coordinates: [coordinates] };
  }
  if (draft.type === "annularSector") {
    const sweep = (draft.endAngle - draft.startAngle + 360) % 360 || 360;
    const angles = Array.from({ length: 65 }, (_, index) => draft.startAngle + sweep * index / 64);
    const outer = angles.map((angle) => destinationCoordinate(draft.center, angle, draft.outerRadius));
    const inner = [...angles].reverse().map((angle) => destinationCoordinate(draft.center, angle, draft.innerRadius));
    return { type: "Polygon", coordinates: [[...outer, ...inner, outer[0]]] };
  }
  if (draft.type === "polygon" && draft.coordinates.length >= 3) {
    return { type: "Polygon", coordinates: [[...draft.coordinates, draft.coordinates[0]]] };
  }
  return null;
}

function shapeMapControlPoints(draft) {
  if (draft.type === "circle") {
    return [
      { id: "center", coordinate: draft.center },
      { id: "radius", coordinate: destinationCoordinate(draft.center, 90, draft.radius) },
    ];
  }
  if (draft.type === "rectangle") {
    const widthBearing = 90 + draft.angle;
    const heightBearing = draft.angle;
    return [
      { id: "origin", coordinate: draft.origin },
      { id: "width", coordinate: destinationCoordinate(draft.origin, widthBearing, draft.width / 2) },
      { id: "height", coordinate: destinationCoordinate(draft.origin, heightBearing, draft.height / 2) },
      { id: "angle", coordinate: destinationCoordinate(draft.origin, widthBearing, draft.width / 2 + Math.max(100, draft.width * 0.2)) },
    ];
  }
  if (draft.type === "annularSector") {
    const sweep = (draft.endAngle - draft.startAngle + 360) % 360 || 360;
    const midpoint = draft.startAngle + sweep / 2;
    return [
      { id: "center", coordinate: draft.center },
      { id: "innerRadius", coordinate: destinationCoordinate(draft.center, midpoint, draft.innerRadius) },
      { id: "outerRadius", coordinate: destinationCoordinate(draft.center, midpoint, draft.outerRadius) },
      { id: "startAngle", coordinate: destinationCoordinate(draft.center, draft.startAngle, draft.outerRadius) },
      { id: "endAngle", coordinate: destinationCoordinate(draft.center, draft.endAngle, draft.outerRadius) },
    ];
  }
  return draft.coordinates.map((coordinate, index) => ({ id: `vertex:${index}`, coordinate }));
}

function shapeControlPointAtMapEvent(event) {
  const bounds = canvas.getBoundingClientRect();
  const pointer = { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
  return shapeMapControlPoints(shapeMapEditing.draft).find((control) => {
    const point = screenPoint(...control.coordinate);
    return Math.hypot(point.x - pointer.x, point.y - pointer.y) <= 16;
  }) ?? null;
}

function updateShapeMapControl(controlId, coordinate) {
  const draft = shapeMapEditing.draft;
  if (controlId === "center" || controlId === "origin") {
    draft[controlId] = coordinate;
  } else if (controlId === "radius" || controlId === "innerRadius" || controlId === "outerRadius") {
    draft[controlId] = Math.max(controlId === "innerRadius" ? 0 : 1, metresBetween(draft.center, coordinate));
    if (controlId === "innerRadius") draft.innerRadius = Math.min(draft.innerRadius, Math.max(0, draft.outerRadius - 1));
    if (controlId === "outerRadius") draft.outerRadius = Math.max(draft.outerRadius, draft.innerRadius + 1);
  } else if (controlId === "width" || controlId === "height") {
    const direction = controlId === "width" ? 90 + draft.angle : draft.angle;
    const offset = ((bearingBetween(draft.origin, coordinate) - direction + 540) % 360) - 180;
    draft[controlId] = Math.max(1, Math.abs(metresBetween(draft.origin, coordinate) * Math.cos(offset * Math.PI / 180)) * 2);
  } else if (controlId === "angle") {
    draft.angle = ((bearingBetween(draft.origin, coordinate) - 90 + 540) % 360) - 180;
  } else if (controlId === "startAngle" || controlId === "endAngle") {
    draft[controlId] = bearingBetween(draft.center, coordinate);
  } else if (controlId.startsWith("vertex:")) {
    draft.coordinates[Number(controlId.slice(7))] = coordinate;
  }
}

function finishShapeDrawing(end) {
  const { type, start } = shapeDrawing;
  const draft = workspaceSession?.shapeEditing?.draft;
  if (!draft) return;
  if (type === "circle") {
    Object.assign(draft, { center: start, radius: metresBetween(start, end) });
  } else if (type === "rectangle") {
    Object.assign(draft, {
      origin: start,
      width: metresBetween(start, [end[0], start[1]]) * 2,
      height: metresBetween(start, [start[0], end[1]]) * 2,
      angle: 0,
    });
  } else if (type === "annularSector") {
    Object.assign(draft, { center: start, innerRadius: 0, outerRadius: metresBetween(start, end), startAngle: 0, endAngle: 90 });
  }
  shapeDrawing = null;
  renderWorkspace();
  workspaceDialog.showModal();
  status.textContent = "Geometry captured. Review the inspector and save the shape.";
}

function renderOverlaySourceControls(savedSelections = {}, savedOpacities = {}) {
  dataSourceList.replaceChildren();
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
    const workspace = service.manifest?.capabilities?.workspace;
    configure.dataset.symbol = workspace ? "rocket" : "gear";
    configure.dataset.symbolFallback = workspace ? "🚀" : "⚙";
    configure.textContent = workspace ? "🚀" : "⚙";
    configure.title = workspace
      ? `Open ${workspace.label}`
      : `Configure ${overlayDisplayName(service)}`;
    configure.setAttribute("aria-label", configure.title);
    renderButtonSymbol(configure);
    configure.addEventListener("click", () => {
      const open = service.manifest?.capabilities?.workspace
        ? configureWorkspace(service)
        : service.manifest?.capabilities?.scenarioEditor
            ? configureTrackEditor(service)
            : configureService(service);
      open.catch((error) => {
        status.textContent = `Unable to configure ${overlayDisplayName(service)}: ${error.message}`;
      });
    });
    label.append(checkbox, ` ${overlayDisplayName(service)}`);
    row.append(label, opacity, configure);
    dataSourceList.append(row);
  }
  renderWorkspaceLaunchers();
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
      renderRegisteredButtonSymbols();
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
    configure.dataset.symbol = "gear";
    configure.dataset.symbolFallback = "⚙";
    configure.textContent = "⚙";
    configure.title = `Review Symbols: ${service.id}`;
    configure.setAttribute("aria-label", configure.title);
    renderButtonSymbol(configure);
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

workspaceClose.addEventListener("click", () => workspaceDialog.close());
workspaceDialog.addEventListener("close", stopWorkspaceSimulationPolling);
for (const [handle, side] of [
  [workspaceNavigatorResize, "navigator"],
  [workspaceInspectorResize, "inspector"],
]) {
  handle.addEventListener("pointerdown", (event) => {
    workspaceResize = { pointerId: event.pointerId, side };
    handle.classList.add("is-resizing");
    handle.setPointerCapture(event.pointerId);
    event.preventDefault();
  });
}
window.addEventListener("pointermove", (event) => {
  if (!workspaceResize || workspaceResize.pointerId !== event.pointerId) return;
  const bounds = workspaceContent.getBoundingClientRect();
  if (workspaceResize.side === "navigator") {
    const width = Math.max(144, Math.min(420, event.clientX - bounds.left));
    workspaceContent.style.setProperty("--workspace-navigator-width", `${width}px`);
  } else {
    const width = Math.max(176, Math.min(480, bounds.right - event.clientX));
    workspaceContent.style.setProperty("--workspace-inspector-width", `${width}px`);
  }
});
window.addEventListener("pointerup", (event) => {
  if (!workspaceResize || workspaceResize.pointerId !== event.pointerId) return;
  workspaceNavigatorResize.classList.remove("is-resizing");
  workspaceInspectorResize.classList.remove("is-resizing");
  workspaceResize = null;
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

trackRouteSelect.addEventListener("change", () => renderTrackEditor());
trackNewRoute.addEventListener("click", () => {
  trackRouteDrawing = { points: [], preview: null };
  trackEditorDialog.close();
  status.textContent = "Click route waypoints. Press Enter to finish the route, or Escape to cancel.";
  refreshMap();
});
trackSimulationToggle.addEventListener("click", async () => {
  try {
    const { service, simulationCapability, simulation } = trackEditorSession;
    const response = await fetch(`${service.url}${simulationCapability.writePath}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", [simulationCapability.revisionHeader]: simulation.revision },
      body: JSON.stringify({ running: !simulation.running }),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `simulation update returned ${response.status}`);
    }
    trackEditorSession.simulation = await response.json();
    renderTrackEditor();
    await refreshShapes();
  } catch (error) {
    trackEditorStatus.textContent = `Unable to update simulation: ${error.message}`;
  }
});
trackSimulationReset.addEventListener("click", async () => {
  try {
    const { service, simulationCapability } = trackEditorSession;
    const response = await fetch(`${service.url}${simulationCapability.resetPath}`, { method: "POST" });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `simulation reset returned ${response.status}`);
    }
    trackEditorSession.simulation = await response.json();
    trackEditorStatus.textContent = "Simulation reset to each aircraft's initial state.";
    renderTrackEditor();
    await refreshShapes();
  } catch (error) {
    trackEditorStatus.textContent = `Unable to reset simulation: ${error.message}`;
  }
});
trackDeleteRoute.addEventListener("click", () => {
  const route = selectedTrackRoute();
  if (!route) return;
  if (trackEditorSession.scenario.aircraft.some((aircraft) => aircraft.routeId === route.id)) {
    trackEditorStatus.textContent = "Reassign every aircraft before deleting this route.";
    return;
  }
  trackEditorSession.scenario.routes = trackEditorSession.scenario.routes.filter((candidate) => candidate.id !== route.id);
  trackRouteSelect.value = "";
  renderTrackEditor();
});
trackCancel.addEventListener("click", () => {
  trackRouteDrawing = null;
  trackWaypointMove = null;
  trackEditorDialog.close();
  refreshMap();
});
trackEditorForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    applyTrackRouteForm();
    await persistTrackScenario();
    trackEditorStatus.textContent = "Scenario saved.";
    renderTrackEditor();
  } catch (error) {
    trackEditorStatus.textContent = `Unable to save scenario: ${error.message}`;
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

function esaDemTileKey({ zoom, x, y }) {
  return `${esaDemServiceUrl}/raster/${zoom}/${x}/${y}`;
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

function clearEsaDemTileCache() {
  esaDemTileCache.forEach((bitmap) => bitmap.close());
  esaDemTileCache.clear();
  requestedEsaDemTileKeys.clear();
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

async function refreshEsaDemRevision() {
  if (!mapServices.has("esa-dem") || !esaDemLayer?.checked) {
    return;
  }
  const response = await fetch(`${esaDemServiceUrl}/api/raster/revision`);
  if (!response.ok) {
    throw new Error(`ESA Download Terrain service returned ${response.status} for raster revision`);
  }
  const { revision } = await response.json();
  if (esaDemRevision !== null && esaDemRevision !== revision) {
    clearEsaDemTileCache();
    requestVisibleTiles();
  }
  esaDemRevision = revision;
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

function mapScreenPointFromEvent(event) {
  const rectangle = canvas.getBoundingClientRect();
  return { x: event.clientX - rectangle.left, y: event.clientY - rectangle.top };
}

function selectableFeatureContextMenuPlugins(point) {
  return selectableDataFeatures().flatMap(({ service, feature }) => {
    if (!featureContainsScreenPoint(feature, point)) return [];
    const actions = declaredFeatureActions(service, feature);
    if (actions.length === 0) return [];
    return [{
      label: `${service.displayName}: ${selectionLabel(feature)}`,
      actions: actions.map((action) => ({
        label: action.label,
        run: () => {
          closeContextMenu();
          runSelectedFeatureAction(service, feature, action).catch((error) => {
            status.textContent = `Unable to run ${action.label}: ${error.message}`;
          });
        },
      })),
    }];
  });
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
  const screenPoint = mapScreenPointFromEvent(event);
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
    ...selectableFeatureContextMenuPlugins(screenPoint),
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
    ? svg
      .replace("<svg ", `<svg color="${color}" `)
      .replace(/\s(stroke|fill)="(?!none")[^"]*"/gi, ` $1="${color}"`)
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

function renderButtonSymbol(button) {
  const fallback = button.dataset.symbolFallback;
  const definition = symbolDefinitions.get(button.dataset.symbol);
  const label = button.querySelector("[data-symbol-label]")?.cloneNode(true);
  if (!definition) {
    button.textContent = fallback;
    if (label) button.append(label);
    return;
  }
  const color = button.isConnected
    ? getComputedStyle(button).color
    : button.dataset.symbolColor || "#9bdff6";
  const image = loadSymbolImage(
    definition.svg,
    definition.colorMode === "colorable" ? color : null,
  );
  const icon = document.createElement("img");
  icon.src = image.src;
  icon.alt = "";
  button.replaceChildren(icon);
  if (label) button.append(label);
}

function renderRegisteredButtonSymbols() {
  document.querySelectorAll("button[data-symbol]").forEach(renderButtonSymbol);
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
  renderRegisteredButtonSymbols();
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

function missingEsaDemTiles(zoom) {
  return visibleTiles(zoom).filter((tile) => {
    const key = esaDemTileKey(tile);
    if (requestedEsaDemTileKeys.has(key)) return false;
    requestedEsaDemTileKeys.add(key);
    return true;
  });
}

function requestVisibleTiles() {
  if (!configurationLoaded || canvas.clientWidth === 0 || canvas.clientHeight === 0) {
    return;
  }
  if (tileRequestFrame !== null) {
    return;
  }
  tileRequestFrame = requestAnimationFrame(() => {
    tileRequestFrame = null;
    const baseZoom = vectorTileZoom();
    const baseTiles = visibleTiles(baseZoom);
    const tiles = missingTiles(baseZoom);
    const detailZoom = Math.min(18, Math.ceil(camera.zoom));
    if (mapServices.has("vecmap") && vectorLayer?.checked && tiles.length > 0) {
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
    if (mapServices.has("satmap") && imageryLayer?.checked) {
      let rasterTiles = missingRasterTiles(baseZoom);
      const rasterBaseTilesReady = baseTiles.every((tile) => rasterTileCache.has(rasterTileKey(tile)));
      if (rasterBaseTilesReady && updatesPerSecond >= 50 && detailZoom > baseZoom) {
        rasterTiles = rasterTiles.concat(missingRasterTiles(detailZoom));
      }
      if (rasterTiles.length > 0) {
        tileWorker.postMessage({ type: "request-raster-tiles", serviceUrl: satelliteMapServiceUrl, tiles: rasterTiles });
      }
    }
    if (mapServices.has("copernicus") && copernicusLayer?.checked) {
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
    if (mapServices.has("terrain") && terrainLayer?.checked) {
      let terrainTiles = missingTerrainTiles(baseZoom);
      const terrainBaseTilesReady = baseTiles.every((tile) => terrainTileCache.has(terrainTileKey(tile)));
      if (terrainBaseTilesReady && updatesPerSecond >= 50 && detailZoom > baseZoom) {
        terrainTiles = terrainTiles.concat(missingTerrainTiles(detailZoom));
      }
      if (terrainTiles.length > 0) {
        tileWorker.postMessage({ type: "request-raster-tiles", serviceUrl: terrainServiceUrl, tiles: terrainTiles });
      }
    }
    if (mapServices.has("esa-dem") && esaDemLayer?.checked) {
      let demTiles = missingEsaDemTiles(baseZoom);
      const baseTilesReady = baseTiles.every((tile) => esaDemTileCache.has(esaDemTileKey(tile)));
      if (baseTilesReady && updatesPerSecond >= 50 && detailZoom > baseZoom) {
        demTiles = demTiles.concat(missingEsaDemTiles(detailZoom));
      }
      if (demTiles.length > 0) {
        tileWorker.postMessage({ type: "request-raster-tiles", serviceUrl: esaDemServiceUrl, tiles: demTiles });
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
  centerOnSelectedFeature();
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
  configuredMapServices = configuration.services.filter((service) => ["vecmap", "satmap", "copernicus", "terrain", "esa-dem"].includes(service.type));
  const runningServices = await registeredRunningServices(configuration.services);
  const vectorService = runningServices.find((item) => item.type === "vecmap");
  const satelliteService = runningServices.find((item) => item.type === "satmap");
  const copernicusService = runningServices.find((item) => item.type === "copernicus");
  const terrainService = runningServices.find((item) => item.type === "terrain");
  const esaDemService = runningServices.find((item) => item.type === "esa-dem");
  mapServices.clear();
  [vectorService, satelliteService, copernicusService, terrainService, esaDemService].filter(Boolean).forEach((service) => {
    mapServices.set(service.type, service);
  });
  symbolServices = runningServices
    .filter((item) => item.type === "symbols")
    .map((item) => ({ ...item, enabled: true }));
  overlayServices = runningServices
    .filter((item) => ["shapes", "geojson", "adsb", "ais", "airlanes"].includes(item.type))
    .map((item) => ({
      ...item,
      endpoint: item.manifest?.dataEndpoint?.path ?? "/api/geojson",
      enabled: true,
    }));
  workspaceServices = runningServices
    .filter((item) => item.type === "workspace")
    .filter((item) => item.manifest?.capabilities?.workspace);
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
    if (esaDemService) {
      const nextEsaDemServiceUrl = esaDemService.url.replace(/\/$/, "");
      if (esaDemServiceUrl !== nextEsaDemServiceUrl) {
        clearEsaDemTileCache();
        esaDemRevision = null;
      }
      esaDemServiceUrl = nextEsaDemServiceUrl;
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
    displayMiniTimeControl = mapView.layers?.displayMiniTimeControl ?? false;
    layerOpacity.satellite = mapView.layers?.satelliteOpacity ?? 10;
    layerOpacity.copernicus = mapView.layers?.copernicusOpacity ?? 10;
    layerOpacity.terrain = mapView.layers?.terrainOpacity ?? 8;
    layerOpacity.esaDem = mapView.layers?.esaDemOpacity ?? 8;
    layerOpacity.vector = mapView.layers?.vectorOpacity ?? 10;
    layerOpacity.grid = mapView.layers?.gridOpacity ?? 10;
  }
  configurationLoaded = true;
  startMiniTimeControlUpdates();
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
  if (esaDemLayer?.checked) {
    await refreshEsaDemRevision();
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
          satelliteImagery: imageryLayer?.checked ?? false,
          copernicus: copernicusLayer?.checked ?? false,
          terrain: terrainLayer?.checked ?? false,
          esaDem: esaDemLayer?.checked ?? false,
          dataSources: overlayLayerSelections(),
          dataSourceOpacity: overlaySourceOpacities(),
          vectorMap: vectorLayer?.checked ?? false,
          grid: gridLayer.checked,
          gridCrosshair,
          displayMiniTimeControl,
          satelliteOpacity: layerOpacity.satellite,
          copernicusOpacity: layerOpacity.copernicus,
          terrainOpacity: layerOpacity.terrain,
          esaDemOpacity: layerOpacity.esaDem,
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
      satelliteImagery: imageryLayer?.checked ?? false,
      copernicus: copernicusLayer?.checked ?? false,
      terrain: terrainLayer?.checked ?? false,
      esaDem: esaDemLayer?.checked ?? false,
      dataSources: overlayLayerSelections(),
      dataSourceOpacity: overlaySourceOpacities(),
      vectorMap: vectorLayer?.checked ?? false,
      grid: gridLayer.checked,
      gridCrosshair,
      displayMiniTimeControl,
      satelliteOpacity: layerOpacity.satellite,
      copernicusOpacity: layerOpacity.copernicus,
      terrainOpacity: layerOpacity.terrain,
      esaDemOpacity: layerOpacity.esaDem,
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
    if (key.startsWith(`${esaDemServiceUrl}/`)) {
      esaDemTileCache.set(key, bitmap);
    } else if (key.startsWith(`${terrainServiceUrl}/`)) {
      terrainTileCache.set(key, bitmap);
    } else if (key.startsWith(`${copernicusServiceUrl}/`)) {
      copernicusTileCache.set(key, bitmap);
    } else {
      rasterTileCache.set(key, bitmap);
    }
    requestVisibleTiles();
  } else if (type === "raster-missing") {
    if (key.startsWith(`${esaDemServiceUrl}/`)) {
      window.setTimeout(() => {
        requestedEsaDemTileKeys.delete(key);
        requestVisibleTiles();
      }, 5000);
    } else if (key.startsWith(`${terrainServiceUrl}/`)) {
      window.setTimeout(() => {
        requestedTerrainTileKeys.delete(key);
        requestVisibleTiles();
      }, 5000);
    } else if (key.startsWith(`${copernicusServiceUrl}/`)) {
      window.setTimeout(() => {
        requestedCopernicusTileKeys.delete(key);
        requestVisibleTiles();
      }, 5000);
    } else {
      window.setTimeout(() => {
        requestedRasterTileKeys.delete(key);
        requestVisibleTiles();
      }, 5000);
    }
  } else if (type === "raster-error") {
    if (key.startsWith(`${esaDemServiceUrl}/`)) {
      requestedEsaDemTileKeys.delete(key);
      status.textContent = `Unable to load ESA Download Terrain tile: ${message}`;
    } else if (key.startsWith(`${terrainServiceUrl}/`)) {
      requestedTerrainTileKeys.delete(key);
      status.textContent = `Unable to load terrain tile: ${message}`;
    } else if (key.startsWith(`${copernicusServiceUrl}/`)) {
      requestedCopernicusTileKeys.delete(key);
      status.textContent = `Unable to load Copernicus tile: ${message}`;
    } else {
      requestedRasterTileKeys.delete(key);
      status.textContent = `Unable to load satellite tile: ${message}`;
    }
    window.setTimeout(requestVisibleTiles, 5000);
  }
});
serviceConfigDialog.addEventListener("close", stopServiceActivityPolling);

function clearInterruptedMapGesture(pointerId) {
  if (trackWaypointMove?.pointerId === pointerId) {
    trackWaypointMove = null;
  }
  if (shapeMapMove?.pointerId === pointerId) {
    shapeMapMove = null;
  }
  if (selectionPointer?.pointerId === pointerId) {
    selectionPointer = null;
  }
  if (lastPointer?.pointerId === pointerId) {
    dragging = false;
    lastPointer = null;
  }
}

canvas.addEventListener("pointerdown", (event) => {
  if (event.button !== 0) {
    return;
  }
  clearInterruptedMapGesture(event.pointerId);
  closeContextMenu();
  if (terrainProfileDrawing) {
    const point = mapPosition(event);
    if (!terrainProfileDrawing.start) {
      terrainProfileDrawing.start = point;
      terrainProfileDrawing.preview = null;
      status.textContent = "Choose the terrain-profile end point on the map.";
      refreshMap();
    } else if (!terrainProfileDrawing.submitting) {
      finishTerrainProfileDrawing(point).catch((error) => {
        status.textContent = `Unable to create terrain profile: ${error.message}`;
      });
    }
    return;
  }
  if (trackRouteDrawing) {
    trackRouteDrawing.points.push(mapPosition(event));
    trackRouteDrawing.preview = null;
    refreshMap();
    return;
  }
  if (trackWaypointMove) {
    trackWaypointMove.pointerId = event.pointerId;
    trackWaypointMove.preview = mapPosition(event);
    canvas.setPointerCapture(event.pointerId);
    refreshMap();
    return;
  }
  if (trackRouteEditing) {
    const waypoint = waypointAtMapEvent(event);
    if (waypoint) {
      trackWaypointMove = {
        routeId: trackRouteEditing.routeId,
        legId: waypoint.id,
        preview: [...waypoint.waypoint],
        pointerId: event.pointerId,
      };
      canvas.setPointerCapture(event.pointerId);
      refreshMap();
      return;
    }
  }
  if (shapeMapEditing) {
    const control = shapeControlPointAtMapEvent(event);
    if (control) {
      shapeMapMove = { controlId: control.id, pointerId: event.pointerId };
      canvas.setPointerCapture(event.pointerId);
      return;
    }
    return;
  }
  if (shapeDrawing) {
    const point = mapPosition(event);
    if (shapeDrawing.type === "polygon") {
      shapeDrawing.points.push(point);
      return;
    }
    shapeDrawing.start = point;
    canvas.setPointerCapture(event.pointerId);
    return;
  }
  if (event.shiftKey) {
    const bounds = canvas.getBoundingClientRect();
    selectionPointer = {
      pointerId: event.pointerId,
      start: { x: event.clientX - bounds.left, y: event.clientY - bounds.top },
      current: { x: event.clientX - bounds.left, y: event.clientY - bounds.top },
      isDragging: false,
    };
    canvas.setPointerCapture(event.pointerId);
    return;
  }
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
  if (terrainProfileDrawing?.start) {
    terrainProfileDrawing.preview = mapPosition(event);
    refreshMap();
    return;
  }
  if (trackRouteDrawing) {
    trackRouteDrawing.preview = mapPosition(event);
    refreshMap();
    return;
  }
  if (trackWaypointMove?.pointerId === event.pointerId) {
    trackWaypointMove.preview = mapPosition(event);
    refreshMap();
    return;
  }
  if (shapeMapMove?.pointerId === event.pointerId) {
    updateShapeMapControl(shapeMapMove.controlId, mapPosition(event));
    refreshMap();
    return;
  }
  if (selectionPointer?.pointerId === event.pointerId) {
    const bounds = canvas.getBoundingClientRect();
    selectionPointer.current = {
      x: event.clientX - bounds.left,
      y: event.clientY - bounds.top,
    };
    selectionPointer.isDragging = selectionPointer.isDragging
      || Math.hypot(
        selectionPointer.current.x - selectionPointer.start.x,
        selectionPointer.current.y - selectionPointer.start.y,
      ) > 4;
    return;
  }
  if (!dragging) {
    return;
  }
  if (centeredSelectionKey !== null) {
    centeredSelectionKey = null;
    renderSelectedFeatures();
  }
  camera.x = Math.max(0, Math.min(1, camera.x - (event.clientX - lastPointer.clientX) / scale()));
  camera.y = Math.max(0, Math.min(1, camera.y - (event.clientY - lastPointer.clientY) / scale()));
  lastPointer = event;
  mapStateVersion += 1;
  refreshMap();
  invalidateAirLanesCache();
  scheduleViewportPublish();
});

canvas.addEventListener("pointerup", async (event) => {
  if (trackWaypointMove?.pointerId === event.pointerId) {
    const move = trackWaypointMove;
    const route = trackEditorSession.scenario.routes.find((item) => item.id === move.routeId);
    const leg = route?.legs.find((item) => item.id === move.legId);
    if (!leg) {
      trackWaypointMove = null;
      status.textContent = "Waypoint no longer exists.";
      return;
    }
    const previousWaypoint = [...leg.waypoint];
    leg.waypoint = move.preview;
    try {
      await persistTrackScenario();
      status.textContent = trackRouteEditing
        ? `Moved ${leg.id}. Drag another cyan waypoint or press Escape to return to the route editor.`
        : `Moved ${leg.id}.`;
      if (!trackRouteEditing) {
        renderTrackEditor();
        trackEditorDialog.showModal();
      }
    } catch (error) {
      leg.waypoint = previousWaypoint;
      status.textContent = `Unable to move waypoint: ${error.message}`;
    } finally {
      trackWaypointMove = null;
      refreshMap();
    }
    return;
  }
  if (shapeMapMove?.pointerId === event.pointerId) {
    shapeMapMove = null;
    status.textContent = "Draft updated. Drag another cyan control or press Escape to return to the Shapes inspector.";
    refreshMap();
    return;
  }
  if (shapeDrawing?.start) {
    finishShapeDrawing(mapPosition(event));
    return;
  }
  if (selectionPointer?.pointerId === event.pointerId) {
    if (selectionPointer.isDragging) {
      toggleSelectableFeaturesInRectangle(selectionPointer.start, selectionPointer.current);
    } else {
      toggleSelectableFeaturesAt(selectionPointer.start);
    }
    selectionPointer = null;
    return;
  }
  dragging = false;
  lastPointer = null;
  invalidateAirLanesCache();
});

canvas.addEventListener("pointercancel", (event) => {
  clearInterruptedMapGesture(event.pointerId);
  refreshMap();
});

canvas.addEventListener("lostpointercapture", (event) => {
  clearInterruptedMapGesture(event.pointerId);
});

canvas.addEventListener("dblclick", (event) => {
  if (shapeDrawing?.type !== "polygon" || shapeDrawing.points.length < 3) {
    return;
  }
  event.preventDefault();
  const draft = workspaceSession?.shapeEditing?.draft;
  if (!draft) return;
  draft.coordinates = shapeDrawing.points;
  shapeDrawing = null;
  renderWorkspace();
  workspaceDialog.showModal();
  status.textContent = "Geometry captured. Review the inspector and save the shape.";
});

window.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && shapeMapEditing) {
    const editing = shapeMapEditing;
    shapeMapMove = null;
    if (editing.mapEditOrigin === "selection") {
      shapeMapEditing = null;
      status.textContent = "Saving selected shape changes…";
      persistShapeDraft(editing).then(() => {
        workspaceSession.shapeEditing = null;
        status.textContent = "Saved selected shape changes.";
        refreshMap();
      }).catch((error) => {
        shapeMapEditing = editing;
        status.textContent = `Unable to save selected shape changes: ${error.message}`;
        refreshMap();
      });
    } else {
      shapeMapEditing = null;
      renderWorkspace();
      workspaceDialog.showModal();
      status.textContent = "Finished editing the Shapes draft on the map.";
      refreshMap();
    }
    return;
  }
  if (event.key === "Escape" && shapeDrawing) {
    shapeDrawing = null;
    renderWorkspace();
    workspaceDialog.showModal();
    status.textContent = "Shape drawing cancelled.";
    refreshMap();
    return;
  }
  if (event.key === "Escape" && terrainProfileDrawing) {
    terrainProfileDrawing = null;
    renderWorkspace();
    workspaceDialog.showModal();
    status.textContent = "Terrain profile selection cancelled.";
    refreshMap();
    return;
  }
  if (event.key === "Escape" && (trackRouteDrawing || trackWaypointMove || trackRouteEditing)) {
    const returnToWorkspace = trackRouteEditing !== null;
    trackRouteDrawing = null;
    trackWaypointMove = null;
    trackRouteEditing = null;
    if (returnToWorkspace && workspaceSession) {
      workspaceSession.activePane = "routes";
      workspaceSession.activeItem = null;
      workspaceSession.editing = null;
      renderWorkspace();
      workspaceDialog.showModal();
      startWorkspaceSimulationPolling();
      status.textContent = "Finished map route editing.";
    } else {
      status.textContent = "Track editing cancelled.";
    }
    refreshMap();
    return;
  }
  if (event.key === "Enter" && trackRouteDrawing) {
    event.preventDefault();
    finishTrackRouteDrawing();
  }
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
miniTimePlay.addEventListener("click", () => {
  const action = simTimeControlState?.status === "paused" ? "resume" : "start";
  sendMiniTimeControlAction(action).catch((error) => {
    status.textContent = `Unable to ${action} simulation time: ${error.message}`;
  });
});
miniTimePause.addEventListener("click", () => {
  sendMiniTimeControlAction("pause").catch((error) => {
    status.textContent = `Unable to pause simulation time: ${error.message}`;
  });
});
miniTimeReset.addEventListener("click", () => {
  sendMiniTimeControlAction("reset").catch((error) => {
    status.textContent = `Unable to reset simulation time: ${error.message}`;
  });
});
layersTab.addEventListener("click", () => showDisplayPanel("layers"));
selectionTab.addEventListener("click", () => showDisplayPanel("selection"));
renderSelectedFeatures();
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
  refreshEsaDemRevision().catch((error) => {
    status.textContent = `Unable to refresh ESA Download Terrain: ${error.message}`;
  });
}, 5000);
window.setInterval(() => {
  reconcileRegisteredServices().catch((error) => {
    status.textContent = `Unable to reconcile registered services: ${error.message}`;
  });
}, 2000);
window.setInterval(requestVisibleTiles, 1000);
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
    sidePanel.classList.add("is-ready");
    resizeCanvas();
    refreshMap();
  });
