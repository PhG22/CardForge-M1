"""python -m cardforge_server [serve | migrate | new-machine-token]

    serve               apply the migrations, then run the API (default)
    migrate             only apply the migrations (database/ schema) and exit
    new-machine-token   print a random token: put it in CF_MACHINE_TOKENS (server)
                        and MTG_MACHINE_TOKEN (Raspberry Pi)
"""

from __future__ import annotations

import argparse
import logging
import secrets

from .config import ServerSettings


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m cardforge_server", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", default="serve", choices=["serve", "migrate", "new-machine-token"])
    args = parser.parse_args()

    if args.command == "new-machine-token":
        print(secrets.token_urlsafe(32))
        return

    settings = ServerSettings.from_env()
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    if args.command == "migrate":
        from .db import migrate

        applied = migrate(settings.database_url)
        print("applied: " + ", ".join(applied) if applied else "database is up to date")
        return

    import uvicorn

    from .api import create_app

    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level=settings.log_level.lower())


if __name__ == "__main__":
    main()
