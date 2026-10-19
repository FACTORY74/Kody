import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from kody_symbols import symbol_service


class SymbolServiceTests(unittest.TestCase):
    @staticmethod
    def starter_symbols_directory() -> Path:
        return Path(__file__).resolve().parents[3] / "data_starter" / "symbols"

    def test_supplies_named_orientable_and_always_up_svg_symbols(self) -> None:
        directory = self.starter_symbols_directory()
        with (
            patch.object(symbol_service, "SYMBOLS_DIRECTORY", directory),
            patch.object(symbol_service, "CATALOGUE_FILE", directory / "symbols.json"),
        ):
            symbols = {symbol["name"]: symbol for symbol in symbol_service.get_symbols()["symbols"]}

        self.assertEqual(
            set(symbols),
            {
                "plane", "ship", "sailboat", "yacht", "fishing_boat", "tug",
                "pilot_boat", "rescue_boat", "passenger_ship", "cargo_ship",
                "tanker", "high_speed_craft", "military_ship", "law_enforcement_boat",
                "service_boat", "car", "heli", "balloon", "undefined", "map_pin", "crosshair",
            },
        )
        self.assertEqual(symbols["plane"]["orientation"], "orientable")
        self.assertEqual(symbols["ship"]["orientation"], "orientable")
        self.assertEqual(symbols["heli"]["orientation"], "orientable")
        self.assertEqual(symbols["balloon"]["orientation"], "alwaysup")
        self.assertEqual(symbols["undefined"]["orientation"], "alwaysup")
        self.assertEqual(symbols["plane"]["colorMode"], "colorable")
        self.assertEqual(symbols["ship"]["colorMode"], "colorable")
        self.assertEqual(symbols["plane"]["scale"], 2)
        for name in {
            "sailboat", "yacht", "fishing_boat", "tug", "pilot_boat", "rescue_boat",
            "passenger_ship", "cargo_ship", "tanker", "high_speed_craft", "military_ship",
            "law_enforcement_boat", "service_boat",
        }:
            self.assertEqual(symbols[name]["orientation"], "orientable")
            self.assertEqual(symbols[name]["colorMode"], "colorable")
        self.assertEqual(symbols["undefined"]["colorMode"], "fixed")
        self.assertEqual(symbols["map_pin"]["orientation"], "alwaysup")
        self.assertIn("<svg", symbols["plane"]["svg"])

    def test_uses_catalogue_name_instead_of_svg_filename(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            (directory / "aircraft.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"></svg>',
                encoding="utf-8",
            )
            (directory / "symbols.json").write_text(
                json.dumps(
                    {
                        "symbols": [
                            {
                                "name": "surveillance-aircraft",
                                "file": "aircraft.svg",
                                "orientation": "orientable",
                                "colorMode": "colorable",
                                "scale": 2,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(symbol_service, "SYMBOLS_DIRECTORY", directory),
                patch.object(symbol_service, "CATALOGUE_FILE", directory / "symbols.json"),
            ):
                symbols = symbol_service.get_symbols()["symbols"]

        self.assertEqual(symbols[0]["name"], "surveillance-aircraft")
        self.assertEqual(symbols[0]["orientation"], "orientable")
        self.assertEqual(symbols[0]["colorMode"], "colorable")
        self.assertEqual(symbols[0]["scale"], 2)

    def test_handles_empty_symbols_directory(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory) / "symbols"
            with (
                patch.object(symbol_service, "SYMBOLS_DIRECTORY", directory),
                patch.object(symbol_service, "CATALOGUE_FILE", directory / "symbols.json"),
            ):
                self.assertEqual(symbol_service.get_symbols()["symbols"], ())


if __name__ == "__main__":
    unittest.main()
