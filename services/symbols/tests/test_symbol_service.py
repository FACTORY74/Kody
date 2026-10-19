import unittest

from kody_symbols.symbol_service import get_symbols


class SymbolServiceTests(unittest.TestCase):
    def test_supplies_named_orientable_and_always_up_svg_symbols(self) -> None:
        symbols = {symbol["name"]: symbol for symbol in get_symbols()["symbols"]}

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


if __name__ == "__main__":
    unittest.main()
