"""Server settings, read from environment variables (all prefixed CF_)."""

from __future__ import annotations

import os
from dataclasses import dataclass, fields


@dataclass
class ServerSettings:
    # PostgreSQL connection (libpq URL)
    database_url: str = "postgresql://cardforge:cardforge@localhost:5432/cardforge"
    # Signs the users' access tokens (HS256). Empty = a random secret per start:
    # every user must sign in again after a restart (fine for tests only).
    token_secret: str = ""
    token_ttl_hours: float = 168.0          # 7 days
    # Tokens the sorter machines send to POST /sync/runs, comma-separated.
    # Make one with: python -m cardforge_server new-machine-token
    machine_tokens: str = ""
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"
    pool_max_size: int = 10
    auto_migrate: bool = True               # apply migrations/ on start

    @property
    def machine_token_list(self) -> list[str]:
        return [t.strip() for t in self.machine_tokens.split(",") if t.strip()]

    @classmethod
    def from_env(cls) -> "ServerSettings":
        kwargs = {}
        for f in fields(cls):
            raw = os.environ.get(f"CF_{f.name.upper()}")
            if raw is None or raw == "":
                continue
            cast = {"str": str, "int": int, "float": float, "bool": bool}[str(f.type)]
            kwargs[f.name] = raw.strip().lower() in ("1", "true", "yes", "on") if cast is bool else cast(raw)
        return cls(**kwargs)
