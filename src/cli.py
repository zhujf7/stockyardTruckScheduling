from __future__ import annotations

import argparse
from pathlib import Path

from paths import DEFAULT_SCHEDULE_PATH, LOCAL_MAP_PATH
from visualization.schedule_viewer import generate_schedule_viewer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dataset and example results for DBT stockyard truck scheduling."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    visualization = subparsers.add_parser(
        "visualization",
        help="Generate a browser-based vehicle schedule viewer",
    )
    visualization.add_argument(
        "--schedule",
        default=str(DEFAULT_SCHEDULE_PATH),
        help="Static or rolling schedule result JSON",
    )
    visualization.add_argument(
        "--map",
        default=str(LOCAL_MAP_PATH),
        help="Local-coordinate map JSON",
    )
    visualization.add_argument(
        "--output",
        default=None,
        help="Generated HTML path (default: derived from the schedule path)",
    )
    visualization.add_argument(
        "--open",
        action="store_true",
        help="Open the generated viewer in the default browser",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "visualization":
        try:
            output = generate_schedule_viewer(
                schedule_path=Path(args.schedule),
                map_path=Path(args.map),
                output_path=Path(args.output) if args.output else None,
                open_browser=args.open,
            )
        except (OSError, ValueError) as exc:
            print(f"Visualization failed: {exc}")
            return 1

        print(f"Viewer written to: {output}")
        if not args.open:
            print("Add --open to launch it in your default browser.")
        return 0

    return 0
