import argparse
import sys
import tomllib
from pathlib import Path

from release_puller.core import run


def load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="release-puller",
        description="Poll GitHub releases and pull the latest version.",
    )
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="Path to TOML config file",
    )
    args = parser.parse_args()

    if not args.config.exists():
        print(f"error: config file not found: {args.config}", file=sys.stderr)
        sys.exit(1)

    config = load_config(args.config)
    run(config)
