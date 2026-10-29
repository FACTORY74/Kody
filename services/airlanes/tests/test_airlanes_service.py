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

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
import unittest
from zipfile import ZipFile

from kody_airlanes.airlanes_service import (
    AirLanesConfiguration,
    AirspaceStore,
    ConfigurationStore,
    controlled_kml_to_geojson,
    dataset_urls,
    get_configuration,
    get_manifest,
    kml_from_archive,
    kml_to_geojson,
    notam_pib_to_geojson,
)


class AirLanesServiceTests(unittest.TestCase):
    def test_converts_nats_style_sector_polygon_to_geojson_line(self) -> None:
        collection = kml_to_geojson(b"""<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><name>London Sector</name><Polygon><outerBoundaryIs><LinearRing><coordinates>-1,50,0 0,50,0 0,51,0 -1,50,0</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></Document></kml>""")

        self.assertEqual(collection["features"][0]["properties"]["name"], "London Sector")
        self.assertEqual(collection["features"][0]["properties"]["dataCategory"], "atc-sectors")
        self.assertEqual(collection["features"][0]["geometry"]["type"], "LineString")
        self.assertEqual(collection["features"][0]["geometry"]["coordinates"][0], [-1.0, 50.0])

    def test_extracts_supported_controlled_airspace_from_kml_folder_hierarchy(self) -> None:
        collection = controlled_kml_to_geojson(b"""<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Folder><name>UK Airspace</name><Folder><name>CTA</name><Placemark><name>Solent CTA</name><Polygon><outerBoundaryIs><LinearRing><coordinates>-1.4,50.6,0 -1.1,50.6,0 -1.1,50.8,0</coordinates></LinearRing></outerBoundaryIs><innerBoundaryIs><LinearRing><coordinates>-1.3,50.65,0 -1.2,50.65,0 -1.2,50.7,0</coordinates></LinearRing></innerBoundaryIs></Polygon></Placemark></Folder><Folder><name>Unrelated</name><Placemark><name>Ignored</name><Polygon><outerBoundaryIs><LinearRing><coordinates>0,0,0 1,0,0 1,1,0</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></Folder></Folder></Document></kml>""", "https://example.test/aip.kmz")

        self.assertEqual(len(collection["features"]), 1)
        feature = collection["features"][0]
        self.assertEqual(feature["geometry"]["type"], "Polygon")
        self.assertEqual(len(feature["geometry"]["coordinates"]), 2)
        self.assertEqual(feature["properties"]["category"], "CTA")
        self.assertEqual(feature["properties"]["folderHierarchy"], ["UK Airspace", "CTA"])
        self.assertEqual(feature["properties"]["sourceUrl"], "https://example.test/aip.kmz")
        self.assertEqual(feature["properties"]["fillOpacity"], 0.25)

    def test_selects_current_official_nats_kml_downloads(self) -> None:
        page = """
        <a href="/datasets/EG_LAC_PC_SECTOR_DS_FULL_20261001_KML.zip">current sectors</a>
        <a href="/datasets/EG_LAC_PC_SECTOR_DS_FULL_20261029_KML.zip">next sectors</a>
        <a href="/datasets/EG_AIP_DS_FULL_20261001_KML.zip">current AIP</a>
        <a href="/datasets/EG_AIP_DS_FULL_20261029_KML.zip">next AIP</a>
        """

        urls = dataset_urls(page, datetime(2026, 10, 17, tzinfo=UTC))

        self.assertTrue(urls["sectors"].endswith("20261001_KML.zip"))
        self.assertTrue(urls["controlledAirspace"].endswith("20261001_KML.zip"))

    def test_response_filters_categories_from_persisted_configuration(self) -> None:
        store = AirspaceStore(Path("services\\airlanes\\data\\test-filter-cache.geojson"))
        store.collection = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"dataCategory": "atc-sectors"}, "geometry": None},
                {"type": "Feature", "properties": {"dataCategory": "controlled-airspace"}, "geometry": None},
            ],
        }

        response = store.response(AirLanesConfiguration(showAtcSectors=False, showControlledAirspace=True))

        self.assertEqual(response["enabledCategories"], ["controlled-airspace"])
        self.assertEqual(len(response["features"]), 1)
        self.assertEqual(response["features"][0]["properties"]["dataCategory"], "controlled-airspace")

    def test_manifest_and_configuration_surface_display_checkboxes(self) -> None:
        fields = {field["key"]: field for field in get_manifest()["configuration"]["fields"]}
        configuration = get_configuration()

        self.assertEqual(fields["showAtcSectors"]["type"], "boolean")
        self.assertEqual(fields["showControlledAirspace"]["type"], "boolean")
        self.assertEqual(fields["showNotams"]["type"], "boolean")
        self.assertIn("showAtcSectors", configuration)
        self.assertIn("showControlledAirspace", configuration)
        self.assertIn("showNotams", configuration)

    def test_converts_pib_notam_q_line_circle_to_geojson(self) -> None:
        collection = notam_pib_to_geojson(
            b"""<Pib><Notam><Series>L</Series><Number>4903</Number><Year>26</Year><QLine><FIR>EGTT</FIR><Code23>WU</Code23><Code45>LW</Code45><Lower>0</Lower><Upper>2</Upper></QLine><Coordinates>5407N00312W</Coordinates><Radius>1</Radius><StartValidity>2608200000</StartValidity><EndValidity>2611202359</EndValidity><ItemE>Temporary warning</ItemE></Notam></Pib>"""
        )

        self.assertEqual(len(collection["features"]), 1)
        feature = collection["features"][0]
        self.assertEqual(feature["properties"]["name"], "L4903/26")
        self.assertEqual(feature["properties"]["radiusNauticalMiles"], 1)
        self.assertEqual(feature["properties"]["qCode"]["code"], "WULW")
        self.assertTrue(feature["properties"]["mapDisplay"])
        self.assertFalse(feature["properties"]["label"])
        self.assertEqual(len(feature["geometry"]["coordinates"][0]), 49)

    def test_uses_item_e_bounded_area_as_notam_polygon(self) -> None:
        collection = notam_pib_to_geojson(
            b"""<Pib><Notam><Series>W</Series><Number>1</Number><Year>26</Year><QLine><Code23>WU</Code23><Code45>LW</Code45></QLine><Coordinates>5145N00317W</Coordinates><Radius>3</Radius><ItemE>UAS OPR WI AREA BOUNDED BY 514557N 0031740W - 512903N 0030454W - 512358N 0032156W.</ItemE></Notam></Pib>"""
        )

        feature = collection["features"][0]
        self.assertEqual(feature["properties"]["geometrySource"], "item-e-polygon")
        coordinates = feature["geometry"]["coordinates"][0]
        self.assertEqual(len(coordinates), 4)
        for actual, expected in zip(
            coordinates,
            [[-3.2944444444444443, 51.76583333333333], [-3.0816666666666666, 51.48416666666667], [-3.3655555555555554, 51.39944444444445], [-3.2944444444444443, 51.76583333333333]],
        ):
            self.assertAlmostEqual(actual[0], expected[0])
            self.assertAlmostEqual(actual[1], expected[1])

    def test_display_checkbox_configuration_persists(self) -> None:
        path = Path("data\\airlanes-config-persistence-test.json")
        path.unlink(missing_ok=True)
        try:
            store = ConfigurationStore(path)
            store.update(
                AirLanesConfiguration(showAtcSectors=False, showControlledAirspace=False),
                store.response()["revision"],
            )

            reloaded = ConfigurationStore(path).response()

            self.assertFalse(reloaded["showAtcSectors"])
            self.assertFalse(reloaded["showControlledAirspace"])
        finally:
            path.unlink(missing_ok=True)

    def test_reads_kml_from_nats_kmz_bundle(self) -> None:
        kml = b"<kml/>"
        nested = BytesIO()
        with ZipFile(nested, "w") as archive:
            archive.writestr("document.kml", kml)
        outer = BytesIO()
        with ZipFile(outer, "w") as archive:
            archive.writestr("sector.kmz", nested.getvalue())
        with ZipFile(outer) as archive:
            self.assertEqual(kml_from_archive(archive), kml)


if __name__ == "__main__":
    unittest.main()
