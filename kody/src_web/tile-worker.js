const pendingTiles = new Set();
const pendingRasterTiles = new Set();
const tileQueue = [];
const activeTileRequests = new Map();
const MAX_CONCURRENT_TILE_LOADS = 4;
let activeTileLoads = 0;
let currentTileGeneration = -1;

function tileKey(serviceUrl, zoom, x, y, transparentBase = false) {
  return `${serviceUrl}/vector/${transparentBase}/${zoom}/${x}/${y}`;
}

async function loadTile(serviceUrl, zoom, x, y, transparentBase, generation) {
  const key = tileKey(serviceUrl, zoom, x, y, transparentBase);
  const controller = new AbortController();
  activeTileRequests.set(key, controller);
  try {
    const response = await fetch(`${serviceUrl}/api/patch/${zoom}/${x}/${y}?buffer_pixels=2`, {
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`Map service returned ${response.status}`);
    }
    const collection = await response.json();
    if (generation !== currentTileGeneration) {
      self.postMessage({ type: "tile-cancelled", key });
      return;
    }
    const bitmap = renderTile(collection, zoom, x, y, transparentBase);
    self.postMessage({ type: "tile", key, tile: { zoom, x, y }, source: collection.source, bitmap }, [bitmap]);
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

function queueTiles(serviceUrl, tiles, transparentBase, generation) {
  if (generation !== currentTileGeneration) {
    currentTileGeneration = generation;
    tileQueue.forEach((tile) => {
      self.postMessage({
        type: "tile-cancelled",
        key: tileKey(tile.serviceUrl, tile.zoom, tile.x, tile.y, tile.transparentBase),
      });
    });
    tileQueue.length = 0;
    activeTileRequests.forEach((controller) => controller.abort());
  }
  tiles.forEach(({ zoom, x, y }) => {
    const key = tileKey(serviceUrl, zoom, x, y, transparentBase);
    if (!pendingTiles.has(key)) {
      pendingTiles.add(key);
      tileQueue.push({ serviceUrl, zoom, x, y, transparentBase, generation });
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
  const { type, serviceUrl, tiles, transparentBase, generation } = event.data;
  if (type === "request-tiles") {
    queueTiles(serviceUrl, tiles, transparentBase, generation);
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
  const features = collection.features.filter((feature) => {
    if (zoom >= 10) {
      return true;
    }
    if (feature.properties.layer === "osm-forest") {
      return false;
    }
    return (
      feature.properties.layer !== "osm-road"
      || ["motorway", "trunk"].includes(feature.properties.highway)
    );
  }).sort((left, right) => {
    const order = {
      "osm-land": 1,
      "osm-water": 2,
      "osm-urban": 3,
      "osm-green": 4,
      "osm-forest": 5,
      "osm-building": 6,
      "osm-road": 7,
      "osm-coastline": 8,
      "osm-place": 9,
    };
    return (order[left.properties.layer] || 0) - (order[right.properties.layer] || 0);
  });
  features.forEach((feature) => {
    context.beginPath();
    drawGeometry(context, feature.geometry, zoom, x, y);
    if (feature.properties.layer === "osm-land") {
      if (transparentBase) {
        return;
      }
      context.fillStyle = "#9ac889";
      context.fill("evenodd");
      return;
    }
    if (feature.properties.layer === "osm-water") {
      context.fillStyle = "#8eb8c5";
      context.fill("evenodd");
      return;
    }
    if (feature.properties.layer === "osm-urban") {
      context.fillStyle = "#c7b89d";
      context.fill("evenodd");
      return;
    }
    if (feature.properties.layer === "osm-green") {
      context.fillStyle = "#78a56d";
      context.fill("evenodd");
      return;
    }
    if (feature.properties.layer === "osm-building") {
      context.fillStyle = "#d9c9b3";
      context.strokeStyle = "#ad9a7d";
      context.lineWidth = 0.7;
      context.fill("evenodd");
      context.stroke();
      return;
    }
    if (feature.properties.layer === "osm-forest") {
      if (!["Polygon", "MultiPolygon"].includes(feature.geometry.type)) {
        return;
      }
      context.fillStyle = "#5e8f55";
      context.fill("evenodd");
      return;
    }
    if (feature.properties.layer === "osm-road") {
      context.strokeStyle = "#f5f1e5";
      context.lineWidth = feature.properties.highway === "motorway" ? 2.4 : 1.2;
      context.stroke();
      return;
    }
    if (feature.properties.layer === "osm-coastline") {
      context.strokeStyle = "#274c58";
      context.lineWidth = 1.4;
      context.stroke();
      return;
    }
    if (feature.properties.layer === "osm-place") {
      if (feature.geometry.type !== "Point" || !feature.properties.name) {
        return;
      }
      const point = toTilePoint(...feature.geometry.coordinates, zoom, x, y);
      context.font = feature.properties.place === "city" ? "600 13px system-ui, sans-serif" : "12px system-ui, sans-serif";
      context.textAlign = "left";
      context.textBaseline = "middle";
      context.lineWidth = 3;
      context.strokeStyle = "rgb(7 17 30 / 80%)";
      context.fillStyle = "#f7f1dd";
      context.strokeText(feature.properties.name, point.x + 5, point.y);
      context.fillText(feature.properties.name, point.x + 5, point.y);
      return;
    }
    if (feature.properties.kind === "graticule") {
      context.strokeStyle = "#5f7b8e";
      context.lineWidth = 0.8;
      context.stroke();
      return;
    }
    if (transparentBase) {
      context.strokeStyle = "#3f6c4e";
      context.lineWidth = 0.7;
      context.stroke();
      return;
    }
    context.fillStyle = "#9ac889";
    context.fill("evenodd");
  });
  return canvas.transferToImageBitmap();
}
