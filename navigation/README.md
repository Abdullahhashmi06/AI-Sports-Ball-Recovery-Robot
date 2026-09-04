# navigation

Autonomous navigation subsystem (Module 2): deciding where the robot should go and how to get there safely. Owned by Person 3 (Navigation & Robotics Software); interfaces with Module 1 and the state machine per `docs/TEAM_INTERFACE_CONTRACT.md`.

## Subdirectories

- `localization/` — World-frame state representation and localization container (wheel encoders, IMU)
- `planning/` — Recovery-zone configuration/selection and the navigation abstraction (paths, search, return-to-base)
- `obstacle_avoidance/` — Obstacle detection responses and dynamic rerouting

## Status

**Laptop-side software foundation implemented** (no hardware required):

- `localization/state.py` — agreed world-frame types (`WorldPoint`, `Pose`) and pure geometry, exactly per `TEAM_INTERFACE_CONTRACT.md` §6.1 (meters, negative coordinates valid, theta stored verbatim — heading convention is OD-13).
- `localization/estimator.py` — deterministic best-known-pose container with a pluggable `PoseSource` interface. No kinematics: encoder-count → meters is gated by **OD-12**, heading/IMU fusion by **OD-13**.
- `localization/telemetry.py` — raw protocol `TELE` bridge (`TelemetrySnapshot`). Encoder counts stay raw counts; nothing is converted or interpreted.
- `planning/zones.py` — the four agreed zones `WEST`/`EAST`/`NORTH`/`SOUTH` (DEC-018) with configurable, unset-by-default bounds; §6.4 search-focus classification with an explicit calibration threshold.
- `planning/interfaces.py` + `planner.py` — deterministic `NavigationInput`/`NavigationOutput`/`MovementIntent` planning (Module 2 internal). No `lin`/`ang` magnitudes are generated (**OD-07**/**OD-13**).

**Not implemented yet (open decisions / hardware-gated):** odometry integration (OD-12), IMU heading + theta convention (OD-13), obstacle avoidance (OD-02/OD-08), approach geometry (OD-01/OD-04), search patterns and the I-3/I-4 directive set (OD-03), and any speed mapping (OD-07). None of these are resolved or invented here.

Tests: `tests/test_navigation_state.py`, `test_navigation_zones.py`, `test_navigation_planning.py`, `test_navigation_telemetry.py` (fixtures in `tests/telemetry_fixtures.py` are SIMULATION / TEST ONLY).