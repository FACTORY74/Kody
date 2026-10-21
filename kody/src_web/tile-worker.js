const pendingTiles = new Set();
const pendingRasterTiles = new Set();
const tileQueue = [];
const activeTileRequests = new Map();
const MAX_CONCURRENT_TILE_LOADS = 2;
let activeTileLoads = 0;
let currentTileGeneration = -1;

function tileKey(serviceUrl, revision, zoom, x, y, transparentBase = false) {
  return `${serviceUrl}/vector/${revision || "unversioned"}/${transparentBase}/${zoom}/${x}/${y}`;
}

async function loadTile(serviceUrl, revision, zoom, x, y, transparentBase, generation) {
  const key = tileKey(serviceUrl, revision, zoom, x, y, transparentBase);
  const controller = new AbortController();
  activeTileRequests.set(key, controller);
  try {
    const response = await fetch(`${serviceUrl}/api/tiles/${zoom}/${x}/${y}.webp?v=${encodeURIComponent(revision || "unversioned")}`, {
      signal: controller.signal,
    });
    if (response.status === 202) {
      const { retryAfterMilliseconds } = await response.json();
      self.postMessage({
        type: "tile-pending",
        key,
        retryAfterMilliseconds,
      });
      return;
    }
    if (!response.ok) {
      throw new Error(`Map service returned ${response.status}`);
    }
    if (generation !== currentTileGeneration) {
      self.postMessage({ type: "tile-cancelled", key });
      return;
    }
    const bitmap = await createImageBitmap(await response.blob());
    self.postMessage({
      type: "tile",
      key,
      tile: { zoom, x, y },
      source: { id: "natural-earth", attribution: "Natural Earth" },
      bitmap,
    }, [bitmap]);
  } catch (error) {
    if (controller.signal.aborted || generation !== currentTileGeneration) {
      self.postMessage({ type: "tile-cancelled", key });
    } else {
      self.postMessage({ type: "error", key, message: error.message });
    }
  } finally {
    activeTileRequests.delete(key);
    pendingTiles.delete(key);
  }
}

function loadQueuedTiles() {
  while (activeTileLoads < MAX_CONCURRENT_TILE_LOADS && tileQueue.length > 0) {
    const tile = tileQueue.shift();
    activeTileLoads += 1;
    loadTile(
      tile.serviceUrl,
      tile.revision,
      tile.zoom,
      tile.x,
      tile.y,
      tile.transparentBase,
      tile.generation,
    ).finally(() => {
      activeTileLoads -= 1;
      loadQueuedTiles();
    });
  }
}

function queueTiles(serviceUrl, revision, tiles, transparentBase, generation) {
  if (generation !== currentTileGeneration) {
    currentTileGeneration = generation;
    tileQueue.forEach((tile) => {
      self.postMessage({
        type: "tile-cancelled",
        key: tileKey(tile.serviceUrl, tile.revision, tile.zoom, tile.x, tile.y, tile.transparentBase),
      });
    });
    tileQueue.length = 0;
    activeTileRequests.forEach((controller) => controller.abort());
  }
  tiles.forEach(({ zoom, x, y }) => {
    const key = tileKey(serviceUrl, revision, zoom, x, y, transparentBase);
    if (!pendingTiles.has(key)) {
      pendingTiles.add(key);
      tileQueue.push({ serviceUrl, revision, zoom, x, y, transparentBase, generation });
    }
  });
  loadQueuedTiles();
}

async function loadRasterTile(serviceUrl, zoom, x, y) {
  const key = `${serviceUrl}/raster/${zoom}/${x}/${y}`;
  if (pendingRasterTiles.has(key)) {
    return;
  }
  pendingRasterTiles.add(key);
  try {
    const response = await fetch(`${serviceUrl}/api/raster/${zoom}/${x}/${y}`);
    if (response.status === 404) {
      self.postMessage({ type: "raster-missing", key });
      return;
    }
    if (!response.ok) {
      throw new Error(`Map service returned ${response.status}`);
    }
    const bitmap = await createImageBitmap(await response.blob());
    self.postMessage({ type: "raster-tile", key, tile: { zoom, x, y }, bitmap }, [bitmap]);
  } catch (error) {
    self.postMessage({ type: "raster-error", key, message: error.message });
  } finally {
    pendingRasterTiles.delete(key);
  }
}

