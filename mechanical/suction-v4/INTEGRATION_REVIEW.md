# Integration Review: V4 Mechanical Proposal

Baseline commit: 6fd5174232d4b42929f8aaf4538c736147d731a5.
Prepared 11 October 2026. Existing architecture, protocol, interface contract,
decision log and software safety constraints remain authoritative.

| Area | Existing project | V4 proposal and review consequence |
| --- | --- | --- |
| Collection | Front roller and guide plates | Suction pipe/receiver/blower; P4/P5/P1 review intake behavior and OD-04/OD-10 |
| Dispensing | One-ball servo gate; CMD_DISPENSE count=1 | Required automatic feature retained; two local servo gates and confirmation sensors proposed under OD-11 |
| Storage | Capacity/count owned by ESP32 | Initial target 10; physical capacity untested; no firmware constant changed |
| Drive | Four fixed wheels and four motors | CAD retains two driven wheels plus two casters; this remains a chassis alternative, not a silently adopted architecture change |
| Obstacle sensors | 3-4 ultrasonics; ToF optional | CAD shows two generic front envelopes; side/rear mounting and final 3-4 layout still needed under OD-08/OD-09 |
| Compute/transport | Laptop + classic ESP32; USB serial; LaptopLink boundary | Retained; no Raspberry Pi/Jetson purchase proposed |
| Ball domain | Repository's tested baseline is tennis/public sports-ball data | CAD reference is a 66 mm padel ball; confirm intended ball family and physical size range before build |
| Motion | CMD_MOVE deferred until OD-07/OD-13 resolved | Remains deferred; no actuation or calibration added |
| Automatic dispensing code | Physical servo/sensor hooks remain hardware work | This package adds CAD/specification, not working hardware firmware; P5 implementation follows OD-11 measurements |

No authoritative document has been rewritten to make the suction or two-motor
layout appear approved. The repository indexes now point to this proposal.
HARDWARE_PROCUREMENT.md remains the current buy-now authority.

For review, accept or revise the mechanical proposal first. Finalizing a chassis
change or the suction implementation then follows the existing P1/P4/P5 change
control. Publishing this proposal alone does not resolve an open decision.
