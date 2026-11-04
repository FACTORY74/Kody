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

"""SQLite-backed, validated WebP quadtree tile storage."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
import sqlite3
from typing import Callable, Generator

from fastapi import HTTPException
from fastapi.responses import Response
from PIL import Image

DEFAULT_NAMESPACE = "default"


def validate_tile_coordinates(zoom: int, x: int, y: int, maximum_zoom: int = 22) -> None:
    """Raise ValueError unless a coordinate is in the Web-Mercator tile range."""

    if not 0 <= zoom <= maximum_zoom or not 0 <= x < 2**zoom or not 0 <= y < 2**zoom:
        raise ValueError(f"invalid Web-Mercator tile coordinates: z={zoom}, x={x}, y={y}")


def _validate_webp(payload: bytes) -> None:
    try:
        with Image.open(BytesIO(payload)) as image:
            if image.format != "WEBP":
                raise ValueError("tile payload must be encoded as WebP")
            image.verify()
    except (OSError, ValueError) as error:
        raise ValueError("tile payload is not a valid WebP image") from error


def crop_parent_tile(
    payload: bytes,
    parent_zoom: int,
    zoom: int,
    x: int,
    y: int,
    tile_size: int = 256,
) -> Image.Image:
    """Return the requested descendant tile cropped and scaled from a parent WebP tile."""

    if parent_zoom > zoom:
        raise ValueError("parent zoom cannot exceed requested zoom")
    validate_tile_coordinates(parent_zoom, x >> (zoom - parent_zoom), y >> (zoom - parent_zoom))
    with Image.open(BytesIO(payload)) as source:
        image = source.convert("RGBA")
    try:
        scale = 2 ** (zoom - parent_zoom)
        parent_x = x >> (zoom - parent_zoom)
        parent_y = y >> (zoom - parent_zoom)
        child_x = x - parent_x * scale
        child_y = y - parent_y * scale
        width, height = image.size
        return image.crop(
            (
                child_x * width / scale,
                child_y * height / scale,
                (child_x + 1) * width / scale,
                (child_y + 1) * height / scale,
            )
        ).resize((tile_size, tile_size), Image.Resampling.NEAREST)
    finally:
        image.close()


def standard_tile_response(
    zoom: int,
    x: int,
    y: int,
    loader: Callable[[int, int, int], bytes | None],
    *,
    maximum_zoom: int,
    missing_detail: str,
) -> Response:
    """Apply the standard tile coordinate, missing-tile, and WebP HTTP contract."""

    try:
        validate_tile_coordinates(zoom, x, y, maximum_zoom)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="invalid raster tile coordinates") from error
    payload = loader(zoom, x, y)
    if payload is None:
        raise HTTPException(status_code=404, detail=missing_detail)
    return Response(content=payload, media_type="image/webp")


class WebPQuadTreeStore:
    """One service's persistent WebP tile quadtree and opaque revision metadata."""

    def __init__(
        self,
        directory: Path,
        *,
        database_name: str = "tiles.sqlite",
        namespace: str = DEFAULT_NAMESPACE,
        format_version: str | None = None,
    ) -> None:
        self.directory = directory
        self.path = directory / database_name
        self.namespace = namespace
        self.format_version = format_version
        self._initialize_database()

    def _connect(self) -> sqlite3.Connection:
        self.directory.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize_database(self) -> None:
        with self._connection() as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(tiles)").fetchall()
            }
            if columns and "namespace" not in columns:
                connection.execute("ALTER TABLE tiles RENAME TO legacy_tiles")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tiles (
                    namespace TEXT NOT NULL,
                    zoom INTEGER NOT NULL,
                    x INTEGER NOT NULL,
                    y INTEGER NOT NULL,
                    image_webp BLOB NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (namespace, zoom, x, y)
                )
                """
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            if columns and "namespace" not in columns:
                connection.execute(
                    """
                    INSERT INTO tiles (namespace, zoom, x, y, image_webp, updated_at)
                    SELECT ?, zoom, x, y, image_webp, updated_at FROM legacy_tiles
                    """,
                    (self.namespace,),
                )
                connection.execute("DROP TABLE legacy_tiles")
                for legacy_key, scoped_key in (
                    ("revision", f"revision:{self.namespace}"),
                    ("tile_format_version", f"format:{self.namespace}"),
                ):
                    row = connection.execute(
                        "SELECT value FROM metadata WHERE key = ?", (legacy_key,)
                    ).fetchone()
                    if row is not None:
                        connection.execute(
                            """
                            INSERT INTO metadata (key, value) VALUES (?, ?)
                            ON CONFLICT(key) DO UPDATE SET value = excluded.value
                            """,
                            (scoped_key, row[0]),
                        )
            if self.format_version is not None:
                key = f"format:{self.namespace}"
                row = connection.execute(
                    "SELECT value FROM metadata WHERE key = ?", (key,)
                ).fetchone()
                if row is None or row[0] != self.format_version:
                    connection.execute("DELETE FROM tiles WHERE namespace = ?", (self.namespace,))
                    connection.execute(
                        "DELETE FROM metadata WHERE key = ?", (f"revision:{self.namespace}",)
                    )
                    connection.execute(
                        """
                        INSERT INTO metadata (key, value) VALUES (?, ?)
                        ON CONFLICT(key) DO UPDATE SET value = excluded.value
                        """,
                        (key, self.format_version),
                    )

    def get(self, zoom: int, x: int, y: int) -> bytes | None:
        validate_tile_coordinates(zoom, x, y)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT image_webp FROM tiles
                WHERE namespace = ? AND zoom = ? AND x = ? AND y = ?
                """,
                (self.namespace, zoom, x, y),
            ).fetchone()
        return bytes(row[0]) if row is not None else None

    def put(self, zoom: int, x: int, y: int, image_webp: bytes) -> None:
        validate_tile_coordinates(zoom, x, y)
        _validate_webp(image_webp)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO tiles (namespace, zoom, x, y, image_webp, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(namespace, zoom, x, y) DO UPDATE SET
                    image_webp = excluded.image_webp,
                    updated_at = excluded.updated_at
                """,
                (
                    self.namespace,
                    zoom,
                    x,
                    y,
                    image_webp,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def put_image(
        self,
        zoom: int,
        x: int,
        y: int,
        image: Image.Image,
        *,
        quality: int = 90,
        lossless: bool = False,
    ) -> bytes:
        """Encode an image as WebP, persist it, and return the persisted payload."""

        output = BytesIO()
        image.save(
            output,
            format="WEBP",
            quality=quality,
            lossless=lossless,
            method=6,
        )
        payload = output.getvalue()
        self.put(zoom, x, y, payload)
        return payload

    def coordinates(self, zoom: int) -> set[tuple[int, int]]:
        validate_tile_coordinates(zoom, 0, 0)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT x, y FROM tiles WHERE namespace = ? AND zoom = ?",
                (self.namespace, zoom),
            ).fetchall()
        return {(int(x), int(y)) for x, y in rows}

    def zooms(self) -> list[int]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT DISTINCT zoom FROM tiles WHERE namespace = ?", (self.namespace,)
            ).fetchall()
        return sorted(int(row[0]) for row in rows)

    def revision(self) -> str:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key = ?", (f"revision:{self.namespace}",)
            ).fetchone()
        return str(row[0]) if row is not None else ""

    def set_revision(self, value: str | None = None) -> str:
        revision = value or datetime.now(UTC).isoformat()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO metadata (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (f"revision:{self.namespace}", revision),
            )
        return revision

    def migrate_legacy_files(self, revision_marker: str = ".revision") -> int:
        """Import legacy ``z/x/y.webp`` files, verify every write, then remove them."""

        files: list[tuple[Path, int, int, int]] = []
        for path in self.directory.glob("*/*/*.webp"):
            if not path.is_file():
                continue
            try:
                coordinate = (int(path.parents[1].name), int(path.parent.name), int(path.stem))
                validate_tile_coordinates(*coordinate)
                files.append((path, *coordinate))
            except ValueError:
                continue
        marker = self.directory / revision_marker
        if not files and not marker.is_file():
            return 0

        for path, zoom, x, y in files:
            payload = path.read_bytes()
            self.put(zoom, x, y, payload)
            if self.get(zoom, x, y) != payload:
                raise RuntimeError(f"failed to verify imported tile: {path}")
        if marker.is_file():
            self.set_revision(marker.read_text(encoding="utf-8").strip())

        for path, _, _, _ in files:
            path.unlink()
        for directory in sorted(
            (path for path in self.directory.glob("*/*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            try:
                directory.rmdir()
            except OSError:
                pass
        if marker.is_file():
            marker.unlink()
        return len(files)

    def migrate_legacy_images(
        self,
        legacy_root: Path | None = None,
        revision_marker: str = ".revision",
        *,
        lossless: bool = False,
        prepare_image: Callable[[Image.Image], None] | None = None,
    ) -> int:
        """Convert a legacy ``z/x/y`` image tree into verified WebP database rows."""

        root = legacy_root or self.directory
        files: list[tuple[Path, int, int, int]] = []
        for extension in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
            for path in root.glob(f"*/*/{extension}"):
                if not path.is_file():
                    continue
                try:
                    coordinate = (
                        int(path.parents[1].name),
                        int(path.parent.name),
                        int(path.stem),
                    )
                    validate_tile_coordinates(*coordinate)
                    files.append((path, *coordinate))
                except ValueError:
                    continue
        marker = root / revision_marker
        if not files and not marker.is_file():
            return 0

        for path, zoom, x, y in files:
            with Image.open(path) as source:
                image = source.convert("RGBA")
            try:
                if prepare_image is not None:
                    prepare_image(image)
                payload = self.put_image(zoom, x, y, image, lossless=lossless)
            finally:
                image.close()
            if self.get(zoom, x, y) != payload:
                raise RuntimeError(f"failed to verify imported tile: {path}")
        if marker.is_file():
            self.set_revision(marker.read_text(encoding="utf-8").strip())

        for path, _, _, _ in files:
            path.unlink()
        for directory in sorted(
            (path for path in root.glob("*/*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            try:
                directory.rmdir()
            except OSError:
                pass
        if marker.is_file():
            marker.unlink()
        return len(files)
