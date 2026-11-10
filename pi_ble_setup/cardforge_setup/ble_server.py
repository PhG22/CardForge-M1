"""BLE GATT server (BlueZ through the `bless` library).

One service with two characteristics, like a serial port:
  RX  (write, write without response): the app writes request chunks here.
  TX  (notify, read): the Pi sends reply chunks as notifications.

bless ignores the offset of long reads and long writes, so neither side uses
them: every chunk fits in one packet (see framing.py).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any, Optional

from . import RX_UUID, SERVICE_UUID, TX_UUID
from .framing import FrameDecoder, split_frames
from .protocol import SetupProtocol

log = logging.getLogger(__name__)

# 20 bytes fit in the default BLE MTU (23), so replies work even if the phone
# never negotiates a bigger MTU. Replies are short, so this costs little.
DEFAULT_NOTIFY_CHUNK = 20


class BleSetupServer:
    def __init__(
        self,
        protocol: SetupProtocol,
        *,
        name: str,
        notify_chunk: int = DEFAULT_NOTIFY_CHUNK,
        adapter: Optional[str] = None,
    ) -> None:
        self.protocol = protocol
        self.name = name
        self.notify_chunk = notify_chunk
        self.adapter = adapter
        self._decoder = FrameDecoder()
        self._send_lock = asyncio.Lock()
        self._server: Any = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def run(self, stop: asyncio.Event) -> None:
        # Imported here so the rest of the package (and its tests) work
        # without BlueZ / D-Bus.
        from bless import (  # type: ignore
            BlessServer,
            GATTAttributePermissions,
            GATTCharacteristicProperties,
        )

        self._loop = asyncio.get_running_loop()
        await _prepare_adapter(self.name)

        kwargs = {"adapter": self.adapter} if self.adapter else {}
        server = BlessServer(name=self.name, loop=self._loop, **kwargs)
        server.read_request_func = self._on_read
        server.write_request_func = self._on_write

        await server.add_new_service(SERVICE_UUID)
        await server.add_new_characteristic(
            SERVICE_UUID,
            RX_UUID,
            GATTCharacteristicProperties.write | GATTCharacteristicProperties.write_without_response,
            None,
            GATTAttributePermissions.writeable,
        )
        await server.add_new_characteristic(
            SERVICE_UUID,
            TX_UUID,
            GATTCharacteristicProperties.notify | GATTCharacteristicProperties.read,
            None,  # macOS refuses a starting value on a characteristic that can notify
            GATTAttributePermissions.readable,
        )
        self._server = server
        await server.start()
        log.info("advertising as %s (service %s)", self.name, SERVICE_UUID)
        try:
            await stop.wait()
        finally:
            await server.stop()
            log.info("stopped advertising")

    # ------------------------------------------------------- bless callbacks

    def _on_read(self, characteristic: Any, **_: Any) -> bytearray:
        try:
            return characteristic.value or bytearray()
        except TypeError:  # no value set yet (bless keeps None)
            return bytearray()

    def _on_write(self, characteristic: Any, value: Any, **_: Any) -> None:
        # On Linux bless calls this on the event loop thread; on macOS it comes
        # from a CoreBluetooth thread. call_soon_threadsafe is right for both.
        if str(characteristic.uuid).lower() != RX_UUID:
            return
        assert self._loop is not None
        self._loop.call_soon_threadsafe(self._on_chunk, bytes(value))

    def _on_chunk(self, chunk: bytes) -> None:
        for message in self._decoder.feed(chunk):
            assert self._loop is not None
            self._loop.create_task(self._handle(message))

    async def _handle(self, message: Optional[bytes]) -> None:
        reply = await self.protocol.handle_raw(message)
        await self._notify(reply)

    async def _notify(self, payload: bytes) -> None:
        async with self._send_lock:  # one reply at a time, chunks in order
            characteristic = self._server.get_characteristic(TX_UUID)
            for chunk in split_frames(payload, self.notify_chunk):
                characteristic.value = bytearray(chunk)
                # macOS returns False when its send queue is full: wait and retry,
                # or the reply would arrive with a hole in it.
                for _ in range(100):
                    if self._server.update_value(SERVICE_UUID, TX_UUID) is not False:
                        break
                    await asyncio.sleep(0.02)
                else:
                    log.warning("notification dropped (phone not listening?)")
                    return
                await asyncio.sleep(0.015)  # let the stack send it before the next one


async def _prepare_adapter(name: str) -> None:
    """Powers the adapter on and sets its name, so phones that show the
    Bluetooth device name (not the advertised one) also see CardForge-XXXX.
    Linux only: on macOS the system manages the adapter."""
    if not sys.platform.startswith("linux"):
        return
    for args in (["bluetoothctl", "power", "on"], ["bluetoothctl", "system-alias", name]):
        try:
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            await asyncio.wait_for(proc.wait(), 10)
        except (OSError, asyncio.TimeoutError):
            log.warning("could not run %s", " ".join(args))
