"""Module 2 — Autonomous Navigation & Intelligent Search (laptop side).

Repository area for the laptop-side navigation module
(``docs/ARCHITECTURE.md`` §11, ``docs/TEAM_INTERFACE_CONTRACT.md`` §3–§4,
owned by Person 3).

**Status (bring-up milestone):** this is the laptop-side *software
foundation only*.  It provides the agreed world-frame data structures
(``TEAM_INTERFACE_CONTRACT.md`` §6.1), a localization state container with
pluggable future sensor sources, a raw telemetry bridge, recovery-zone
configuration/selection, and a deterministic navigation planning
abstraction.

It deliberately does **not** implement odometry/heading integration,
obstacle avoidance, search patterns, or any lin/ang generation — those are
gated by open decisions (OD-01, OD-02, OD-03, OD-04, OD-07, OD-08, OD-12,
OD-13) and physical bring-up.  Nothing in this package invents hardware
values or resolves an open decision; see the module docstrings for what is
intentionally left out and why.
"""