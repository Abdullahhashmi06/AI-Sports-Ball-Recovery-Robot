"""Integration foundation (laptop side, owned by Person 1).

Laptop-side pieces that connect the AI / navigation modules to the ESP32
link — the communication layer already exists under
``integration.communication``; this package adds the thin layer *between*
the modules and that link:

* ``state.py``        — :class:`RobotSystemState`, the laptop's raw,
                        ESP32-authoritative picture of the robot (telemetry
                        hub + localization container);
* ``commands.py``     — the command boundary: deterministic mapping of a
                        navigation movement intent onto a protocol command
                        (or an explicit deferred outcome where open
                        decisions block a wire value);
* ``coordinator.py``  — thin ``SystemCoordinator`` wiring the link into the
                        state hub, owning an ``ApplicationStateMachine`` as
                        the laptop policy layer (boot observation, ESP32
                        fault reflection, machine-aware motion gate,
                        ``complete_startup``, ``esp32_reset_all``), and
                        running the telemetry → navigation → command
                        pipeline as one testable ``control_tick``;
* ``state_machine.py`` — pure application-lifecycle skeleton: three
                        explicit axes (lifecycle / safety reflection /
                        mission context) so future perception, navigation,
                        collection and hardware work plugs in without
                        architectural rewrites.  No behaviour, no commands.

Design constraints honoured here (per ``docs/ARCHITECTURE.md``,
``docs/COMMUNICATION_PROTOCOL.md`` and ``docs/TEAM_INTERFACE_CONTRACT.md``):

* no AI/perception, obstacle avoidance, search directives, or physical
  behavior — anything gated by OD-01…OD-16 stays absent;
* the communication module (``LaptopLink``) remains the only transport;
  this layer never writes to serial or re-implements framing/validation;
* no hardware values, calibrations, or thresholds are invented.
"""

from .application import (
    ApplicationEvent,
    ApplicationOrchestrator,
    ApplicationStatus,
    StructuredEventLogger,
    TaskObjective,
    TaskType,
    derive_objective,
)
from .commands import CommandDirective, DirectiveKind
from .coordinator import ControlTick, CoordinatorStatus, MotionGated, SystemCoordinator
from .state import RobotSystemState
from .state_machine import (
    ApplicationStateMachine,
    ApplicationStateView,
    LifecycleState,
    MissionState,
    SafetyCondition,
    TransitionResult,
)

__all__ = [
    "ApplicationEvent",
    "ApplicationOrchestrator",
    "ApplicationStateMachine",
    "ApplicationStateView",
    "ApplicationStatus",
    "CommandDirective",
    "ControlTick",
    "CoordinatorStatus",
    "DirectiveKind",
    "LifecycleState",
    "MissionState",
    "MotionGated",
    "RobotSystemState",
    "SafetyCondition",
    "StructuredEventLogger",
    "SystemCoordinator",
    "TaskObjective",
    "TaskType",
    "TransitionResult",
    "derive_objective",
]
