"""python -m sorter_api [--simulate] [--top-only] [--port 8000]"""

from __future__ import annotations

import argparse

import uvicorn

from .api import create_app
from .config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="MTG card sorter API (Raspberry Pi)")
    parser.add_argument("--simulate", action="store_true", help="fake A1/A2 in software (no serial port)")
    parser.add_argument("--top-only", action="store_true",
                        help="only the top module (A1) is connected: no homing, trays or base moves")
    parser.add_argument("--host", help="bind address (default MTG_HOST or 0.0.0.0)")
    parser.add_argument("--port", type=int, help="HTTP port (default MTG_PORT or 8000)")
    args = parser.parse_args()

    settings = Settings.from_env()
    if args.simulate:
        settings.simulate = True
    if args.top_only:
        settings.bottom_enabled = False
    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = args.port

    uvicorn.run(create_app(settings), host=settings.host, port=settings.port,
                log_level=settings.log_level.lower())


if __name__ == "__main__":
    main()
