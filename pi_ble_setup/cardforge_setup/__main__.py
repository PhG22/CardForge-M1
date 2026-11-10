"""Command line.

    sudo python -m cardforge_setup set-password      # once, before the first run
    sudo python -m cardforge_setup info              # name, id, config path, Wi-Fi check
    sudo python -m cardforge_setup run               # the service (systemd runs this)
    sudo python -m cardforge_setup run --simulate-wifi   # test BLE without changing Wi-Fi
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import signal
import sys
from pathlib import Path

from . import SERVICE_UUID
from .auth import AuthManager, hash_password
from .config import DEFAULT_CONFIG_PATH, SetupConfig, machine_identity
from .network import NmcliWifi, SimulatedWifi, api_is_up, best_ip, wifi_readiness
from .protocol import SetupProtocol


def cmd_set_password(args: argparse.Namespace) -> int:
    config = SetupConfig.load(args.config)
    password = args.password
    if password is None:
        password = getpass.getpass("New machine password: ")
        if getpass.getpass("Repeat it: ") != password:
            print("The passwords do not match.", file=sys.stderr)
            return 1
    if len(password) < 4:
        print("Use at least 4 characters.", file=sys.stderr)
        return 1
    config.password_hash = hash_password(password)
    config.save(args.config)
    print(f"Saved to {args.config}. Restart the service: sudo systemctl restart cardforge-setup")
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    config = SetupConfig.load(args.config)
    identity = machine_identity(config)
    print(f"Name:        {identity.name}")
    print(f"Machine id:  {identity.machine_id}")
    print(f"Config:      {args.config}")
    print(f"Password:    {'set' if config.password_hash else 'NOT SET (run set-password)'}")
    print(f"API port:    {config.api_port}")
    print(f"BLE service: {SERVICE_UUID}")
    ok, message = wifi_readiness(args.interface)
    print(f"Wi-Fi:       {'OK, ' if ok else 'PROBLEM: '}{message}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config = SetupConfig.load(args.config)
    if not config.password_hash:
        print(
            f"No machine password in {args.config}. Run: sudo python -m cardforge_setup set-password",
            file=sys.stderr,
        )
        return 2
    identity = machine_identity(config)
    if not args.simulate_wifi:
        ok, message = wifi_readiness(args.interface)
        if not ok:
            # Keep running: "status" and setups that skip Wi-Fi (Ethernet) still work.
            logging.getLogger("cardforge_setup").error("Wi-Fi setup will fail: %s", message)
    wifi = SimulatedWifi() if args.simulate_wifi else NmcliWifi(interface=args.interface)
    protocol = SetupProtocol(
        identity=identity,
        auth=AuthManager(config.password_hash),
        wifi=wifi,
        api_port=config.api_port,
        ip_lookup=lambda: best_ip(wifi),
        api_check=api_is_up,
    )

    from .ble_server import BleSetupServer

    server = BleSetupServer(protocol, name=identity.name, adapter=args.adapter)

    async def main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await server.run(stop)

    asyncio.run(main())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cardforge_setup", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("set-password", help="set the machine password the app asks for")
    p.add_argument("--password", help="non-interactive (it ends up in the shell history)")
    p.set_defaults(func=cmd_set_password)

    p = sub.add_parser("info", help="show the machine name, id and whether Wi-Fi setup can work")
    p.add_argument("--interface", default="wlan0", help="Wi-Fi interface (default wlan0)")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("run", help="run the Bluetooth setup service")
    p.add_argument("--simulate-wifi", action="store_true", help="pretend to connect (network `fail` fails)")
    p.add_argument("--interface", default="wlan0", help="Wi-Fi interface (default wlan0)")
    p.add_argument("--adapter", default=None, help="Bluetooth adapter, e.g. hci0")
    # Also accept -v after "run" (the README shows it there).
    p.add_argument("-v", "--verbose", action="store_true", dest="run_verbose", help="debug logs")
    p.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose or getattr(args, "run_verbose", False) else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
