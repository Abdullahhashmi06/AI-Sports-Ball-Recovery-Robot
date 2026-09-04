"""Bring-up console: exercise the laptop ↔ ESP32 link on the bench.

Run against the ESP32 firmware skeleton (no motors/sensors needed):

    python -m integration.communication.demo_laptop --port COM5        # Windows
    python -m integration.communication.demo_laptop --port /dev/ttyUSB0

Requires pyserial (``pip install pyserial``).  The script performs the
protocol flows A (startup), CMD_RESET re-arm, and then prints live TELE
summaries so you can watch the ESP32's status without opening a second
serial monitor.

Nothing here drives motors: with the bring-up firmware the ESP32 reports
"not implemented" for motion/intake/dispense hardware, which is the honest
state before motors are attached.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Optional

from .laptop_link import CommandError, Esp32Lost, LaptopLink, LinkError, LinkTimeout
from .protocol import PROTOCOL_VERSION

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
LOG = logging.getLogger("demo_laptop")


def _tele_summary(msg: dict) -> str:
    enc = msg.get("enc", {})
    us = msg.get("us", [])
    mot = msg.get("mot", {})
    balls = msg.get("balls", {})
    faults = msg.get("faults", [])
    return (
        f"TELE enc=({enc.get('dl', 0):+d},{enc.get('dr', 0):+d}) "
        f"us={us} mot(pwm)={mot.get('pwm_l')}/{mot.get('pwm_r')} "
        f"balls={balls.get('count')} faults={faults}"
    )


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="serial port, e.g. COM5 or /dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=115200, help="baud rate (protocol: 115200 8N1)")
    parser.add_argument("--duration", type=float, default=10.0, help="seconds to watch telemetry")
    parser.add_argument("--debug", action="store_true", help="enable DEBUG logging (raw lines)")
    args = parser.parse_args(argv)

    if args.debug:
        logging.getLogger("integration.communication").setLevel(logging.DEBUG)

    from .transport import SerialTransport  # requires pyserial

    transport = SerialTransport(args.port, args.baud)
    link = LaptopLink(transport)
    print(f"[demo] protocol version {PROTOCOL_VERSION}, port {args.port} @ {args.baud}")
    try:
        link.start()

        # --- Flow A: startup -------------------------------------------------
        boot = link.wait_for_event("EVT_BOOT", timeout=5.0)
        if boot is None:
            print("[demo] ERROR: no EVT_BOOT within 5 s — is the ESP32 running the skeleton?")
            return 1
        print(f"[demo] EVT_BOOT received: reason={boot.get('reason')} fw={boot.get('fw')}")

        tele = link.wait_tele(timeout=5.0, after=0)
        if tele is None:
            print("[demo] ERROR: no TELE within 5 s")
            return 1
        print(f"[demo] first TELE: {_tele_summary(tele)}")

        # --- CMD_RESET scope=all: confirm the re-arm path works ---------------
        try:
            resp = link.command("CMD_RESET", scope="all")
            print(f"[demo] CMD_RESET scope=all -> {resp['type']} (ack={resp.get('ack')})")
        except CommandError as err:
            print(f"[demo] CMD_RESET rejected: {err.code} {err.message}")
        except (LinkTimeout, Esp32Lost) as err:
            print(f"[demo] CMD_RESET failed: {err}")
            return 1

        # Probe the hardware boundary honestly: motion is not implemented yet.
        try:
            resp = link.command("CMD_MOVE", lin=0.2, ang=0.0)
            print(f"[demo] CMD_MOVE accepted: {resp['type']} (skeleton reports motion? unexpected)")
        except CommandError as err:
            print(f"[demo] CMD_MOVE -> RESP_ERR {err.code}: {err.message} "
                  f"(expected on bring-up skeleton: drive hardware not attached)")
        except (LinkTimeout, Esp32Lost) as err:
            print(f"[demo] CMD_MOVE failed: {err}")
            return 1
        try:
            link.command("CMD_STOP", mode="normal")
            print("[demo] CMD_STOP normal -> RESP_OK")
        except CommandError as err:
            print(f"[demo] CMD_STOP rejected: {err.code} {err.message}")

        # --- Watch live telemetry ----------------------------------------------
        deadline = time.monotonic() + args.duration
        last_print = 0.0
        tele_seq = link.tele_seq
        print(f"[demo] watching telemetry for {args.duration:.0f} s (Ctrl-C to stop)")
        while time.monotonic() < deadline:
            msg = link.wait_tele(timeout=0.5, after=tele_seq)
            if msg is None:
                if link.frozen:
                    print("[demo] link frozen (ESP32 lost) — check the USB cable")
                    return 1
                continue
            tele_seq = link.tele_seq
            now = time.monotonic()
            if now - last_print >= 1.0:
                last_print = now
                print(f"[demo] {_tele_summary(msg)}")
        print("[demo] done — link stayed up for the full duration")
        return 0
    except KeyboardInterrupt:
        print("\n[demo] interrupted")
        return 130
    except LinkError as err:
        print(f"[demo] link error: {err}")
        return 1
    finally:
        link.stop()


if __name__ == "__main__":
    sys.exit(main())
