import unittest

from kody_app.config_service import (
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
        self.assertFalse(defaults.layers.terrain)
        self.assertTrue(defaults.layers.vector_map)
        self.assertTrue(defaults.layers.grid)
        self.assertEqual(defaults.layers.data_source_opacity, {})

        saved = MapView.model_validate(
            {
                "longitude": 0,
                "latitude": 0,
                "zoom": 1,
                "layers": {
                    "satelliteImagery": True,
                    "terrain": True,
                    "vectorMap": False,
                    "grid": False,
                    "gridCrosshair": True,
                    "satelliteOpacity": 5,
                    "terrainOpacity": 6,
                    "dataSources": {"local-adsb": False},
                    "dataSourceOpacity": {"local-shapes": 4, "local-adsb": 8},
                },
            }
        )

        self.assertTrue(saved.layers.satellite_imagery)
        self.assertTrue(saved.layers.terrain)
        self.assertFalse(saved.layers.vector_map)
        self.assertFalse(saved.layers.grid)
        self.assertTrue(saved.layers.grid_crosshair)
        self.assertEqual(saved.layers.satellite_opacity, 5)
        self.assertEqual(saved.layers.terrain_opacity, 6)
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

    def test_runtime_configuration_lists_all_services_with_bounded_log_retention(self) -> None:
        runtime = RuntimeConfiguration.model_validate(
            {
                "host": "127.0.0.1",
                "logRetentionMinutes": 30,
                "services": [
                    {
                        "id": "kody-web",
                        "displayName": "Kody web application",
                        "module": "kody_app.app:app",
                        "workingDirectory": "products/kody",
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
