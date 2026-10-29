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

import unittest

from config_service import (
    MapView,
    RuntimeConfiguration,
    ServiceConfiguration,
    Viewport,
    ViewportStore,
)


class MapViewTests(unittest.TestCase):
    def test_map_view_defaults_and_accepts_persisted_layer_selection(self) -> None:
        defaults = MapView.model_validate({"longitude": 0, "latitude": 0, "zoom": 1})
        self.assertFalse(defaults.layers.satellite_imagery)
        self.assertFalse(defaults.layers.copernicus)
        self.assertFalse(defaults.layers.terrain)
        self.assertFalse(defaults.layers.esa_dem)
        self.assertFalse(defaults.layers.worldcover)
        self.assertTrue(defaults.layers.vector_map)
        self.assertTrue(defaults.layers.grid)
        self.assertFalse(defaults.layers.display_mini_time_control)
        self.assertEqual(defaults.layers.data_source_opacity, {})

        saved = MapView.model_validate(
            {
                "longitude": 0,
                "latitude": 0,
                "zoom": 1,
                "layers": {
                    "satelliteImagery": True,
                    "copernicus": True,
                    "terrain": True,
                    "esaDem": True,
                    "worldcover": True,
                    "vectorMap": False,
                    "grid": False,
                    "gridCrosshair": True,
                    "displayMiniTimeControl": True,
                    "satelliteOpacity": 5,
                    "copernicusOpacity": 7,
                    "terrainOpacity": 6,
                    "esaDemOpacity": 5,
                    "worldcoverOpacity": 5,
                    "dataSources": {"local-adsb": False},
                    "dataSourceOpacity": {"local-shapes": 4, "local-adsb": 8},
                },
            }
        )

        self.assertTrue(saved.layers.satellite_imagery)
        self.assertTrue(saved.layers.copernicus)
        self.assertTrue(saved.layers.terrain)
        self.assertTrue(saved.layers.esa_dem)
        self.assertTrue(saved.layers.worldcover)
        self.assertFalse(saved.layers.vector_map)
        self.assertFalse(saved.layers.grid)
        self.assertTrue(saved.layers.grid_crosshair)
        self.assertTrue(saved.layers.display_mini_time_control)
        self.assertEqual(saved.layers.satellite_opacity, 5)
        self.assertEqual(saved.layers.copernicus_opacity, 7)
        self.assertEqual(saved.layers.terrain_opacity, 6)
        self.assertEqual(saved.layers.esa_dem_opacity, 5)
        self.assertEqual(saved.layers.worldcover_opacity, 5)
        self.assertFalse(saved.layers.data_sources["local-adsb"])
        self.assertEqual(saved.layers.data_source_opacity, {"local-shapes": 4, "local-adsb": 8})
        self.assertEqual(
            saved.model_dump(by_alias=True)["layers"]["dataSourceOpacity"],
            {"local-shapes": 4, "local-adsb": 8},
        )

    def test_rejects_data_source_opacity_outside_compact_control_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "less than or equal to 10"):
            MapView.model_validate(
                {
                    "longitude": 0,
                    "latitude": 0,
                    "zoom": 1,
                    "layers": {"dataSourceOpacity": {"local-adsb": 11}},
                }
            )

    def test_accepts_adsb_service(self) -> None:
        configuration = ServiceConfiguration.model_validate(
            {
                "services": [
                    {
                        "id": "local-adsb",
                        "type": "adsb",
                        "url": "http://127.0.0.1:8005/",
                    }
                ]
            }
        )

        self.assertEqual(configuration.services[0].type, "adsb")

    def test_accepts_symbols_service(self) -> None:
        configuration = ServiceConfiguration.model_validate(
            {"services": [{"id": "local-symbols", "type": "symbols", "url": "http://127.0.0.1:8007/"}]}
        )

        self.assertEqual(configuration.services[0].type, "symbols")

    def test_accepts_ais_service(self) -> None:
        configuration = ServiceConfiguration.model_validate(
            {"services": [{"id": "local-ais", "type": "ais", "url": "http://127.0.0.1:8008/"}]}
        )

        self.assertEqual(configuration.services[0].type, "ais")

    def test_accepts_terrain_service(self) -> None:
        configuration = ServiceConfiguration.model_validate(
            {"services": [{"id": "local-terrain", "type": "terrain", "url": "http://127.0.0.1:8010/"}]}
        )
        self.assertEqual(configuration.services[0].type, "terrain")

    def test_accepts_esa_download_terrain_service(self) -> None:
        configuration = ServiceConfiguration.model_validate(
            {"services": [{"id": "local-esa-dem", "type": "esa-dem", "url": "http://127.0.0.1:8013/"}]}
        )
        self.assertEqual(configuration.services[0].type, "esa-dem")

    def test_accepts_worldcover_service(self) -> None:
        configuration = ServiceConfiguration.model_validate(
            {"services": [{"id": "local-worldcover", "type": "worldcover", "url": "http://127.0.0.1:8014/"}]}
        )
        self.assertEqual(configuration.services[0].type, "worldcover")

    def test_accepts_independent_vector_and_satellite_map_services(self) -> None:
        configuration = ServiceConfiguration.model_validate(
            {
                "services": [
                    {"id": "local-vecmap", "type": "vecmap", "url": "http://127.0.0.1:8004/"},
                    {"id": "local-satmap", "type": "satmap", "url": "http://127.0.0.1:8006/"},
                ]
            }
        )

        self.assertEqual([service.type for service in configuration.services], ["vecmap", "satmap"])

    def test_accepts_copernicus_map_service(self) -> None:
        configuration = ServiceConfiguration.model_validate(
            {
                "services": [
                    {"id": "local-copernicus", "type": "copernicus", "url": "http://127.0.0.1:8011/"}
                ]
            }
        )

        self.assertEqual(configuration.services[0].type, "copernicus")

    def test_runtime_configuration_lists_all_services_with_bounded_log_retention(self) -> None:
        runtime = RuntimeConfiguration.model_validate(
            {
                "host": "127.0.0.1",
                "logRetentionMinutes": 30,
                "services": [
                    {
                        "id": "kody-web",
                        "displayName": "Kody web application",
                        "module": "app:app",
                        "workingDirectory": "kody",
                        "port": 8000,
                    },
                    {
                        "id": "shapes",
                        "displayName": "Shapes service",
                        "module": "kody_shapes.shapes_service:app",
                        "workingDirectory": "services/shapes",
                        "port": 8002,
                    },
                ],
            }
        )

        self.assertEqual(runtime.log_retention_minutes, 30)
        self.assertEqual([service.id for service in runtime.services], ["kody-web", "shapes"])

    def test_rejects_duplicate_enabled_runtime_ports(self) -> None:
        with self.assertRaisesRegex(ValueError, "ports must be unique"):
            RuntimeConfiguration.model_validate(
                {
                    "services": [
                        {
                            "id": "first",
                            "displayName": "First",
                            "module": "first.app:app",
                            "workingDirectory": "first",
                            "port": 8000,
                        },
                        {
                            "id": "second",
                            "displayName": "Second",
                            "module": "second.app:app",
                            "workingDirectory": "second",
                            "port": 8000,
                        },
                    ]
                }
            )

    def test_shares_validated_transient_viewport(self) -> None:
        store = ViewportStore()
        viewport = store.update(
            Viewport(west=-1.4, south=50.5, east=-1.2, north=50.7, zoom=12)
        )

        self.assertEqual(store.read(), viewport)


if __name__ == "__main__":
    unittest.main()
