"""Laptop-side integration package.

Owned by Person 1 (System Architecture & Integration Lead).  This package
holds the laptop-side pieces that connect the AI / navigation modules to the
ESP32: first of all the laptop ↔ ESP32 communication link
(``integration.communication``), later the system state machine and
bring-up/demo tooling.

Subpackages must stay importable with the repository root on ``sys.path``
(e.g. running ``python -m unittest`` from the repository root).
"""
