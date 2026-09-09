"""Software-only application orchestrator and scenario/diagnostics foundation.

Owned by Person 1 (System Architecture & Integration Lead). Coordinates existing
components (``LaptopLink``, ``SystemCoordinator``, ``RobotSystemState``,
``ApplicationStateMachine``, ``plan_navigation``) into a unified application
layer without implementing physical hardware behavior.

Architectural boundaries preserved:
* LaptopLink owns transport and frame decoding.
* RobotSystemState owns raw authoritative telemetry and localization state.
* ApplicationStateMachine owns lifecycle, safety reflection, and mission labels.
* plan_navigation owns hardware-free spatial planning.
* resolve_movement_command / SystemCoordinator owns command gating and boundary.
* ApplicationOrchestrator coordinates the pipeline and provides observability.

Safety invariant:
All OD-01 through OD-16 remain strictly OPEN. CMD_MOVE generation stays
DEFERRED in the command boundary (OD-07/OD-13 open). Zero CMD_MOVE commands
reach the wire.
"""

from __future__ import annotations

import enum
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from integration.communication.laptop_link import LaptopLink
from integration.system.commands import CommandDirective, DirectiveKind, emergency_stop_directive
from integration.system.coordinator import ControlTick, CoordinatorStatus, MotionGated, SystemCoordinator
from integration.system.state_machine import (
    ApplicationStateMachine,
    LifecycleState,
    MissionState,
    SafetyCondition,
    TransitionResult,
)
from navigation.localization.state import Pose, WorldPoint
from navigation.planning.interfaces import (
    ExitHint,
    MovementKind,
    NavigationOutput,
    NavigationStatus,
    Objective,
    exit_hint_from_payload,
)
from integration.perception.model import PerceptionFrame
from integration.perception.seams import derive_objective_from_perception
from integration.perception.validation import validate_perception_payload
from navigation.planning.zones import ZoneId

LOGGER = logging.getLogger("integration.system.application")


class TaskType(enum.Enum):
    """High-level declarative task specification (WHAT the robot should do).

    Deliberately describes high-level intent without physical velocities, PWM,
    acceleration, braking, or wheel control.
    """

    HOLD = "hold"
    GO_TO_TARGET = "go_to_target"
    SEARCH_ZONE = "search_zone"
    APPROACH_TARGET = "approaching_target"
    COLLECT_BALL = "collecting_ball"
    RETURN_TO_BASE = "returning_to_base"
    NO_OBJECTIVE = "no_objective"


@dataclass(frozen=True)
class TaskObjective:
    """Declarative representation of a task or goal.

    * ``objective_type`` — task type classification.
    * ``target`` — optional world point goal (e.g. ball, base, or anchor point).
    * ``zone`` — optional recovery zone.
    * ``source`` — provenance label (e.g. "mission_seam", "user", "perception_exit_hint").
    * ``metadata`` — arbitrary non-safety operational metadata.
    """

    objective_type: TaskType
    target: Optional[WorldPoint] = None
    zone: Optional[ZoneId] = None
    source: str = "declarative"
    metadata: Dict[str, Any] = field(default_factory=dict)


