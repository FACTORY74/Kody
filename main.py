"""Kody's standalone application entrypoint."""

from start_kody import main as start_kody_main


def main() -> int:
    """Start the configured Kody application and every enabled service."""

    return start_kody_main()


if __name__ == "__main__":
    raise SystemExit(main())
