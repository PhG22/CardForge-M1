"""Setup config file and machine identity.

Config file (JSON, default /etc/cardforge/setup.json, readable by root only):

    {
      "password_hash": "pbkdf2_sha256$120000$...",   # set with `set-password`
      "name": "CardForge-A1B2",                      # optional, else from the serial
      "api_port": 8000                               # port of sorter_api
    }
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_CONFIG_PATH = Path(os.environ.get("CARDFORGE_SETUP_CONFIG", "/etc/cardforge/setup.json"))
DEFAULT_API_PORT = 8000


@dataclass
class SetupConfig:
    password_hash: Optional[str] = None
    name: Optional[str] = None
    api_port: int = DEFAULT_API_PORT

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG_PATH) -> "SetupConfig":
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
        return cls(
            password_hash=data.get("password_hash"),
            name=data.get("name"),
            api_port=int(data.get("api_port", DEFAULT_API_PORT)),
        )

    def save(self, path: Path = DEFAULT_CONFIG_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"password_hash": self.password_hash, "api_port": self.api_port}
        if self.name:
            data["name"] = self.name
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)  # the hash is still a secret
        os.replace(tmp, path)


@dataclass(frozen=True)
class MachineIdentity:
    machine_id: str
    name: str


def read_serial(
    cpuinfo: Path = Path("/proc/cpuinfo"),
    machine_id: Path = Path("/etc/machine-id"),
    device_tree_serial: Path = Path("/sys/firmware/devicetree/base/serial-number"),
) -> str:
    """The Pi's CPU serial, or /etc/machine-id on other Linux boards.

    Raspberry Pi OS kernels put the serial in /proc/cpuinfo. Plain Debian
    (mainline kernel) does not, but exposes it in the device tree.
    """
    try:
        match = re.search(r"^Serial\s*:\s*([0-9a-fA-F]+)\s*$", cpuinfo.read_text(), re.M)
        if match and int(match.group(1), 16) != 0:
            return match.group(1).lower()
    except OSError:
        pass
    try:
        value = device_tree_serial.read_bytes().strip(b"\x00 \n").decode("ascii", "ignore")
        if re.fullmatch(r"[0-9a-fA-F]+", value) and int(value, 16) != 0:
            return value.lower()
    except OSError:
        pass
    try:
        value = machine_id.read_text().strip()
        if value:
            return value.lower()
    except OSError:
        pass
    return "0000"


def machine_identity(config: SetupConfig, serial: Optional[str] = None) -> MachineIdentity:
    serial = serial or read_serial()
    name = config.name or f"CardForge-{serial[-4:].upper()}"
    return MachineIdentity(machine_id=serial, name=name)
