"""Narrow validation boundary for synthetic perception payloads.

Validates raw JSON/dict perception payloads into structured :class:`PerceptionFrame`
objects. Fails safely on malformed payloads with :class:`PerceptionValidationError`.
Tolerates extra unknown fields per additive policy.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Mapping, Optional, Tuple

from navigation.localization.state import WorldPoint, _finite_number
from navigation.planning.interfaces import ExitHint, NavigationError, exit_hint_from_payload

from .model import BallObservation, ObstacleObservation, PerceptionFrame


class PerceptionValidationError(ValueError):
    """Raised when a perception payload violates schema or numeric validity."""


def validate_perception_payload(
    payload: Mapping[str, Any],
    *,
    default_timestamp: Optional[float] = None,
) -> PerceptionFrame:
    """Validate a raw perception payload mapping into a :class:`PerceptionFrame`.

    * Tolerates unknown extra fields.
    * Fails safely with :class:`PerceptionValidationError` on malformed inputs.
    * Preserves confidence as supplied numeric data.
    """
    if not isinstance(payload, Mapping):
        raise PerceptionValidationError("perception payload must be a mapping/dict")

    ts_raw = payload.get("timestamp")
    if ts_raw is None:
        ts = default_timestamp if default_timestamp is not None else time.monotonic()
    else:
        try:
            ts = _finite_number(ts_raw, "timestamp")
        except ValueError as exc:
            raise PerceptionValidationError(f"invalid timestamp: {exc}") from exc

    # Parse ball observation if present
    ball_obs: Optional[BallObservation] = None
    if "ball" in payload and payload["ball"] is not None:
        b_raw = payload["ball"]
        if not isinstance(b_raw, Mapping):
            raise PerceptionValidationError("ball payload must be a mapping/dict")

        visible = bool(b_raw.get("visible", False))

        pos: Optional[WorldPoint] = None
        pos_raw = b_raw.get("position")
        if pos_raw is not None:
            if isinstance(pos_raw, WorldPoint):
                pos = pos_raw
            elif isinstance(pos_raw, Mapping):
                if "x" not in pos_raw or "y" not in pos_raw:
                    raise PerceptionValidationError("ball position must contain x and y")
                try:
                    pos = WorldPoint(pos_raw["x"], pos_raw["y"])
                except ValueError as exc:
                    raise PerceptionValidationError(f"invalid ball position coordinates: {exc}") from exc
            else:
                raise PerceptionValidationError("ball position must be WorldPoint or {x, y}")

        conf_raw = b_raw.get("confidence", 1.0)
        try:
            conf = _finite_number(conf_raw, "ball confidence")
        except ValueError as exc:
            raise PerceptionValidationError(f"invalid ball confidence: {exc}") from exc

        vel: Optional[Tuple[float, float]] = None
        vel_raw = b_raw.get("velocity")
        if vel_raw is not None:
            if isinstance(vel_raw, (list, tuple)) and len(vel_raw) == 2:
                try:
                    vel = (_finite_number(vel_raw[0], "vx"), _finite_number(vel_raw[1], "vy"))
                except ValueError as exc:
                    raise PerceptionValidationError(f"invalid ball velocity: {exc}") from exc
            else:
                raise PerceptionValidationError("ball velocity must be a 2-tuple (vx, vy)")

        ball_obs = BallObservation(
            visible=visible,
            position=pos,
            velocity=vel,
            confidence=conf,
            timestamp=ts,
        )

    # Parse obstacle observations if present
    obstacles: List[ObstacleObservation] = []
    if "obstacles" in payload and payload["obstacles"] is not None:
        obs_raw_list = payload["obstacles"]
        if not isinstance(obs_raw_list, (list, tuple)):
            raise PerceptionValidationError("obstacles payload must be a list/tuple")

        for i, item in enumerate(obs_raw_list):
            if not isinstance(item, Mapping):
                raise PerceptionValidationError(f"obstacle[{i}] must be a mapping/dict")

            o_type = item.get("type", item.get("obstacle_type", "unknown"))
            if not isinstance(o_type, str):
                raise PerceptionValidationError(f"obstacle[{i}] type must be a string")

            region_raw = item.get("world_region", item.get("region", {}))
            if not isinstance(region_raw, Mapping):
                raise PerceptionValidationError(f"obstacle[{i}] world_region must be a mapping/dict")

            conf_raw = item.get("confidence", 1.0)
            try:
                conf = _finite_number(conf_raw, f"obstacle[{i}] confidence")
            except ValueError as exc:
                raise PerceptionValidationError(f"invalid obstacle[{i}] confidence: {exc}") from exc

            obstacles.append(
                ObstacleObservation(
                    obstacle_type=o_type,
                    world_region=dict(region_raw),
                    confidence=conf,
                    timestamp=ts,
                )
            )

    # Parse exit_hint if present
    hint: Optional[ExitHint] = None
    if "exit_hint" in payload and payload["exit_hint"] is not None:
        h_raw = payload["exit_hint"]
        try:
            if isinstance(h_raw, ExitHint):
                hint = h_raw
            elif isinstance(h_raw, Mapping):
                hint = exit_hint_from_payload(h_raw)
            else:
                raise PerceptionValidationError("exit_hint must be an ExitHint or mapping")
        except (NavigationError, ValueError) as exc:
            raise PerceptionValidationError(f"invalid exit_hint payload: {exc}") from exc

    metadata = {k: v for k, v in payload.items() if k not in ("timestamp", "ball", "obstacles", "exit_hint")}

    return PerceptionFrame(
        timestamp=ts,
        ball=ball_obs,
        obstacles=tuple(obstacles),
        exit_hint=hint,
        metadata=metadata,
    )


__all__ = [
    "PerceptionValidationError",
    "validate_perception_payload",
]