self.addEventListener("message", (event) => {
  const { type, serviceUrl, revision, tiles, transparentBase, generation } = event.data;
  if (type === "request-tiles") {
    queueTiles(serviceUrl, revision, tiles, transparentBase, generation);
  } else if (type === "request-raster-tiles") {
    tiles.forEach(({ zoom, x, y }) => loadRasterTile(serviceUrl, zoom, x, y));
  }
});

function toTilePoint(longitude, latitude, zoom, x, y) {
  const latitudeRadians = Math.max(-85.05112878, Math.min(85.05112878, latitude)) * Math.PI / 180;
  const scale = 2 ** zoom;
  return {
    x: (((longitude + 180) / 360) * scale - x) * 256,
    y: ((1 - Math.log(Math.tan(latitudeRadians) + 1 / Math.cos(latitudeRadians)) / Math.PI) / 2 * scale - y) * 256,
  };
}

function appendLineString(context, coordinates, zoom, x, y) {
  coordinates.forEach(([longitude, latitude], index) => {
    const point = toTilePoint(longitude, latitude, zoom, x, y);
    if (index === 0) {
      context.moveTo(point.x, point.y);
    } else {
      context.lineTo(point.x, point.y);
    }
  });
}

function drawGeometry(context, geometry, zoom, x, y) {
  if (geometry.type === "LineString") {
    appendLineString(context, geometry.coordinates, zoom, x, y);
  } else if (geometry.type === "MultiLineString") {
    geometry.coordinates.forEach((line) => appendLineString(context, line, zoom, x, y));
  } else if (geometry.type === "Polygon") {
    geometry.coordinates.forEach((ring) => {
      appendLineString(context, ring, zoom, x, y);
      context.closePath();
    });
  } else if (geometry.type === "MultiPolygon") {
    geometry.coordinates.forEach((polygon) => {
      polygon.forEach((ring) => {
        appendLineString(context, ring, zoom, x, y);
        context.closePath();
      });
    });
  } else if (geometry.type === "GeometryCollection") {
    geometry.geometries.forEach((item) => drawGeometry(context, item, zoom, x, y));
  }
}

function renderTile(collection, zoom, x, y, transparentBase) {
  const canvas = new OffscreenCanvas(512, 512);
  const context = canvas.getContext("2d");
  context.scale(2, 2);
  const styles = collection.styles || {};
  const features = collection.features.filter((feature) => styles[feature.properties.layer])
    .sort((left, right) => (
      styles[left.properties.layer].zIndex - styles[right.properties.layer].zIndex
    ));
  features.forEach((feature) => {
    const style = styles[feature.properties.layer];
    if (feature.geometry.type === "Point") {
      const point = toTilePoint(...feature.geometry.coordinates, zoom, x, y);
      if (style.label && feature.properties.name) {
        context.font = style.font;
        context.textAlign = style.textAlign;
        context.textBaseline = style.textBaseline;
        context.lineWidth = style.labelStrokeWidth;
        context.strokeStyle = style.labelStroke;
        context.fillStyle = style.labelFill;
        context.strokeText(feature.properties.name, point.x + style.labelOffsetX, point.y + style.labelOffsetY);
        context.fillText(feature.properties.name, point.x + style.labelOffsetX, point.y + style.labelOffsetY);
      }
      if (style.pointRadius) {
        context.fillStyle = style.fill;
        context.beginPath();
        context.arc(point.x, point.y, style.pointRadius, 0, Math.PI * 2);
        context.fill();
      }
      return;
    }
    context.beginPath();
    drawGeometry(context, feature.geometry, zoom, x, y);
    if (style.fill && !(transparentBase && feature.properties.layer === "osm-land")) {
      context.fillStyle = style.fill;
      context.fill("evenodd");
    }
    if (style.stroke) {
      context.strokeStyle = style.stroke;
      context.lineWidth = style.lineWidth;
      context.setLineDash(style.lineDash || []);
      context.stroke();
      context.setLineDash([]);
    }
  });
  return canvas.transferToImageBitmap();
}
