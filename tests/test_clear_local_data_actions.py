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

"""Service manifest contracts for the destructive local-data action."""

from __future__ import annotations

import sys
import unittest
from asyncio import run
from importlib import import_module
from inspect import iscoroutine
from pathlib import Path


ROOT_DIRECTORY = Path(__file__).resolve().parents[1]
for source_directory in ROOT_DIRECTORY.glob("services/*/src"):
    sys.path.insert(0, str(source_directory))


class ClearLocalDataManifestTests(unittest.TestCase):
    def test_every_configured_service_exposes_a_confirmed_clear_action(self) -> None:
        manifests = {
            "vecmap": import_module("kody_vecmap.server").get_manifest(),
            "satmap": import_module("kody_satmap.server").get_manifest(),
            "terrain": import_module("kody_terrain.server").get_manifest(),
            "shapes": import_module("kody_shapes.shapes_service").get_manifest(),
            "shape-server": import_module("kody_shape_server.shape_server").get_manifest(),
            "tracks": import_module("kody_tracks.track_service").get_manifest(),
            "adsb": import_module("kody_adsb.adsb_service").get_manifest(),
            "ais": import_module("kody_ais.ais_service").get_manifest(),
            "airlanes": import_module("kody_airlanes.airlanes_service").get_manifest(),
            "symbols": import_module("kody_symbols.symbol_service").get_manifest(),
        }

        for service_name, manifest in manifests.items():
            if iscoroutine(manifest):
                manifest = run(manifest)
            actions = manifest["configuration"]["actions"]
            action = next(action for action in actions if action["path"] == "/api/actions/clear-local-data")
            self.assertEqual(action["label"], "Clear local data", service_name)
            self.assertEqual(action["method"], "POST", service_name)
            self.assertIn("cannot be undone", action["confirmationMessage"].lower(), service_name)


if __name__ == "__main__":
    unittest.main()
