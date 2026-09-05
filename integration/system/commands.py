"""Command boundary: movement intent → protocol command (or deferred).

Maps a navigation :class:`MovementIntent` (``navigation.planning``) onto a
laptop protocol command *request* — expressed as ``(message_type, fields)``
ready for ``LaptopLink.command(message_type, **fields)`` — or onto an
explicit non-command outcome.

The mapping is deliberately conservative:

* ``MovementKind.NONE``  → no command (``DirectiveKind.NONE``).
* ``MovementKind.STOP``  → ``CMD_STOP`` mode ``normal`` — fully expressible
  today (no physical values needed).
* ``MovementKind.MOVE``  → **deferred** (``DirectiveKind.DEFERRED``).  The
  wire command ``CMD_MOVE`` requires ``lin``/``ang`` (protocol §5), and
  mapping a world objective to normalized velocities needs the speed
  calibration of **OD-07** and the heading convention of **OD-13** — both
  open.  No values are invented; the directive carries the reason.

``STOP``/emergency payloads are validated against the protocol schema by
reusing ``integration.communication.protocol`` (no duplicated schema).
Issuing commands is NOT this module's job — it returns a directive; the
caller (e.g. the coordinator in ``coordinator.py``) applies it through
``LaptopLink`` only, per ``TEAM_INTERFACE_CONTRACT.md`` R-1/R-6 (navigation
must not bypass the communication module).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from integration.communication.protocol import (  # noqa: F401  (documented reuse)
    complete_command_payload,
    validate_command,
)
from navigation.planning.interfaces import MovementIntent, MovementKind

# Reason attached to every deferred MOVE directive.  Kept as a module
# constant so tests and logs see one canonical statement.
DEFERRED_MOVE_REASON = (
    "CMD_MOVE requires lin/ang (protocol §5); mapping world geometry to "
    "normalized velocities is gated by OD-07 (speed calibration) and "
    "OD-13 (heading convention) — deferred, no values invented"
)


class DirectiveKind(enum.Enum):
    """What the coordinator should do with a resolved directive."""

    SEND = "send"          # a valid protocol command request (type + fields)
    DEFERRED = "deferred"  # expressible as intent but not yet as a wire value
    NONE = "none"          # no command should be sent


@dataclass(frozen=True)
class CommandDirective:
    """One command-boundary decision.

    ``fields`` are the keyword arguments for
    ``LaptopLink.command(message_type, **fields)``.  For ``DEFERRED`` the
    ``message_type`` names the *eventual* command and ``fields`` is
    ``None``.
    """

    kind: DirectiveKind
    message_type: Optional[str] = None
    fields: Optional[Dict[str, Any]] = None
    reason: str = ""


def _stop_directive(mode: str, reason: str) -> CommandDirective:
    fields = {"mode": mode}
    # Fail fast on a schema bug: the directive must always be sendable.
    validate_command("CMD_STOP", complete_command_payload("CMD_STOP", fields))
    return CommandDirective(
        kind=DirectiveKind.SEND,
        message_type="CMD_STOP",
        fields=fields,
        reason=reason,
    )


def resolve_movement_command(intent: MovementIntent) -> CommandDirective:
    """Map one movement intent to a command directive (pure/deterministic)."""
    if intent.kind is MovementKind.NONE:
        return CommandDirective(
            kind=DirectiveKind.NONE,
            reason="planner produced no movement command (MovementKind.NONE)",
        )
    if intent.kind is MovementKind.STOP:
        return _stop_directive(
            "normal",
            "movement intent STOP -> CMD_STOP mode=normal (protocol §4.1)",
        )
    # MovementKind.MOVE
    return CommandDirective(
        kind=DirectiveKind.DEFERRED,
        message_type="CMD_MOVE",
        reason=DEFERRED_MOVE_REASON,
    )


def emergency_stop_directive() -> CommandDirective:
    """Emergency stop for the state-machine/safety layer (R-3, DEC-021).

    Always available regardless of motion gating — a fail-safe must never
    be blocked by link/state bookkeeping (protocol §7).
    """
    return _stop_directive(
        "emergency",
        "safety-layer fail-safe -> CMD_STOP mode=emergency (protocol §7, R-3)",
    )


__all__ = [
    "DEFERRED_MOVE_REASON",
    "CommandDirective",
    "DirectiveKind",
    "emergency_stop_directive",
    "resolve_movement_command",
]
