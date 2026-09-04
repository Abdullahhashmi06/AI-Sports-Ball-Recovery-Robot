"""Byte/line transports for the laptop ↔ ESP32 link.

The link layer (``laptop_link.py``) only depends on the small
:class:`Transport` interface, so tests can use the in-memory
:func:`create_memory_pair` instead of a real serial port.
"""

from __future__ import annotations

import queue
import threading
from typing import Optional, Tuple


class Transport:
    """Minimal line-oriented duplex transport used by :class:`LaptopLink`.

    Lines are newline-terminated text; implementations are responsible for
    framing on the wire (USB serial is already line-based at 115200 8N1 per
    protocol §2).
    """

    def send_text(self, line: str) -> None:
        """Send one complete line (a trailing newline is added by the impl)."""
        raise NotImplementedError

    def recv_text(self, timeout: Optional[float] = None) -> Optional[str]:
        """Receive one line, or ``None`` on timeout/close.

        ``timeout=None`` blocks until a line is available.
        """
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class SerialTransport(Transport):
    """USB serial transport (pyserial).

    pyserial is imported lazily so that pure protocol tests and non-serial
    users never need it installed.  Install with::

        pip install pyserial
    """

    def __init__(self, port: str, baudrate: int = 115200) -> None:
        self.port = port
        self.baudrate = baudrate
        self._serial = None

    # -- Transport ---------------------------------------------------------
    def open(self) -> None:
        try:
            import serial  # lazy: only needed when talking to real hardware
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "pyserial is required for USB serial. Install it with "
                "'pip install pyserial'."
            ) from exc
        # 115200 8N1 per protocol §2; write timeout so send_text never hangs.
        self._serial = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.5,
            write_timeout=1.0,
        )

    def send_text(self, line: str) -> None:
        if self._serial is None:
            raise RuntimeError("transport not open")
        self._serial.write(line.encode("utf-8") + b"\n")

    def recv_text(self, timeout: Optional[float] = None) -> Optional[str]:
        if self._serial is None:
            return None
        if timeout is not None:
            self._serial.timeout = timeout
        raw = self._serial.readline()  # returns b"" on timeout
        if not raw:
            return None
        text = raw.decode("utf-8", errors="replace")
        if text.endswith("\n"):
            text = text[:-1]
        if text.endswith("\r"):
            text = text[:-1]
        return text

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None


class _MemoryEnd(Transport):
    """One end of an in-memory duplex pair (used by the test harness)."""

    def __init__(
        self,
        name: str,
        inbox: "queue.Queue[Optional[str]]",
        outbox: "queue.Queue[Optional[str]]",
    ) -> None:
        self.name = name
        self._inbox = inbox   # lines addressed TO this end
        self._outbox = outbox  # lines sent FROM this end
        self._closed = False

    def send_text(self, line: str) -> None:
        if self._closed:
            raise RuntimeError(f"{self.name}: transport closed")
        self._outbox.put(line)

    def recv_text(self, timeout: Optional[float] = None) -> Optional[str]:
        try:
            return self._inbox.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._closed = True
        self._inbox.put(None)  # unblock a pending blocking recv


def create_memory_pair() -> Tuple[Transport, Transport]:
    """Create a connected in-memory transport pair (a, b).

    Lines written to ``a`` are received by ``b`` and vice versa.  Used by the
    unit tests to exercise the full protocol without any hardware.
    """
    q_l: "queue.Queue[Optional[str]]" = queue.Queue()  # -> laptop (a)
    q_e: "queue.Queue[Optional[str]]" = queue.Queue()  # -> esp32 (b)
    a = _MemoryEnd("laptop", inbox=q_l, outbox=q_e)
    b = _MemoryEnd("esp32", inbox=q_e, outbox=q_l)
    return a, b
