"""Laptop side of the laptop ↔ ESP32 communication link.

Implements the wire protocol specified in ``docs/COMMUNICATION_PROTOCOL.md``
(protocol version 1): newline-framed, compact JSON messages over USB serial.

This is the **bring-up skeleton**: it establishes and tests the communication
boundary only.  It contains no AI, navigation, or motor logic.  The AI,
navigation and state-machine modules will reuse ``LaptopLink`` later.

Usage::

    from integration.communication import LaptopLink
    from integration.communication.transport import SerialTransport

    link = LaptopLink(SerialTransport("/dev/ttyUSB0", 115200))
    link.start()
    link.wait_for_event("EVT_BOOT", timeout=5.0)
    resp = link.command("CMD_RESET", scope="all")   # -> RESP_OK payload
    ...
    link.stop()
"""

from .protocol import (
    PROTOCOL_VERSION,
    HEARTBEAT_INTERVAL_S,
    TELEMETRY_PERIOD_S,
    COMMAND_TIMEOUT_S,
    ESP32_LIVENESS_TIMEOUT_S,
    MAX_LINE_BYTES,
    ERROR_CODES,
    FAULT_CODES,
    LAPTOP_TO_ESP32,
    ESP32_TO_LAPTOP,
    make_message,
    encode_line,
    decode_line,
    ValidationError,
)
from .laptop_link import LaptopLink, LinkError, CommandError, LinkTimeout, Esp32Lost

__version__ = "0.1.0"

__all__ = [
    "PROTOCOL_VERSION",
    "HEARTBEAT_INTERVAL_S",
    "TELEMETRY_PERIOD_S",
    "COMMAND_TIMEOUT_S",
    "ESP32_LIVENESS_TIMEOUT_S",
    "MAX_LINE_BYTES",
    "ERROR_CODES",
    "FAULT_CODES",
    "LAPTOP_TO_ESP32",
    "ESP32_TO_LAPTOP",
    "make_message",
    "encode_line",
    "decode_line",
    "ValidationError",
    "LaptopLink",
    "LinkError",
    "CommandError",
    "LinkTimeout",
    "Esp32Lost",
]