def derive_objective(
    mission: MissionState,
    *,
    target: Optional[WorldPoint] = None,
    zone: Optional[ZoneId] = None,
    hint: Optional[ExitHint] = None,
    base_location: Optional[WorldPoint] = None,
) -> TaskObjective:
    """Conservative seam connecting ApplicationStateMachine mission context to a TaskObjective.

    Rules & Open Decision Gating:
    * IDLE -> TaskType.HOLD
    * NAVIGATING -> TaskType.GO_TO_TARGET (if target set) or SEARCH_ZONE (if zone set), else NO_OBJECTIVE
    * SEARCHING ->
        - If ExitHint supplied: SEARCH_ZONE using hint.exit_zone (and hint.estimated_position if available).
        - If zone supplied: SEARCH_ZONE using zone.
        - Else: TaskType.NO_OBJECTIVE (Documented: OD-03 blocks generating an implicit wire-level search directive).
    * APPROACHING ->
        - If target supplied: TaskType.APPROACH_TARGET.
        - Else: TaskType.NO_OBJECTIVE (Documented: OD-01 ball world position / approach threshold open).
    * COLLECTING ->
        - TaskType.NO_OBJECTIVE (Documented: OD-04 intake/approach pose open).
    * RETURNING ->
        - If base_location supplied: TaskType.RETURN_TO_BASE.
        - Else: TaskType.NO_OBJECTIVE.
    """
    if not isinstance(mission, MissionState):
        raise TypeError(f"mission must be a MissionState, got {type(mission).__name__}")

    if mission is MissionState.IDLE:
        return TaskObjective(TaskType.HOLD, source="mission_seam:idle")

    if mission is MissionState.NAVIGATING:
        if target is not None:
            return TaskObjective(TaskType.GO_TO_TARGET, target=target, zone=zone, source="mission_seam:navigating")
        if zone is not None:
            return TaskObjective(TaskType.SEARCH_ZONE, zone=zone, source="mission_seam:navigating")
        return TaskObjective(TaskType.NO_OBJECTIVE, source="mission_seam:navigating_unspecified")

    if mission is MissionState.SEARCHING:
        if hint is not None:
            return TaskObjective(
                TaskType.SEARCH_ZONE,
                target=hint.estimated_position,
                zone=hint.exit_zone,
                source="perception_exit_hint",
                metadata={"confidence": hint.confidence},
            )
        if zone is not None:
            return TaskObjective(TaskType.SEARCH_ZONE, zone=zone, source="mission_seam:searching")
        # OD-03 blocks inventing a wire-level search pattern
        return TaskObjective(
            TaskType.NO_OBJECTIVE,
            source="mission_seam:searching_od03_blocked",
            metadata={"note": "OD-03 blocks search directive generation without zone or perception hint"},
        )

    if mission is MissionState.APPROACHING:
        if target is not None:
            return TaskObjective(TaskType.APPROACH_TARGET, target=target, source="mission_seam:approaching")
        return TaskObjective(
            TaskType.NO_OBJECTIVE,
            source="mission_seam:approaching_od01_blocked",
            metadata={"note": "OD-01 ball position / approach threshold open"},
        )

    if mission is MissionState.COLLECTING:
        return TaskObjective(
            TaskType.NO_OBJECTIVE,
            source="mission_seam:collecting_od04_blocked",
            metadata={"note": "OD-04 intake/approach pose open"},
        )

    if mission is MissionState.RETURNING:
        if base_location is not None:
            return TaskObjective(
                TaskType.RETURN_TO_BASE,
                target=base_location,
                source="mission_seam:returning",
            )
        return TaskObjective(TaskType.NO_OBJECTIVE, source="mission_seam:returning_no_base")

    return TaskObjective(TaskType.NO_OBJECTIVE, source="mission_seam:unknown")


@dataclass(frozen=True)
class ApplicationEvent:
    """Lightweight in-memory structured event for auditing and diagnostics."""

    timestamp: float
    category: str
    event_type: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "category": self.category,
            "event_type": self.event_type,
            "details": self.details,
        }

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict())


class StructuredEventLogger:
    """Stdlib-only, deterministic in-memory event logger."""

    def __init__(self, max_events: int = 1000) -> None:
        self.max_events = max_events
        self._events: List[ApplicationEvent] = []
        self._counters: Dict[str, int] = {
            "telemetry": 0,
            "faults": 0,
            "commands_sent": 0,
            "commands_gated": 0,
            "commands_deferred": 0,
            "estop": 0,
        }

    @property
    def events(self) -> Tuple[ApplicationEvent, ...]:
        return tuple(self._events)

    @property
    def counters(self) -> Dict[str, int]:
        return dict(self._counters)

    def log(
        self,
        category: str,
        event_type: str,
        details: Optional[Dict[str, Any]] = None,
        *,
        timestamp: Optional[float] = None,
    ) -> ApplicationEvent:
        ts = timestamp if timestamp is not None else time.monotonic()
        evt = ApplicationEvent(ts, category, event_type, details or {})
        self._events.append(evt)
        if len(self._events) > self.max_events:
            self._events.pop(0)

        # Counter bookkeeping
        if category == "telemetry":
            self._counters["telemetry"] += 1
        elif category == "fault":
            self._counters["faults"] += 1
        elif category == "command":
            if event_type == "COMMAND_SENT":
                self._counters["commands_sent"] += 1
            elif event_type == "COMMAND_GATED":
                self._counters["commands_gated"] += 1
            elif event_type == "COMMAND_DEFERRED":
                self._counters["commands_deferred"] += 1
        elif category == "safety" and "STOP" in event_type:
            self._counters["estop"] += 1

        return evt

    def clear(self) -> None:
        self._events.clear()
        for k in self._counters:
            self._counters[k] = 0

    def to_jsonl(self) -> str:
        return "\n".join(e.to_jsonl() for e in self._events)


