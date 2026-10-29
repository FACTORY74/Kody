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

import tempfile
import unittest
from array import array
from pathlib import Path
from unittest.mock import patch
from unittest.mock import Mock

from kody_esa_dem.server import (
    DemConfiguration,
    DownloadState,
    NATIVE_TILE_ZOOM,
    Viewport,
    assessment,
    dem_request,
    elevation,
    read_numeric,
    write_tile,
    download_tile,
)


class EsaDemTests(unittest.TestCase):
    def test_request_uses_copernicus_30_dem(self) -> None:
        request = dem_request((-10, -20, 30, 40))

        self.assertEqual(request["input"]["data"][0]["type"], "dem")
        self.assertEqual(
            request["input"]["data"][0]["dataFilter"]["demInstance"],
            "COPERNICUS_30",
        )
        self.assertEqual(request["output"]["responses"][0]["format"]["type"], "image/tiff")

    def test_written_numeric_tile_supports_local_elevation_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            values = array("f", [100.0] * (256 * 256))
            with patch("kody_esa_dem.server.DEM_DIRECTORY", Path(directory)):
                write_tile(NATIVE_TILE_ZOOM, 0, 0, values)
                loaded = read_numeric(NATIVE_TILE_ZOOM, 0, 0)

            self.assertIsNotNone(loaded)
            self.assertEqual(loaded[0], 100.0)

    def test_configuration_requires_independent_client_credentials(self) -> None:
        configuration = DemConfiguration(clientId="client-id", clientSecret="client-secret")

        self.assertEqual(configuration.client_id, "client-id")
        self.assertEqual(configuration.client_secret, "client-secret")

    def test_native_assessment_uses_glo_30_tile_zoom(self) -> None:
        viewport = Viewport(west=-1, south=50, east=-0.5, north=50.5, zoom=7)
        with patch("kody_esa_dem.server.viewport_from_configuration_service", return_value=viewport):
            result = assessment(NATIVE_TILE_ZOOM, "GLO-30 ")

        self.assertEqual(result["zoom"], NATIVE_TILE_ZOOM)
        self.assertGreater(result["tileCount"], 0)

    def test_glo_30_permission_error_explains_ccm_requirement(self) -> None:
        response = Mock(status_code=403)
        session = Mock()
        session.post.return_value = response

        with self.assertRaisesRegex(PermissionError, "CCM"):
            download_tile(session, "access-token", 1, 0, 0)


if __name__ == "__main__":
    unittest.main()
