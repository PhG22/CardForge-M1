"""Settings, read from environment variables (all prefixed MTG_)."""

from __future__ import annotations

import os
from dataclasses import dataclass, fields


def _env(name: str, default, cast):
    raw = os.environ.get(f"MTG_{name.upper()}")
    if raw is None or raw == "":
        return default
    if cast is bool:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return cast(raw)


@dataclass
class Settings:
    # --- HTTP server
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"             # DEBUG prints every RS-485 frame

    # --- RS-485 link (match what your test script used)
    simulate: bool = False              # True = fake A1/A2 in software, no serial port needed
    serial_port: str = "/dev/serial0"   # Pi UART; /dev/ttyUSB0 for a USB-RS485 adapter
    baudrate: int = 9600
    de_pin: int | None = None           # Pi only: BCM GPIO wired to DE+RE; None for auto-direction adapters
    de_rts: bool = False                # laptop + USB-TTL adapter: DE+RE wired to the adapter's RTS pin
    de_rts_invert: bool = False         # flip if the module never answers with de_rts on
    reply_timeout: float = 0.3          # seconds to wait for a reply frame
    retries: int = 2                    # resends after a timeout / bad checksum
    frame_gap_s: float = 0.002          # quiet time between a reply and the next request

    # --- modules
    bottom_enabled: bool = True         # False = top-only test bench: A2 is never contacted

    # --- mechanics (flap/drop timings from feeder_flap_test.ino)
    flap_closed_angle: int = 80
    flap_open_angle: int = 0
    flap_settle_s: float = 0.5          # time for the flap servo to reach its angle
    drop_timeout_s: float = 3.0         # max time for the card to leave the platform
    card_fall_s: float = 0.5            # after the platform IR clears, before closing the flap
    feed_timeout_s: float = 10.0        # Arduino gives up feeding after this (~33 feed steps)
    home_timeout_s: float = 40.0        # Arduino gives up homing after this (gentle ramp: ~20 s)
    move_timeout_s: float = 15.0        # Pi gives up waiting for GOTO / MOVE after this
    base_steps_per_turn: int = 12800    # for /base/move with degrees (measured: 4:1 gearing)
    poll_interval_s: float = 0.1        # STATUS polling period while a module is BUSY
    # Every byte on the bus pauses the base's step interrupt (SoftwareSerial), so the base
    # is left alone while it moves: wait the duration it reports, then poll slowly.
    base_poll_interval_s: float = 0.25  # after the reported move time has passed
    home_poll_interval_s: float = 1.0   # while homing (duration unknown)
    trays: int = 6

    # --- identification
    # "camera" = Pi camera + computer vision (the real machine)
    # "files"  = computer vision on photos from vision_images, in turn (no camera: simulator / bench)
    # "off"    = no vision: wait identify_delay_s and cycle trays 1..6 (mechanics bench test)
    vision: str = "off"
    vision_config: str = "vision.toml"  # cardforge.cv settings (thresholds, camera, cards.db path)
    vision_images: str = "input_cartas"
    identify_attempts: int = 3          # captures per card: 1 + 2 retries (RF29)
    identify_delay_s: float = 2.0       # vision=off only
    light_base_level: int = 60          # chamber LEDs, % (needs the optional LIGHT command on A1)
    light_step: int = 20                # added before each retry (RNF09)

    # --- runs
    max_cards_per_run: int = 250        # RNF27: a batch never exceeds one full feed load
    run_db: str = "data/run.db"         # local_run / local_cards (RF19, RNF19)

    # --- app sign-in (POST /auth/login). Empty password = any credentials are accepted.
    app_username: str = "admin"
    app_password: str = ""

    # --- simulator only
    sim_stack_cards: int = 8
    sim_time_scale: float = 1.0         # <1 makes the simulated motors faster
    sim_optional_commands: bool = True  # False = simulate a1_top.ino as it is today (no LIGHT / LED)

    @classmethod
    def from_env(cls) -> "Settings":
        kwargs = {}
        for f in fields(cls):
            default = f.default
            cast = {"int": int, "float": float, "bool": bool, "str": str, "int | None": int}[str(f.type)]
            kwargs[f.name] = _env(f.name, default, cast)
        return cls(**kwargs)
