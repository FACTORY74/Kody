"""Separate-process entry point for long-running Vector actions."""

from __future__ import annotations

import argparse

from .server import run_vector_action


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=("import-selected", "import-all", "rebuild-pyramid"),
    )
    run_vector_action(parser.parse_args().action)


if __name__ == "__main__":
    main()
