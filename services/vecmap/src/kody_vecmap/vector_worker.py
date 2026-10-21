"""Separate-process entry point for the Natural Earth import."""

from __future__ import annotations

import argparse

from .server import run_natural_earth_action


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("import-natural-earth",))
    run_natural_earth_action(parser.parse_args().action)


if __name__ == "__main__":
    main()