@dataclass(frozen=True)
class ApplicationStatus:
    """Unified application observability snapshot."""

    coordinator_status: CoordinatorStatus
    lifecycle: LifecycleState
    safety: SafetyCondition
    mission: MissionState
    usable: bool
    task_objective: TaskObjective
    last_navigation_status: Optional[NavigationStatus]
    last_movement_kind: Optional[MovementKind]
    last_directive_kind: Optional[DirectiveKind]
    motion_gated: bool
    gate_reason: Optional[str]
    event_counts: Dict[str, int]


class ApplicationOrchestrator:
    """Application-level orchestrator coordinating existing architecture components."""

    def __init__(
        self,
        link: LaptopLink,
        *,
        zones: Tuple[Any, ...] = (),
        useful_confidence_threshold: Optional[float] = None,
        machine: Optional[ApplicationStateMachine] = None,
        event_logger: Optional[StructuredEventLogger] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.coordinator = SystemCoordinator(
            link,
            zones=zones,
            useful_confidence_threshold=useful_confidence_threshold,
            machine=machine,
            logger=logger,
        )
        self.event_logger = event_logger if event_logger is not None else StructuredEventLogger()
        self.current_task: Optional[TaskObjective] = None
        self.current_perception: Optional[PerceptionFrame] = None
        self.current_hint: Optional[ExitHint] = None
        self.base_location: Optional[WorldPoint] = None
        self._last_tick: Optional[ControlTick] = None

        self.event_logger.log(
            "lifecycle",
            "ORCHESTRATOR_INITIALIZED",
            {"started": link.started, "lifecycle": self.coordinator.machine.lifecycle.value},
        )

    # -- Properties & state access -------------------------------------------

    @property
    def link(self) -> LaptopLink:
        return self.coordinator.link

    @property
    def machine(self) -> ApplicationStateMachine:
        return self.coordinator.machine

    @property
    def state(self) -> Any:
        return self.coordinator.state

    # -- Ingestion & Perception ---------------------------------------------

    def ingest_perception(self, payload: Dict[str, Any]) -> PerceptionFrame:
        """Validate and ingest a raw synthetic perception payload mapping."""
        frame = validate_perception_payload(payload)
        self.current_perception = frame
        if frame.exit_hint is not None:
            self.current_hint = frame.exit_hint
        self.event_logger.log(
            "perception",
            "PERCEPTION_FRAME_INGESTED",
            {
                "has_ball": frame.ball is not None and frame.ball.visible,
                "num_obstacles": len(frame.obstacles),
                "has_exit_hint": frame.exit_hint is not None,
            },
        )
        return frame

    def clear_perception(self) -> None:
        self.current_perception = None
        self.event_logger.log("perception", "PERCEPTION_CLEARED")

    def handle_message(self, msg: Dict[str, Any]) -> None:
        """Ingest a protocol message in-process, updating state and event log."""
        mtype = msg.get("type") if isinstance(msg, dict) else None
        initial_faults = self.coordinator.state.faults

        self.coordinator.ingest_message(msg)

        if mtype == "TELE":
            self.event_logger.log(
                "telemetry",
                "TELEMETRY_RECEIVED",
                {
                    "seq": msg.get("seq"),
                    "has_faults": self.coordinator.state.has_faults,
                    "faults": self.coordinator.state.faults,
                },
            )
            if self.coordinator.state.has_faults and self.coordinator.state.faults != initial_faults:
                self.event_logger.log(
                    "fault",
                    "ESP32_FAULT_OBSERVED",
                    {"faults": self.coordinator.state.faults},
                )
        elif mtype == "EVT_BOOT":
            self.event_logger.log(
                "lifecycle",
                "BOOT_OBSERVED",
                {"fw": msg.get("fw"), "reason": msg.get("reason")},
            )
        elif mtype is not None:
            self.event_logger.log("telemetry", "MESSAGE_INGESTED", {"type": mtype})

    def set_exit_hint(self, payload: Dict[str, Any]) -> ExitHint:
        """Ingest Module 1 exit hint payload (I-1, frozen per §6.4)."""
        hint = exit_hint_from_payload(payload)
        self.current_hint = hint
        self.event_logger.log(
            "perception",
            "EXIT_HINT_RECEIVED",
            {
                "zone": hint.exit_zone.value,
                "position": {"x": hint.estimated_position.x, "y": hint.estimated_position.y},
                "confidence": hint.confidence,
            },
        )
        return hint

    def clear_exit_hint(self) -> None:
        self.current_hint = None
        self.event_logger.log("perception", "EXIT_HINT_CLEARED")

    def set_base_location(self, base: WorldPoint) -> None:
        if not isinstance(base, WorldPoint):
            raise TypeError(f"base_location must be a WorldPoint, got {type(base).__name__}")
        self.base_location = base
        self.event_logger.log(
            "task",
            "BASE_LOCATION_SET",
            {"base": {"x": base.x, "y": base.y}},
        )

    def set_pose(self, pose: Pose, source: str = "external", at: Optional[float] = None) -> None:
        self.coordinator.state.set_pose(pose, source=source, at=at)
        self.event_logger.log(
            "localization",
            "POSE_UPDATED",
            {"source": source, "x": pose.x, "y": pose.y, "theta": pose.theta},
        )

    def clear_pose(self) -> None:
        self.coordinator.state.clear_pose()
        self.event_logger.log("localization", "POSE_CLEARED")

    # -- Lifecycle & Mission -------------------------------------------------

    def complete_startup(self) -> TransitionResult:
        res = self.coordinator.complete_startup()
        self.event_logger.log(
            "lifecycle",
            "STARTUP_COMPLETE_ATTEMPTED",
            {"ok": res.ok, "reason": res.reason, "lifecycle": res.state.lifecycle.value},
        )
        return res

    def set_mission(self, mission: MissionState) -> TransitionResult:
        res = self.coordinator.machine.request_mission(mission)
        self.event_logger.log(
            "mission",
            "MISSION_CHANGED",
            {"ok": res.ok, "reason": res.reason, "mission": res.state.mission.value},
        )
        return res

    def set_task(self, task: TaskObjective) -> None:
        if not isinstance(task, TaskObjective):
            raise TypeError(f"task must be a TaskObjective, got {type(task).__name__}")
        self.current_task = task
        self.event_logger.log(
            "task",
            "EXPLICIT_TASK_SET",
            {
                "type": task.objective_type.value,
                "source": task.source,
                "target": {"x": task.target.x, "y": task.target.y} if task.target else None,
                "zone": task.zone.value if task.zone else None,
            },
        )

    # -- Tick & Command Execution -------------------------------------------

    def tick(
        self,
        *,
        telemetry_max_age_s: Optional[float] = None,
        now: Optional[float] = None,
    ) -> ControlTick:
        """Run one planning + directive pass using the current task/mission objective."""
        # Derive or use explicit objective
        if self.current_task is not None:
            active_objective = self.current_task
        elif self.current_perception is not None:
            active_objective = derive_objective_from_perception(
                self.current_perception,
                self.coordinator.machine.mission,
                base_location=self.base_location,
            )
        else:
            active_objective = derive_objective(
                self.coordinator.machine.mission,
                hint=self.current_hint,
                base_location=self.base_location,
            )

        tick = self.coordinator.control_tick(
            target=active_objective.target,
            zone=active_objective.zone,
            hint=self.current_hint,
            telemetry_max_age_s=telemetry_max_age_s,
            now=now,
        )
        self._last_tick = tick

        self.event_logger.log(
            "planning",
            "TICK_PROCESSED",
            {
                "objective_type": active_objective.objective_type.value,
                "nav_status": tick.nav_output.status.value,
                "movement_kind": tick.nav_output.movement.kind.value,
                "directive_kind": tick.directive.kind.value,
                "gated": tick.gated,
                "gate_reason": tick.gate_reason,
            },
        )
        if tick.directive.kind is DirectiveKind.DEFERRED:
            self.event_logger.log(
                "command",
                "COMMAND_DEFERRED",
                {"message_type": tick.directive.message_type, "reason": tick.directive.reason},
            )
        elif tick.gated and tick.directive.kind is DirectiveKind.SEND:
            self.event_logger.log(
                "command",
                "COMMAND_GATED",
                {"message_type": tick.directive.message_type, "reason": tick.gate_reason},
            )

        return tick

    def apply_tick(
        self,
        tick: Optional[ControlTick] = None,
        *,
        timeout: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Apply a computed ControlTick directive through SystemCoordinator.apply()."""
        t = tick or self._last_tick
        if t is None:
            raise RuntimeError("No ControlTick available to apply (call tick() first)")

        if t.gated and t.directive.kind is DirectiveKind.SEND:
            is_emergency = (
                t.directive.message_type == "CMD_STOP"
                and (t.directive.fields or {}).get("mode") == "emergency"
            )
            if not is_emergency:
                self.event_logger.log(
                    "command",
                    "COMMAND_GATED",
                    {"message_type": t.directive.message_type, "reason": t.gate_reason},
                )
                raise MotionGated(f"normal motion gated: {t.gate_reason}")

        resp = self.coordinator.apply(t.directive, timeout=timeout)
        if resp is not None:
            self.event_logger.log(
                "command",
                "COMMAND_SENT",
                {
                    "message_type": t.directive.message_type,
                    "fields": t.directive.fields,
                    "response_type": resp.get("type"),
                },
            )
        return resp

    # -- Safety & Stop Operations -------------------------------------------

    def request_stop(self) -> TransitionResult:
        res = self.coordinator.machine.request_stop()
        self.event_logger.log("safety", "NORMAL_STOP_REQUESTED", {"ok": res.ok, "reason": res.reason})
        return res

    def emergency_stop(self, cause: str = "emergency stop requested") -> TransitionResult:
        res = self.coordinator.machine.emergency_stop(cause=cause)
        directive = emergency_stop_directive()
        try:
            self.coordinator.apply(directive)
        except Exception as exc:
            LOGGER.warning("Emergency stop directive application failed: %s", exc)
        self.event_logger.log("safety", "EMERGENCY_STOP", {"cause": cause, "reason": res.reason})
        return res

    def esp32_reset_all(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        resp = self.coordinator.esp32_reset_all(timeout=timeout)
        self.event_logger.log(
            "safety",
            "ESP32_RESET_ALL",
            {"response_type": resp.get("type"), "safety": self.coordinator.machine.safety.value},
        )
        return resp

    def reset_local_application(self) -> TransitionResult:
        """Reset local application state. Does NOT clear ESP32 physical fault latch."""
        res = self.coordinator.machine.reset()
        self.event_logger.log(
            "lifecycle",
            "LOCAL_APPLICATION_RESET",
            {"ok": res.ok, "reason": res.reason, "safety": res.state.safety.value},
        )
        return res

    # -- Status & Diagnostics ------------------------------------------------

    def status(
        self,
        *,
        telemetry_max_age_s: Optional[float] = None,
        now: Optional[float] = None,
    ) -> ApplicationStatus:
        coord_stat = self.coordinator.status(telemetry_max_age_s=telemetry_max_age_s, now=now)
        active_task = self.current_task or derive_objective(
            self.coordinator.machine.mission,
            hint=self.current_hint,
            base_location=self.base_location,
        )

        nav_status = self._last_tick.nav_output.status if self._last_tick else None
        m_kind = self._last_tick.nav_output.movement.kind if self._last_tick else None
        d_kind = self._last_tick.directive.kind if self._last_tick else None

        return ApplicationStatus(
            coordinator_status=coord_stat,
            lifecycle=coord_stat.lifecycle,
            safety=coord_stat.safety,
            mission=coord_stat.mission,
            usable=coord_stat.usable,
            task_objective=active_task,
            last_navigation_status=nav_status,
            last_movement_kind=m_kind,
            last_directive_kind=d_kind,
            motion_gated=coord_stat.motion_gate_reason is not None,
            gate_reason=coord_stat.motion_gate_reason,
            event_counts=self.event_logger.counters,
        )


__all__ = [
    "ApplicationEvent",
    "ApplicationOrchestrator",
    "ApplicationStatus",
    "StructuredEventLogger",
    "TaskObjective",
    "TaskType",
    "derive_objective",
]
