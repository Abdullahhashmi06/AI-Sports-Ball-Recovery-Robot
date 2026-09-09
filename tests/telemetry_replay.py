"""SIMULATION / TEST ONLY — deterministic telemetry replay + scenario helpers.

Feeds *already-decoded* protocol frames through ``SystemCoordinator.
ingest_message`` — the same intake path ``LaptopLink`` uses for real data
(``TELE`` → structural guard → ``_on_tele`` → ``from_telemetry`` →
``RobotSystemState``/state-machine reflection; ``EVT_BOOT`` → boot
observation).  Nothing here bypasses ``RobotSystemState`` or ``LaptopLink``:
commands still flow only through ``coordinator.apply()``.

Every value below is **synthetic test data**, never a physical measurement:
raw encoder counts stay raw counts, ultrasonic/IMU values are preserved
verbatim, and nothing is converted to meters or heading (OD-12/OD-13 remain
open).  Frames are replayed synchronously in the caller's thread so tests
are deterministic (no wall-clock timing assumptions).
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from integration.communication.protocol import make_message
from integration.system.coordinator import SystemCoordinator

from telemetry_fixtures import sample_telemetry


def boot_frame(
    seq: int,
    *,
    reason: str = "poweron",
    fw: str = "replay-sim-0.0",
    ts: int = 100,
) -> Dict[str, Any]:
    """One synthetic ``EVT_BOOT`` frame (SIMULATION / TEST ONLY)."""
    return make_message("EVT_BOOT", seq, {"reason": reason, "fw": fw}, ts=ts)


def tele_frame(
    seq: int,
    *,
    ts: int = 200,
    **overrides: Any,
) -> Dict[str, Any]:
    """One synthetic protocol-v1 ``TELE`` frame (SIMULATION / TEST ONLY).

    ``overrides`` are passed to ``telemetry_fixtures.sample_telemetry`` (e.g.
    ``faults=(6,)``, ``enc_dl=…``, ``us=(…)``) — values are preserved
    verbatim by the replay path.
    """
    return sample_telemetry(seq=seq, ts=ts, **overrides)


def run_frames(coordinator: SystemCoordinator, frames: Sequence[Dict[str, Any]]) -> None:
    """Replay decoded frames synchronously through the coordinator intake.

    Deterministic ordering: each frame takes effect before the next call
    returns (no threads, no sleeps, no wall-clock timing).
    """
    for frame in frames:
        coordinator.ingest_message(frame)


def startup_to_ready(
    coordinator: SystemCoordinator,
    *,
    boot_seq: int = 1,
    tele_seq: int = 2,
) -> Dict[str, Any]:
    """Replay ``EVT_BOOT`` + one healthy TELE frame, then advance the
    lifecycle via ``complete_startup()``.

    Returns the last TELE frame so tests can assert on the raw values that
    reached the state hub.  Raises if the lifecycle cannot advance (the
    caller then knows the scenario precondition failed).
    """
    run_frames(coordinator, [boot_frame(boot_seq), tele_frame(tele_seq)])
    result = coordinator.complete_startup()
    if not result.ok:
        raise AssertionError(f"startup_to_ready failed: {result.reason}")
    return coordinator.state.snapshot  # type: ignore[return-value]


def with_extra_field(msg: Dict[str, Any], key: str, value: Any) -> Dict[str, Any]:
    """Return a copy of ``msg`` with an extra (unknown) field added.

    Used to prove additive tolerance — unknown fields create no behaviour.
    """
    copy = dict(msg)
    copy[key] = value
    return copy


__all__ = [
    "boot_frame",
    "run_frames",
    "startup_to_ready",
    "tele_frame",
    "with_extra_field",
]
