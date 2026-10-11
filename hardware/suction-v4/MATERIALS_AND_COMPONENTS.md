# V4 Materials and Component Specification

Review draft, 11 October 2026. This is a proposed construction BOM, not an approved
shopping list. Existing HARDWARE_PROCUREMENT.md and HARDWARE_BRINGUP_PLAN.md govern
current spending. Prices, supplier stock and purchased components are unconfirmed.

[CSV BOM](BOM.csv) lists assembly quantities, materials, CAD dimensions, status,
owners and required measurements. Quantity means parts per proposed robot, not
raw stock cut length or a manufacturing nesting plan. CAD colors are illustrative
and do not encode material grades.

## Proposed Materials

| Material | Intended use | Reason and qualification |
| --- | --- | --- |
| PVC-U smooth-bore tube/fittings | Main suction pipe and rear outlet where suitable stock exists | Candidate for a smooth ball path. Confirm actual inside diameter, bend radius and external-pressure suitability with supplier; nominal pipe size is not bore size. Source M3. |
| PETG | Prototype nozzle, custom bends, non-load-critical mounts | Accessible printed prototype material; seal layer leakage and smooth ball-contact seams. Printed pressure parts need validation; they are not automatically vacuum-rated. Source M4. |
| TPU around 95A | Flexible pickup lip and non-marking bumper prototypes | Candidate flexible printing material; geometry and hardness need court/ball trials. Use sourced elastomer for final sealing where prints leak. Source M4. |
| 6061-T6 aluminium | Chassis plate, supports, brackets | Structural stock candidate. CAD chassis is 6 mm; rails are solid envelopes, not weight-optimized sections. Verify mass, deflection and joints. Source M1. |
| 5052-H32 aluminium sheet | Sealed receiver, lid and hopper | Formable corrosion-resistant sheet candidate. CAD receiver wall is 4 mm and lid 5 mm; hopper roughly 4 mm envelope. Final thickness/stiffeners require vacuum and mass checks. Source M2. |
| POM-C/acetal | Sliding gate plates and wear guides | Machinable, dimensionally stable, wear-resistant material candidate. CAD gates are 4 mm; add tested edge profile and guides before manufacture. Source M5. |
| Solid silicone or suitable EPDM seal stock | Lid, pipe and gate seals | Grade/compression/joint design must suit air service and temperatures. Do not assume printed TPU or open-cell foam is an airtight substitute. Source M6. |
| Closed-cell elastomer padding | Impact liner and contact bumpers | Candidate cushioning; verify non-marking behavior, retention and debris shedding. |
| 304 stainless steel | Perforated retention screen, shafts/fasteners where appropriate | Proposed wear/corrosion-resistant hardware; screen is not a dust filter. Finish edges to avoid ball damage. |
| Copper conductors with suitable insulation | Power and signal wiring | Gauge, connector current rating and protection follow measured load, voltage drop and installation conditions. |

Material choices are engineering proposals, not claims that these exact products
have been tested in this robot. No full robot mass is derived from the CAD:
supplier blocks, solid rail envelopes and support shapes are not mass models.

## Mechanical Subsystems

**Intake:** 100 mm clear main bore, 140 x 90 mm internal mouth, two proposed
200 mm-radius 60-degree bends. Buy fittings only after measuring the real bore;
stock angles/OD may differ from the CAD. Use removable joints for clearing jams.

**Receiver:** 360 x 230 x 216 mm shell raised above the dispenser, with sealed
lid, protected air outlet, filter and soft impact liner. Pressure difference,
panel buckling, weld/adhesive joints and leakage must be checked on the bench.
Do not replace it with an open basket while expecting the same suction path.

**Dispenser:** sloped hopper, two gate cassettes, two position servos, two slide
plates, two face seals, 76 mm bore metering chamber, rear curved outlet,
gate-position switches, pocket and exit detection. Additional normally closed
vent valve and pressure sensor allow verified equalization before dispensing.
See the [cycle and failure tests](../../mechanical/suction-v4/AUTOMATIC_DISPENSING.md).

**Chassis:** two drive motors and two front casters in this CAD alternative.
The project baseline uses four motors; that difference is expressly pending
review. Raised storage and rear overhang need full/empty tipping and stopping
tests. The 150 mm wheel size is a proposed CAD dimension, not an odometry constant.

## Electrical and Purchased Parts

