# Terrain Analysis Workspace

Terrain Analysis is the second reference workspace proposed in the
[common workspace design](../README.md). It intentionally has no simulation
clock, aircraft catalogue, or route model. Its purpose is to prove that the
workspace platform supports operational analysis as well as simulation.

## Intended operator tasks

```text
Terrain Analysis
├─ Overview
├─ Elevation sources
├─ Line of sight
├─ Elevation profiles
└─ Saved analyses
```

The workspace will let an operator:

1. inspect available local elevation sources and coverage;
2. see source resolution, priority, and provenance;
3. select an observer point and create a line-of-sight rosette;
4. draw a start-to-end profile line on the map;
5. inspect elevation, ascent, descent, gaps, and source provenance in a
   profile chart;
6. retain or clear named analysis results.

## Current Terrain capabilities

The existing Terrain service already provides:

- locally imported elevation packages and raster rendering;
- point elevation lookup with the contributing package identifier;
- configurable LOS bearing count, range, sample distance, and observer
  height;
- an asynchronous LOS rosette action with progress and GeoJSON output.

There is not yet a workspace capability, multi-source resource, saved
analysis model, elevation-profile endpoint, or profile chart. Those are
deliberate next steps, not implemented features.

## Core versus Terrain responsibilities

The core will provide trusted `choosePoint` and `chooseLine` map modes,
temporary geometry previews, job progress presentation, standard result
overlays, and a profile-chart view.

The Terrain service will choose overlapping-source policy, sample elevation,
identify no-data gaps, calculate LOS and profile statistics, validate inputs,
persist any saved analyses, and return source provenance. The core must not
blend source layers or recreate terrain calculations in the browser.

## Initial delivery path

1. Publish a Terrain workspace definition and coherent snapshot.
2. Expose elevation-source metadata and enablement/priority policy.
3. Surface the existing LOS operation through the workspace while retaining
   the current map-context shortcut.
4. Add a typed point-to-point profile command and immutable profile results.
5. Add the core profile chart, map/chart cursor synchronization, and
   explicit no-data rendering.
