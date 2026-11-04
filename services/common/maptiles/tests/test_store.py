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

"""Focused tests for the shared WebP quadtree store."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from fastapi import HTTPException
from PIL import Image

from maptiles import WebPQuadTreeStore, crop_parent_tile, standard_tile_response


def webp_payload(color: str) -> bytes:
    output = BytesIO()
    Image.new("RGBA", (256, 256), color).save(output, format="WEBP")
    return output.getvalue()


class WebPQuadTreeStoreTests(unittest.TestCase):
    def test_round_trip_revision_and_coordinates(self) -> None:
        with TemporaryDirectory() as directory:
            store = WebPQuadTreeStore(Path(directory))
            payload = webp_payload("red")

            store.put(2, 1, 3, payload)
            revision = store.set_revision("test-revision")

            self.assertEqual(store.get(2, 1, 3), payload)
            self.assertEqual(store.coordinates(2), {(1, 3)})
            self.assertEqual(store.zooms(), [2])
            self.assertEqual(revision, "test-revision")
            self.assertEqual(store.revision(), "test-revision")

    def test_legacy_files_are_verified_before_deletion(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "1" / "1" / "0.webp"
            legacy.parent.mkdir(parents=True)
            payload = webp_payload("blue")
            legacy.write_bytes(payload)
            (root / ".revision").write_text("legacy", encoding="utf-8")

            store = WebPQuadTreeStore(root)

            self.assertEqual(store.migrate_legacy_files(), 1)
            self.assertFalse(legacy.exists())
            self.assertEqual(store.get(1, 1, 0), payload)
            self.assertEqual(store.revision(), "legacy")

    def test_legacy_png_files_are_converted_to_webp_before_deletion(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "1" / "1" / "0.png"
            legacy.parent.mkdir(parents=True)
            Image.new("RGBA", (256, 256), "blue").save(legacy)

            store = WebPQuadTreeStore(root)

            self.assertEqual(store.migrate_legacy_images(), 1)
            self.assertFalse(legacy.exists())
            payload = store.get(1, 1, 0)
            self.assertIsNotNone(payload)
            with Image.open(BytesIO(payload)) as image:
                self.assertEqual(image.format, "WEBP")

    def test_legacy_database_schema_is_migrated(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            payload = webp_payload("green")
            connection = sqlite3.connect(root / "tiles.sqlite")
            connection.execute(
                """
                CREATE TABLE tiles (
                    zoom INTEGER NOT NULL,
                    x INTEGER NOT NULL,
                    y INTEGER NOT NULL,
                    image_webp BLOB NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (zoom, x, y)
                )
                """
            )
            connection.execute(
                "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO tiles VALUES (0, 0, 0, ?, '2026-01-01T00:00:00+00:00')",
                (payload,),
            )
            connection.execute("INSERT INTO metadata VALUES ('revision', 'legacy')")
            connection.commit()
            connection.close()

            store = WebPQuadTreeStore(root)

            self.assertEqual(store.get(0, 0, 0), payload)
            self.assertEqual(store.revision(), "legacy")

    def test_invalid_payload_and_coordinates_are_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            store = WebPQuadTreeStore(Path(directory))

            with self.assertRaisesRegex(ValueError, "valid WebP"):
                store.put(0, 0, 0, b"not-an-image")
            with self.assertRaisesRegex(ValueError, "invalid Web-Mercator"):
                store.get(1, 2, 0)

    def test_parent_tile_is_cropped_for_descendant(self) -> None:
        output = BytesIO()
        parent = Image.new("RGBA", (256, 256), "black")
        parent.paste("red", (128, 0, 256, 128))
        parent.save(output, format="WEBP")
        parent.close()

        child = crop_parent_tile(output.getvalue(), 0, 1, 1, 0)
        try:
            self.assertEqual(child.size, (256, 256))
            for actual, expected in zip(child.getpixel((128, 128))[:3], (255, 0, 0)):
                self.assertLessEqual(abs(actual - expected), 2)
        finally:
            child.close()

    def test_standard_http_contract_handles_invalid_and_missing_tiles(self) -> None:
        payload = webp_payload("red")

        response = standard_tile_response(
            0, 0, 0, lambda *_: payload, maximum_zoom=18, missing_detail="missing"
        )
        self.assertEqual(response.media_type, "image/webp")
        self.assertEqual(response.body, payload)

        with self.assertRaisesRegex(HTTPException, "invalid raster tile coordinates"):
            standard_tile_response(
                1, 2, 0, lambda *_: payload, maximum_zoom=18, missing_detail="missing"
            )
        with self.assertRaisesRegex(HTTPException, "missing"):
            standard_tile_response(
                0, 0, 0, lambda *_: None, maximum_zoom=18, missing_detail="missing"
            )


if __name__ == "__main__":
    unittest.main()
