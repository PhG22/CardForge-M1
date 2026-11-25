"""Setup commands: what the Pi answers to each request from the app.

Request:  {"id": 7, "cmd": "auth", "password": "..."}
Reply:    {"id": 7, "ok": true, ...}  or  {"id": 7, "ok": false, "error": "CODE"}

This module knows nothing about Bluetooth, so it can be tested on any computer.
See docs/ble-setup-protocol.md for every command and error code.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from . import PROTOCOL_VERSION
from .auth import AuthManager
from .config import MachineIdentity
from .network import WifiManager

log = logging.getLogger(__name__)

Json = Dict[str, Any]


class SetupProtocol:
    def __init__(
        self,
        *,
        identity: MachineIdentity,
        auth: AuthManager,
        wifi: WifiManager,
        api_port: int,
        ip_lookup: Callable[[], Awaitable[Optional[str]]],
        api_check: Callable[[int], Awaitable[bool]],
    ) -> None:
        self.identity = identity
        self.auth = auth
        self.wifi = wifi
        self.api_port = api_port
        self._ip_lookup = ip_lookup
        self._api_check = api_check
        self._handlers: Dict[str, Callable[[Json], Awaitable[Json]]] = {
            "ping": self._ping,
            "info": self._info,
            "auth": self._auth,
            "wifi": self._wifi,
            "status": self._status,
        }

    async def handle_raw(self, raw: Optional[bytes]) -> bytes:
        """Decodes one message, runs it and returns the encoded reply."""
        if raw is None:
            return self._encode({"id": None, "ok": False, "error": "TOO_LONG"})
        try:
            request = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._encode({"id": None, "ok": False, "error": "BAD_JSON"})
        if not isinstance(request, dict):
            return self._encode({"id": None, "ok": False, "error": "BAD_JSON"})
        return self._encode(await self.handle(request))

    async def handle(self, request: Json) -> Json:
        req_id = request.get("id")
        if not isinstance(req_id, int) or isinstance(req_id, bool):
            req_id = None
        handler = self._handlers.get(request.get("cmd"))  # type: ignore[arg-type]
        if handler is None:
            reply: Json = {"ok": False, "error": "BAD_CMD"}
        else:
            try:
                reply = await handler(request)
            except Exception:
                log.exception("command %r failed", request.get("cmd"))
                reply = {"ok": False, "error": "INTERNAL"}
        return {"id": req_id, **reply}

    @staticmethod
    def _encode(reply: Json) -> bytes:
        return json.dumps(reply, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    # ------------------------------------------------------------ commands

    async def _ping(self, _: Json) -> Json:
        return {"ok": True}

    async def _info(self, _: Json) -> Json:
        return {
            "ok": True,
            "protocol": PROTOCOL_VERSION,
            "machine_id": self.identity.machine_id,
            "name": self.identity.name,
            "api_port": self.api_port,
        }

    async def _auth(self, request: Json) -> Json:
        password = request.get("password")
        if not isinstance(password, str) or not password:
            return {"ok": False, "error": "BAD_ARG", "field": "password"}
        locked = self.auth.check_lock()
        if locked is None:
            # PBKDF2 takes about a second on a Pi 3: keep the event loop free.
            result = await asyncio.to_thread(self.auth.login, password)
        else:
            result = locked
        if result.ok:
            log.info("setup session opened")
            return {"ok": True, "session": result.session, "expires_in": 600}
        log.warning("auth refused: %s", result.error)
        reply: Json = {"ok": False, "error": result.error}
        if result.attempts_left is not None:
            reply["attempts_left"] = result.attempts_left
        if result.retry_after is not None:
            reply["retry_after"] = result.retry_after
        return reply

    async def _wifi(self, request: Json) -> Json:
        if not self.auth.is_valid(request.get("session")):
            return {"ok": False, "error": "NOT_AUTHENTICATED"}
        ssid = request.get("ssid")
        password = request.get("password", "")
        if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32:
            return {"ok": False, "error": "BAD_ARG", "field": "ssid"}
        if not isinstance(password, str) or not _valid_wifi_password(password):
            return {"ok": False, "error": "BAD_ARG", "field": "password"}
        if not self.wifi.start(ssid, password):
            return {"ok": False, "error": "BUSY"}
        log.info("connecting to Wi-Fi %r", ssid)  # never log the password
        return {"ok": True, "state": "connecting"}

    async def _status(self, _: Json) -> Json:
        ip = await self._ip_lookup()
        api_ok = await self._api_check(self.api_port) if ip else False
        return {
            "ok": True,
            "wifi": self.wifi.status.to_json(),
            "ip": ip,
            "api_port": self.api_port,
            "api_ok": api_ok,
        }


def _valid_wifi_password(password: str) -> bool:
    """Empty (open network), 8-63 characters (WPA passphrase) or 64 hex digits."""
    if password == "":
        return True
    if len(password) == 64:
        return all(c in "0123456789abcdefABCDEF" for c in password)
    return 8 <= len(password) <= 63
