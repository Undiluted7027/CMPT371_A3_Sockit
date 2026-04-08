"""Project entry point for launching the Sockit host application."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from src.host_gui import launch_host_app
from src.server import DEFAULT_PORT

if TYPE_CHECKING:
    from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI parser for the current host-launch entry point."""
    parser = argparse.ArgumentParser(description="Sockit Trivia")
    subparsers = parser.add_subparsers(dest="mode")

    host_parser = subparsers.add_parser("host", help="Launch the host GUI")
    host_parser.add_argument(
        "--quiz",
        dest="quiz_path",
        help="Optional path to a quiz JSON file to pre-load.",
    )
    host_parser.add_argument(
        "--bind-host",
        default="0.0.0.0",
        help="Host/interface for the game server to bind to.",
    )
    host_parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="TCP/UDP port for the game server.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Parse command-line options and launch the requested app mode."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.mode != "host":
        parser.error("Only host mode is implemented in this iteration.")
    launch_host_app(args.quiz_path, bind_host=args.bind_host, port=args.port)


if __name__ == "__main__":
    main()
