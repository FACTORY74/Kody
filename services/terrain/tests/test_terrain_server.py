import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from kody_terrain.server import (
    ElevationGrid,
    TerrainConfiguration,
    configuration_store,
    create_los_rosette,
    compose_parent_level,
    downsample_terrain_tile,
    get_manifest,
    get_palette,
    import_source,
    import_terrain,
    parse_arc_ascii,
    raster_tile_bytes,
    render_tile,
    native_tile_zoom,
    terrain_elevation,
    terrain_tile_image,
    terrain_packages,
)


class TerrainTests(unittest.TestCase):
    def test_import_source_accepts_an_absolute_terrain_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "external.asc"
            source.write_text(
                "ncols 1\nnrows 1\nxllcorner 0\nyllcorner 0\ncellsize 1\n10\n",
                encoding="utf-8",
            )
            with patch("kody_terrain.server.IMPORTS_DIRECTORY", root / "imports"):
                self.assertEqual(import_source(str(source)), source.resolve())

    def test_parser_supports_center_header_and_nodata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.asc"
            source.write_text("ncols 2\nnrows 2\nxllcenter 0.5\nyllcenter 50.5\ncellsize 1\nNODATA_value -9999\n10 20\n30 -9999\n", encoding="utf-8")
            grid = parse_arc_ascii(source)
        self.assertEqual((grid.west, grid.south, grid.nodata), (0.0, 50.0, -9999.0))
        self.assertEqual(grid.values, (10.0, 20.0, 30.0, -9999.0))

    def test_parser_accepts_explicit_world_mercator_for_arc_ascii(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "north-wales.asc"
            source.write_text(
                "ncols 1\nnrows 1\nxllcorner -537075\nyllcorner 6832675\ncellsize 50\n100\n",
                encoding="utf-8",
            )
            grid = parse_arc_ascii(source, "world-mercator")
        self.assertEqual(grid.projection, "world-mercator")
        self.assertEqual(grid.south, 6_832_675)

    def test_tile_rendering_colours_elevation_and_keeps_outside_transparent(self) -> None:
        grid = ElevationGrid(1, 1, 0, 0, 1, None, (100.0,))
        tile = render_tile(grid, 1, 1, 0)
        self.assertEqual(tile.getpixel((0, 255))[3], 255)
        self.assertEqual(tile.getpixel((255, 1))[3], 0)

    def test_native_tile_zoom_uses_source_resolution(self) -> None:
        self.assertEqual(
            native_tile_zoom(ElevationGrid(1, 1, 0, 0, 10, None, (100.0,), "web-mercator")),
            14,
        )
        self.assertEqual(
            native_tile_zoom(ElevationGrid(1, 1, 0, 0, 50, None, (100.0,), "web-mercator")),
            12,
        )

    def test_import_generates_native_tile_and_overview(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            imports = root / "imports"
            imports.mkdir()
            (imports / "terrain.asc").write_text("ncols 2\nnrows 2\nxllcorner -1\nyllcorner 50\ncellsize 1\n1 2\n3 4\n", encoding="utf-8")
            with patch("kody_terrain.server.IMPORTS_DIRECTORY", imports), patch("kody_terrain.server.TERRAIN_DIRECTORY", root / "terrain"):
                result = import_terrain("terrain.asc")
                overviews = list((root / "terrain" / "packages").glob("*/tiles/0/**/*.png"))
                numeric_tiles = list((root / "terrain" / "packages").glob("*/numeric-tiles/**/*.f32.gz"))
        self.assertIn("Imported 2×2 terrain cells", result)
        self.assertTrue(overviews)
        self.assertTrue(numeric_tiles)

    def test_import_builds_parent_overviews_for_zoomed_out_tiles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            imports = root / "imports"
            imports.mkdir()
            (imports / "detail.asc").write_text(
                "ncols 2\nnrows 2\nxllcorner -0.25\nyllcorner 50\ncellsize 0.25\n1 2\n3 4\n",
                encoding="utf-8",
            )
            with patch("kody_terrain.server.IMPORTS_DIRECTORY", imports), patch("kody_terrain.server.TERRAIN_DIRECTORY", root / "terrain"):
                import_terrain("detail.asc")
                overview = raster_tile_bytes(0, 0, 0)
        self.assertIsNotNone(overview)
        with Image.open(BytesIO(overview)) as image:
            self.assertGreater(image.getchannel("A").getextrema()[1], 0)

    def test_parent_overview_does_not_create_alpha_halos_outside_terrain(self) -> None:
        child = Image.new("RGBA", (256, 256))
        child.putpixel((0, 0), (91, 143, 67, 255))
        parent = downsample_terrain_tile(child)
        try:
            self.assertEqual(parent.getpixel((0, 0)), (91, 143, 67, 255))
            self.assertEqual(parent.getpixel((1, 0))[3], 0)
        finally:
            child.close()
            parent.close()

    def test_higher_resolution_package_overrides_a_base_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            imports = root / "imports"
            imports.mkdir()
            (imports / "base.asc").write_text("ncols 1\nnrows 1\nxllcorner -1\nyllcorner 50\ncellsize 2\n10\n", encoding="utf-8")
            (imports / "detail.asc").write_text("ncols 1\nnrows 1\nxllcorner -0.5\nyllcorner 50.5\ncellsize 0.5\n100\n", encoding="utf-8")
            with patch("kody_terrain.server.IMPORTS_DIRECTORY", imports), patch("kody_terrain.server.TERRAIN_DIRECTORY", root / "terrain"):
                import_terrain("base.asc")
                import_terrain("detail.asc")
                self.assertEqual(len(list((root / "terrain" / "packages").glob("*/manifest.json"))), 2)
                self.assertIsNotNone(raster_tile_bytes(0, 0, 0))

    def test_higher_resolution_tiles_replace_colours_without_alpha_blending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base_directory = root / "base"
            detail_directory = root / "detail"
            base_path = base_directory / "tiles" / "0" / "0"
            detail_path = detail_directory / "tiles" / "0" / "0"
            base_path.mkdir(parents=True)
            detail_path.mkdir(parents=True)
            Image.new("RGBA", (256, 256), (20, 40, 60, 255)).save(base_path / "0.png")
            detail = Image.new("RGBA", (256, 256))
            detail.putpixel((10, 10), (200, 120, 40, 127))
            detail.save(detail_path / "0.png")
            packages = [
                {"directory": base_directory, "nativeResolutionMetres": 1_000},
                {"directory": detail_directory, "nativeResolutionMetres": 10},
            ]
            with patch("kody_terrain.server.terrain_packages", return_value=packages):
                tile = raster_tile_bytes(0, 0, 0)

        self.assertIsNotNone(tile)
        with Image.open(BytesIO(tile)) as image:
            self.assertEqual(image.getpixel((0, 0)), (20, 40, 60, 255))
            self.assertEqual(image.getpixel((10, 10)), (200, 120, 40, 255))

    def test_higher_display_zoom_is_derived_from_the_best_available_package_tile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package_directory = root / "package"
            tile_path = package_directory / "tiles" / "0" / "0"
            tile_path.mkdir(parents=True)
            Image.new("RGBA", (256, 256), (20, 40, 60, 255)).save(tile_path / "0.png")
            packages = [{"directory": package_directory, "nativeResolutionMetres": 100}]

            with patch("kody_terrain.server.terrain_packages", return_value=packages):
                tile = raster_tile_bytes(1, 1, 0)

        self.assertIsNotNone(tile)
        with Image.open(BytesIO(tile)) as image:
            self.assertEqual(image.getpixel((128, 128)), (20, 40, 60, 255))

    def test_parent_derived_tile_preserves_binary_no_data_alpha(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tile_path = root / "tiles" / "0" / "0"
            tile_path.mkdir(parents=True)
            source = Image.new("RGBA", (256, 256))
            for x in range(120, 136):
                for y in range(256):
                    source.putpixel((x, y), (91, 143, 67, 255))
            source.save(tile_path / "0.png")
            source.close()

            child = terrain_tile_image(root, 1, 0, 0)

        self.assertIsNotNone(child)
        try:
            self.assertEqual({alpha for *_, alpha in child.getdata()}, {0, 255})
        finally:
            child.close()

    def test_higher_resolution_package_overrides_a_coarse_parent_derived_tile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coarse_directory = root / "coarse"
            detail_directory = root / "detail"
            coarse_path = coarse_directory / "tiles" / "0" / "0"
            detail_path = detail_directory / "tiles" / "1" / "1"
            coarse_path.mkdir(parents=True)
            detail_path.mkdir(parents=True)
            Image.new("RGBA", (256, 256), (20, 40, 60, 255)).save(coarse_path / "0.png")
            Image.new("RGBA", (256, 256), (200, 120, 40, 255)).save(detail_path / "0.png")
            packages = [
                {"directory": coarse_directory, "nativeResolutionMetres": 500},
                {"directory": detail_directory, "nativeResolutionMetres": 50},
            ]

            with patch("kody_terrain.server.terrain_packages", return_value=packages):
                tile = raster_tile_bytes(1, 1, 0)

        self.assertIsNotNone(tile)
        with Image.open(BytesIO(tile)) as image:
            self.assertEqual(image.getpixel((128, 128)), (200, 120, 40, 255))

    def test_elevation_lookup_uses_the_highest_resolution_covering_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            imports = root / "imports"
            imports.mkdir()
            (imports / "base.asc").write_text("ncols 1\nnrows 1\nxllcorner -1\nyllcorner 50\ncellsize 2\n10\n", encoding="utf-8")
            (imports / "detail.asc").write_text("ncols 1\nnrows 1\nxllcorner -0.5\nyllcorner 50.5\ncellsize 0.5\n100\n", encoding="utf-8")
            with patch("kody_terrain.server.IMPORTS_DIRECTORY", imports), patch("kody_terrain.server.TERRAIN_DIRECTORY", root / "terrain"):
                import_terrain("base.asc")
                import_terrain("detail.asc")
                with patch("kody_terrain.server.package_elevation_grid", side_effect=AssertionError):
                    self.assertEqual(terrain_elevation(-0.25, 50.75)[0], 100.0)
                    self.assertEqual(terrain_elevation(-0.75, 50.25)[0], 10.0)

    def test_manifest_exposes_manual_import_configuration_and_actions(self) -> None:
        manifest = get_manifest()
        configuration = manifest["configuration"]
        self.assertEqual(
            [field["key"] for field in configuration["fields"]],
            [
                "sourceFilename",
                "sourceProjection",
                "showPalette",
                "circleIncrements",
                "distanceIncrementMetres",
                "distanceLimitMetres",
                "observerHeightMetres",
            ],
        )
        self.assertFalse(configuration["fields"][0]["required"])
        self.assertEqual(
            [action["label"] for action in configuration["actions"]],
            [
                "Import terrain file",
                "Import viewport from local file",
                "Build numeric terrain chunks",
                "Rebuild terrain display tiles",
            ],
        )
        self.assertEqual(
            TerrainConfiguration.model_validate(
                {"sourceFilename": "ridge.grd", "sourceProjection": "world-mercator", "showPalette": True}
            ).source_projection,
            "world-mercator",
        )
        self.assertEqual(
            [action["label"] for action in manifest["contextMenuActions"]],
            ["Create LOS Rosette", "Clear LOS Rosette"],
        )

    def test_los_rosette_stops_each_ray_at_higher_terrain(self) -> None:
        configuration = TerrainConfiguration(
            circleIncrements=4,
            distanceIncrementMetres=100,
            distanceLimitMetres=200,
        )
        elevations = [(10.0, "terrain")] + [(13.0, "terrain")] * 4
        with patch("kody_terrain.server.terrain_elevation", side_effect=elevations):
            collection = create_los_rosette(-1.0, 50.0, configuration)

        coordinates = collection["features"][0]["geometry"]["coordinates"]
        self.assertEqual(len(coordinates), 4)
        self.assertTrue(all(len(ray) == 2 for ray in coordinates))
        self.assertTrue(all(ray[0] == [-1.0, 50.0] for ray in coordinates))

    def test_los_rosette_continues_to_the_distance_limit_outside_terrain_coverage(self) -> None:
        configuration = TerrainConfiguration(
            circleIncrements=4,
            distanceIncrementMetres=100,
            distanceLimitMetres=200,
        )
        elevations = [(10.0, "terrain")] + [None] * 8
        with patch("kody_terrain.server.terrain_elevation", side_effect=elevations):
            collection = create_los_rosette(-1.0, 50.0, configuration)

        feature = collection["features"][0]
        self.assertTrue(all(len(ray) == 3 for ray in feature["geometry"]["coordinates"]))
        self.assertEqual(feature["properties"]["missingTerrainSamples"], 8)

    def test_los_configuration_rejects_limit_below_sample_distance(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least distanceIncrementMetres"):
            TerrainConfiguration(distanceIncrementMetres=500, distanceLimitMetres=250)

    def test_los_rosette_applies_observer_height_to_the_horizon(self) -> None:
        configuration = TerrainConfiguration(
            circleIncrements=4,
            distanceIncrementMetres=100,
            distanceLimitMetres=100,
            observerHeightMetres=2,
        )
        elevations = [(10.0, "terrain")] + [(12.0, "terrain")] * 4
        with patch("kody_terrain.server.terrain_elevation", side_effect=elevations):
            collection = create_los_rosette(-1.0, 50.0, configuration)

        self.assertEqual(
            collection["features"][0]["properties"]["observerHeightMetres"],
            2,
        )
        self.assertEqual(len(collection["features"][0]["geometry"]["coordinates"]), 4)

    def test_palette_publishes_ordered_absolute_height_stops(self) -> None:
        palette = get_palette()
        stops = palette["stops"]
        self.assertEqual([stop["heightMetres"] for stop in stops], sorted(stop["heightMetres"] for stop in stops))
        self.assertTrue(all(stop["colour"].startswith("rgb(") for stop in stops))


if __name__ == "__main__":
    unittest.main()