| Component | Required specification to obtain | Selection status |
| --- | --- | --- |
| Blower | Measured airflow-pressure operating point with pipe, dirty filter and full receiver; supply voltage, current, noise and duty rating | Supplier model and ratings TBD; no cooling-fan substitution assumed |
| Drive motors with encoders | Loaded torque, RPM/gearing, stall/current limits, encoder interface | Wait for chassis mass, court resistance and OD-07/OD-12 |
| Drive/blower drivers | Load voltage; continuous/transient current; fault protection; startup-safe enable | Match measured/selected load; no L298N/BTS7960 model assumed |
| Two position servos | Full 94 mm gate stroke via verified linkage; loaded force/torque; travel; supply range; peak/stall current | Metal gearing is a candidate; no generic torque value or SG90 substitution assumed |
| Servo power regulator | Servos' required voltage and combined transient/blocked-load demand; separate load rail from controller power | Size after actuator selection; ESP32/USB power is not the servo supply |
| Gate/pocket/exit sensors | Four gate-position signals plus pocket, exit and collection verification; compatible logic or level conversion | Type, debounce, timeouts and placement need OD-10/OD-11 trials |
| Vent and pressure sensor | Fail-closed vent behavior; adequate equalization; pressure range matched to blower and overload protection | Proposed additions; pins/thresholds TBD |
| ESP32, MPU6050, webcam, ultrasonics | Existing classic ESP32 target, laptop camera, MPU6050 and 3-4 ultrasonic baseline | Existing approved bench classes; follow procurement/bring-up documents |
| Battery, BMS, charger, regulators | Chosen rail voltage; sustained/peak load and runtime; chemistry-compatible charger/protection | Final chemistry, capacity and voltage deferred |
| E-stop and power protection | Physical drive/blower stop, servo/gate safe state, fuse, rated wiring/connectors and safe re-arm | OD-14 bench verification; mechanical closure not guaranteed by hobby servo power loss |

Servo torque follows measured gate force and linkage radius, including efficiency
and margin. Driver and regulator ratings must account for blocked/startup demand,
not only free-running current. Sources M7/M8 discuss electrical load selection;
their example products are not selections for this robot.

## Procurement Order

1. Follow the existing camera/basic electronics buy-now list for current bring-up.
2. Borrow a controllable vacuum source and power supply; test a nozzle/pipe/receiver
   rig. Measure suction demand with real balls before buying the final blower.
3. Prototype the hopper, gates and exit confirmation with actual balls. Measure
   separation force, stroke, jams and one-ball reliability before final servos.
4. Weigh the proposed assembly and test rolling resistance/tipping; select the
   drive system and power rails only after chassis review.
5. Obtain local supplier quotations using the verified dimensions and part
   specifications. Populate actual prices and part numbers after review.

No final hardware is ordered, priced or certified by this document. Custom pipe
bends, seal fabrication and aluminium machining are distinct quote items.

## Source Register

Sources checked 11 October 2026. They support material families or design
principles, not the untested robot's performance. CSV source IDs refer here.

- M1: [Hydro 6061 aluminium](https://www.hydro.com/us/us/aluminum/products/extruded-profiles/north-america-resources/extruded-aluminum-products/aluminum-extrusion-alloys/6061-aluminum/).
- M2: [Hydro 5052 aluminium](https://www.hydro.com/us/us/aluminum/products/extruded-profiles/north-america-resources/extruded-aluminum-products/aluminum-extrusion-alloys/5052-aluminum/).
- M3: [SIMONA piping engineering manual](https://www.simona.de/fileadmin/user_upload/Medien/Mediacenter/Technische_Informationen/techinfo_Technisches_Handbuch_fuer_Rohrleitungssysteme-englisch.pdf).
- M4: [Prusa material guide](https://help.prusa3d.com/filament-material-guide) and [PETG guide](https://help.prusa3d.com/article/petg_2059).
- M5: [Ensinger POM-C stock](https://www.ensingerplastics.com/en-us/shapes/acetal-tecaform-ah-natural).
- M6: [Parker static gasket material options](https://ph.parker.com/th/en/gaskets-gaskets).
- M7: [Pololu servo electrical characteristics](https://www.pololu.com/blog/16/electrical-characteristics-of-servos-and-introduction-to-the-servo-control-interface).
- M8: [Pololu motor/power selection](https://www.pololu.com/docs/0J73/4.1).
- M9: [Piab conveying filters](https://www.piab.com/en-us/our-technologies/choose-the-right-vacuum-conveyor-filter).
- M10: [Meyer double-gate airlock principles](https://www.meyerindustrial.com/products/).
- M11: [ebm-papst fan operating-point discussion](https://mag.ebmpapst.com/en/industries/refrigeration-ventilation/determining-and-regulating-air-flow_15181/).
- M12: [FIP padel rules and ball dimensions](https://www.padelfip.com/wp-content/uploads/2025/12/FIP_Rules-of-Padel-1.pdf).
- R1: Existing [procurement](../../HARDWARE_PROCUREMENT.md), [bring-up plan](../../docs/HARDWARE_BRINGUP_PLAN.md), and [decision log](../../docs/DECISION_LOG.md).
