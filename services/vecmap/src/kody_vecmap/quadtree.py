"""A sparse quadtree spatial index for GeoJSON features."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from shapely.geometry.base import BaseGeometry

from .math import Bounds


@dataclass(frozen=True)
class IndexedFeature:
    properties: dict[str, Any]
    geometry: BaseGeometry


@dataclass
class QuadTreeNode:
    bounds: Bounds
    depth: int
    entries: list[IndexedFeature] = field(default_factory=list)
    children: list["QuadTreeNode"] | None = None

    def intersects(self, bounds: Bounds) -> bool:
        return not (
            bounds.east < self.bounds.west
            or bounds.west > self.bounds.east
            or bounds.north < self.bounds.south
            or bounds.south > self.bounds.north
        )

    def contains(self, bounds: Bounds) -> bool:
        return (
            self.bounds.west <= bounds.west
            and self.bounds.south <= bounds.south
            and self.bounds.east >= bounds.east
            and self.bounds.north >= bounds.north
        )

    def split(self) -> None:
        midpoint_lon = (self.bounds.west + self.bounds.east) / 2
        midpoint_lat = (self.bounds.south + self.bounds.north) / 2
        west, south, east, north = self.bounds.west, self.bounds.south, self.bounds.east, self.bounds.north
        self.children = [
            QuadTreeNode(Bounds(west, midpoint_lat, midpoint_lon, north), self.depth + 1),
            QuadTreeNode(Bounds(midpoint_lon, midpoint_lat, east, north), self.depth + 1),
            QuadTreeNode(Bounds(west, south, midpoint_lon, midpoint_lat), self.depth + 1),
            QuadTreeNode(Bounds(midpoint_lon, south, east, midpoint_lat), self.depth + 1),
        ]


class QuadTreeMap:
    """Stores large features at their smallest fully containing quadtree node."""

    def __init__(self, max_depth: int = 8, node_capacity: int = 12) -> None:
        self.max_depth = max_depth
        self.node_capacity = node_capacity
        self.root = QuadTreeNode(Bounds(-180, -85.05112878, 180, 85.05112878), 0)

    def insert(self, feature: IndexedFeature) -> None:
        self._insert(self.root, feature)

    def _insert(self, node: QuadTreeNode, feature: IndexedFeature) -> None:
        feature_bounds = Bounds(*feature.geometry.bounds)
        if node.children:
            child = next((item for item in node.children if item.contains(feature_bounds)), None)
            if child:
                self._insert(child, feature)
                return

        node.entries.append(feature)
        if len(node.entries) > self.node_capacity and node.depth < self.max_depth and not node.children:
            node.split()
            existing_entries, node.entries = node.entries, []
            for entry in existing_entries:
                self._insert(node, entry)

    def query(self, bounds: Bounds) -> Iterable[IndexedFeature]:
        yield from self._query(self.root, bounds)

    def _query(self, node: QuadTreeNode, bounds: Bounds) -> Iterable[IndexedFeature]:
        if not node.intersects(bounds):
            return
        for entry in node.entries:
            entry_bounds = Bounds(*entry.geometry.bounds)
            if not (
                entry_bounds.east < bounds.west
                or entry_bounds.west > bounds.east
                or entry_bounds.north < bounds.south
                or entry_bounds.south > bounds.north
            ):
                yield entry
        if node.children:
            for child in node.children:
                yield from self._query(child, bounds)
