"""CardForge Bluetooth setup service for the Raspberry Pi.

The phone app connects over Bluetooth Low Energy (BLE) the first time, checks
the machine password, and sends the Wi-Fi details. After that, the app talks to
the sorter's REST API (sorter_api, port 8000) over Wi-Fi.

The full protocol is in docs/ble-setup-protocol.md. Keep the UUIDs and the
protocol version in sync with the app (lib/features/machine/ble/ble_setup_codec.dart).
"""

PROTOCOL_VERSION = 1

SERVICE_UUID = "cdaa0001-bc5d-45ab-8926-3aa83f2ab5c0"
RX_UUID = "cdaa0002-bc5d-45ab-8926-3aa83f2ab5c0"  # app -> Pi (write)
TX_UUID = "cdaa0003-bc5d-45ab-8926-3aa83f2ab5c0"  # Pi -> app (notify)

__all__ = ["PROTOCOL_VERSION", "SERVICE_UUID", "RX_UUID", "TX_UUID"]
