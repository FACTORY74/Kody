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
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
import config_service
from config_service import (
    ApplicationConfiguration,
    ConfigurationStore,
    MapView,
    RuntimeConfiguration,
    ServiceConfiguration,
    Viewport,
    ViewportStore,
)


class MapViewTests(unittest.TestCase):
    def test_migrates_legacy_local_services_to_runtime_map_services(self) -> None:
        configuration = ApplicationConfiguration.model_validate(
            {
                "services": [
                    {
                        "id": "local-shapes",
                        "type": "shapes",
                        "url": "http://127.0.0.1:8002/",
                    }
                ],
                "runtime": {
                    "host": "127.0.0.1",
                    "services": [
                        {
                            "id": "shapes",
                            "displayName": "Shapes service",
                            "module": "kody_shapes.shapes_service:app",
                            "workingDirectory": "services/shapes",
                            "port": 8002,
                        }
                    ],
                },
            }
        )

        self.assertEqual(configuration.external_services, [])
        self.assertEqual(
            configuration.runtime.services[0].map_service.model_dump(by_alias=True),
            {"id": "local-shapes", "type": "shapes"},
        )

    def test_derives_local_service_catalog_and_rejects_stale_mutations(self) -> None:
        configuration = ApplicationConfiguration.model_validate(
            {
                "externalServices": [],
                "runtime": {
                    "host": "127.0.0.1",
                    "services": [
                        {
                            "id": "shapes",
                            "displayName": "Shapes service",
                            "module": "kody_shapes.shapes_service:app",
                            "workingDirectory": "services/shapes",
                            "port": 8002,
                            "mapService": {"id": "local-shapes", "type": "shapes"},
                        }
                    ],
                },
            }
        )
        with TemporaryDirectory() as directory:
            store = ConfigurationStore(Path(directory) / "kody.json")
            store.write(configuration)
            snapshot, _ = store.snapshot()

            catalog = store.services(snapshot)
            self.assertEqual(catalog.services[0].id, "local-shapes")
            self.assertEqual(str(catalog.services[0].url), "http://127.0.0.1:8002/")
            self.assertTrue(catalog.services[0].managed)

            data_directory_revision = store.revision_for(snapshot.data_directory)
            data_directory, next_revision = store.update_data_directory(
                "updated-data",
                data_directory_revision,
            )
            self.assertEqual(data_directory, "updated-data")
            self.assertNotEqual(next_revision, data_directory_revision)
            with self.assertRaises(HTTPException) as error:
                store.update_data_directory(
                    "other-data",
                    data_directory_revision,
                )
            self.assertEqual(error.exception.status_code, 409)

    def test_configuration_routes_return_and_require_resource_etags(self) -> None:
        configuration = ApplicationConfiguration.model_validate(
            {"externalServices": [], "runtime": {"services": []}}
        )
        with TemporaryDirectory() as directory:
            store = ConfigurationStore(Path(directory) / "kody.json")
            store.write(configuration)
            with patch.object(config_service, "configuration_store", store), TestClient(
                config_service.app
            ) as client:
                initial = client.get(
                    "/api/config/data-directory",
                    headers={"Origin": "http://127.0.0.1:8000"},
                )
                self.assertEqual(initial.status_code, 200)
                revision = initial.headers["etag"]
                self.assertIn("etag", initial.headers["access-control-expose-headers"].lower())

                legacy = client.put(
                    "/api/config/data-directory",
                    json={"dataDirectory": "legacy-updated-data"},
                )
                self.assertEqual(legacy.status_code, 200)
                legacy_revision = legacy.headers["etag"]
                self.assertNotEqual(legacy_revision, revision)

                updated = client.put(
                    "/api/config/data-directory",
                    headers={"If-Match": legacy_revision},
                    json={"dataDirectory": "updated-data"},
                )
                self.assertEqual(updated.status_code, 200)
                self.assertNotEqual(updated.headers["etag"], legacy_revision)

                stale = client.put(
                    "/api/config/data-directory",
                    headers={"If-Match": revision},
                    json={"dataDirectory": "other-data"},
                )
                self.assertEqual(stale.status_code, 409)

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
