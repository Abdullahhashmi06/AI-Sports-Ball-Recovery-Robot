"""V4 suction and automatic-dispensing concept, millimetres.

Keep build_robot.py beside this file. Install cadquery, vtk, pillow; run this file.
The STEP is layout geometry, not a verified pneumatic or fabrication design.
"""
import json
import math
from pathlib import Path
import cadquery as cq
import build_robot as base

OUT = Path(__file__).resolve().parent.parent
CAD = OUT / "cad"
IMAGES = OUT / "images"
CHECKS = OUT / "validation"
for folder in (CAD, IMAGES, CHECKS):
    folder.mkdir(parents=True, exist_ok=True)
base.OUT = IMAGES
base.ROBOT = cq.Assembly(name="Padel_Collector_V4_SUCTION_AUTO_DISPENSE_CONCEPT_mm")
base.COLORS["pipe"] = (0.91, 0.47, 0.18)
base.PARTS.clear()
add, box, cylinder = base.add, base.box, base.cylinder
P = {"ball_nominal_diameter": 66, "ball_max_diameter": 67.7,
     "ball_pipe_inside_diameter": 100, "pipe_wall": 4,
     "mouth_inside_width": 140, "mouth_inside_height": 90,
     "pipe_bend_centreline_radius": 200, "bend_angle_degrees": 60,
     "air_branch_inside_diameter": 50, "screen_hole_diameter": 7,
     "receiver_outer_width": 360, "receiver_outer_length": 230,
     "receiver_floor_height": 222, "receiver_top_height": 438,
     "metering_bore_diameter": 76, "upper_gate_z": 230,
     "lower_gate_z": 150, "gate_thickness": 4, "gate_open_stroke": 94,
     "dispenser_axis_y": 280, "outlet_bend_radius": 80}
CENTRELINE_SAMPLES = []


def point(y, z):
    return cq.Vector(0, y, z)


def pipe_path():
    radius = P["pipe_bend_centreline_radius"]
    a = math.radians(P["bend_angle_degrees"])
    first = point(-365, 56)
    s = point(-325, 56)
    e = point(s.y + radius * math.sin(a), s.z + radius * (1 - math.cos(a)))
    mid = point(s.y + radius * math.sin(a / 2), s.z + radius * (1 - math.cos(a / 2)))
    t = point(e.y + 80 * math.cos(a), e.z + 80 * math.sin(a))
    u = point(t.y + radius * math.sin(a), t.z + radius * (1 - math.cos(a)))
    mid2 = point(t.y + radius * (math.sin(a) - math.sin(a / 2)),
                 t.z + radius * (math.cos(a / 2) - math.cos(a)))
    end = point(108, u.z)
    edges = [cq.Edge.makeLine(first, s), cq.Edge.makeThreePointArc(s, mid, e),
             cq.Edge.makeLine(e, t), cq.Edge.makeThreePointArc(t, mid2, u),
             cq.Edge.makeLine(u, end)]
    for frac in (0, 0.25, 0.5, 0.75, 1):
        CENTRELINE_SAMPLES.append(point(first.y + frac * (s.y - first.y), 56))
        phi = a * frac
        CENTRELINE_SAMPLES.append(point(s.y + radius * math.sin(phi), s.z + radius * (1 - math.cos(phi))))
        CENTRELINE_SAMPLES.append(point(e.y + frac * (t.y - e.y), e.z + frac * (t.z - e.z)))
        CENTRELINE_SAMPLES.append(point(t.y + radius * (math.sin(a) - math.sin(a - phi)),
                                       t.z + radius * (math.cos(a - phi) - math.cos(a))))
        CENTRELINE_SAMPLES.append(point(u.y + frac * (end.y - u.y), u.z))
    return cq.Wire.assembleEdges(edges), end.z


def dispenser():
    y = P["dispenser_axis_y"]
    tube = cylinder(42, 64, (0, y, 158), (0, 0, 1)).cut(
        cylinder(38, 64, (0, y, 158), (0, 0, 1)))
    add("One_ball_metering_chamber_76mm_ID", tube, "pipe")
    for label, z in (("Upper_isolation", 230), ("Lower_release", 150)):
        frame = box(112, 100, 16, 0, y, z)
        frame = frame.cut(cylinder(38, 30, (0, y, z - 15), (0, 0, 1)))
        frame = frame.cut(box(300, 94, 6, 0, y, z))
        add(f"{label}_gate_cassette", frame, "metal")
        plate = box(110, 92, 4, 0, y, z)
        add(f"{label}_servo_slide_gate_CLOSED", plate, "frame")
        gasket = box(108, 96, 1, 0, y, z + 3.5).cut(
            cylinder(38.2, 4, (0, y, z + 2), (0, 0, 1)))
        add(f"{label}_face_seal_ENVELOPE", gasket, "rubber")
        add(f"{label}_servo_ENVELOPE", box(40, 20, 40, 110, 343, z + 10), "sensor")
        add(f"{label}_servo_mount", box(140, 25, 8, 55, 338, z - 10), "metal")
        add(f"{label}_servo_link_ENVELOPE", tube_between((110, 343, z), (50, y, z), 0.01, 3.99), "metal")
        for dx, state in ((-62, "closed"), (157, "open")):
            add(f"{label}_{state}_position_sensor_ENVELOPE", box(8, 12, 10, dx, y + 44, z), "sensor")
    upper_seat = cylinder(42, 6, (0, y, 238), (0, 0, 1)).cut(
        cylinder(38, 6, (0, y, 238), (0, 0, 1)))
    add("Hopper_to_upper_gate_neck", upper_seat, "pipe")
    for x in (-62, 62):
        add(f"Dispenser_mount_post_{x}", box(12, 100, 144, x, y, 153), "metal")
    # Downward inlet turns rearward, leaving the court beneath the outlet clear.
    start, mid, end = point(280, 142), point(303.4314575, 85.4314575), point(360, 62)
    path = cq.Wire.assembleEdges([cq.Edge.makeThreePointArc(start, mid, end),
                                 cq.Edge.makeLine(end, point(430, 62))])
    plane = cq.Plane(origin=start.toTuple(), xDir=(1, 0, 0), normal=(0, 0, -1))
    outlet = cq.Workplane(plane).circle(42).circle(38).sweep(path, isFrenet=True).val()
    add("Automatic_rear_ball_outlet_76mm_ID", outlet, "pipe")
    for x in (-47, 47):
        add(f"Exit_breakbeam_{x}_ENVELOPE", box(10, 16, 12, x, 410, 62), "sensor")
    add("Pocket_sensor_ENVELOPE", box(10, 12, 12, 47, y, 190), "sensor")
    add("REFERENCE_metered_ball", cq.Solid.makeSphere(33.85, cq.Vector(0, y, 185.85),
        angleDegrees1=-90, angleDegrees2=90), "ball", True)
    return outlet


def tube_between(start, end, inner_r, wall):
    v = cq.Vector(*end).sub(cq.Vector(*start))
    return cylinder(inner_r + wall, v.Length, start, v.toTuple()).cut(
        cylinder(inner_r, v.Length, start, v.toTuple()))


def build():
    path, inlet_z = pipe_path()
    plane = cq.Plane(origin=(0, -365, 56), xDir=(1, 0, 0), normal=(0, 1, 0))
    ball_pipe = cq.Workplane(plane).circle(54).circle(50).sweep(path, isFrenet=True).val()
    add("100mm_ID_continuous_ball_transfer_pipe", ball_pipe, "pipe")

    nose_plane = cq.Plane(origin=(0, -395, 47), xDir=(1, 0, 0), normal=(0, 1, 0))
    outer = cq.Solid.makeLoft([cq.Workplane(nose_plane).ellipse(72, 47).val(),
                              cq.Workplane(plane).circle(54).val()])
    inner = cq.Solid.makeLoft([cq.Workplane(nose_plane).ellipse(70, 45).val(),
                              cq.Workplane(plane).circle(50).val()])
    nozzle = outer.cut(inner).cut(box(180, 40, 10, 0, -380, -4.2))
    add("Low_oval_pickup_nozzle_140x90_inside", nozzle, "pipe")
    lip_plane = cq.Plane(origin=(0, -399, 47), xDir=(1, 0, 0), normal=(0, 1, 0))
    lip = cq.Workplane(lip_plane).ellipse(73, 47).ellipse(70, 45).extrude(4)
    add("Compliant_pickup_lip_ENVELOPE", lip, "rubber")

    chassis = box(460, 520, 6, 0, 50, 78)
    chassis = chassis.cut(box(124, 140, 30, 0, -165, 78))
    chassis = chassis.cut(box(90, 110, 20, 0, 285, 78))
    for x in (-211, 211):
        for y in (-130, 70, 255):
            chassis = chassis.cut(cylinder(3.2, 20, (x, y, 68), (0, 0, 1)))
    add("Chassis_with_front_pipe_clearance", chassis, "frame")
    for x, side in ((-211, "Left"), (211, "Right")):
        add(f"{side}_frame_rail", box(32, 500, 30, x, 50, 97), "frame")
        add(f"{side}_drive_gearmotor_ENVELOPE", cylinder(23, 58, (-205 if x < 0 else 147, 190, 75)), "sensor")
        add(f"{side}_motor_bracket", box(8, 68, 65, x, 190, 99), "metal")
        add(f"{side}_drive_shaft", cylinder(6, 27, (-232 if x < 0 else 205, 190, 75)))
        ws = -275 if x < 0 else 230
        tyre = cylinder(75, 45, (ws, 190, 75)).cut(cylinder(45, 45, (ws, 190, 75)))
        add(f"{side}_drive_tyre", tyre, "rubber")
        add(f"{side}_wheel_hub", cylinder(44, 43, (ws + 1, 190, 75)), "metal")
        add(f"{side}_hub_cap", cylinder(16, 47, (ws - 1, 190, 75)), "frame")
        caster_x = -205 if x < 0 else 205
        add(f"{side}_front_caster_ENVELOPE", cylinder(30, 24, (caster_x - 12, -135, 30)), "rubber")
        add(f"{side}_caster_fork_ENVELOPE", box(34, 32, 17, caster_x, -135, 62), "metal")
        add(f"{side}_caster_swivel_ENVELOPE", cylinder(12, 11, (caster_x, -135, 70), (0, 0, 1)))
        add(f"{side}_front_bumper", box(30, 12, 44, x, -207, 98), "rubber")
        add(f"{side}_range_sensor_ENVELOPE", box(28, 18, 22, x, -185, 125), "sensor")
        add(f"{side}_range_sensor_bracket", box(28, 26, 6, x, -185, 113))
        add(f"{side}_range_sensor_window", box(19, 2, 12, x, -195, 125), "lens")

    clamp = cylinder(61, 12, (0, -332, 56), (0, 1, 0)).cut(
        cylinder(54.2, 12, (0, -332, 56), (0, 1, 0)))
    clamp = clamp.cut(box(150, 30, 40, 0, -326, 0))
    add("Pickup_pipe_upper_clamp", clamp, "metal")
    for x in (-80, 80):
        add(f"Pickup_support_arm_{x}", box(20, 136, 8, x, -268, 78), "frame")
        add(f"Pickup_clamp_ear_{x}", box(22, 12, 12, -65 if x < 0 else 65, -326, 80))
        add(f"Pickup_arm_root_{x}", box(20, 22, 18, x, -200, 90), "metal")

    # Sealed receiver: ball inlet and screened air outlet are distinct ports.
    receiver = box(360, 230, 216, 0, 215, 330)
    receiver = receiver.cut(box(352, 222, 216, 0, 215, 334))
    receiver = receiver.cut(cylinder(42.3, 25, (0, 280, 214), (0, 0, 1)))
    receiver = receiver.cut(cylinder(54.3, 20, (0, 95, inlet_z), (0, 1, 0)))
    receiver = receiver.cut(cylinder(29, 20, (125, 95, 384), (0, 1, 0)))
    add("SEALED_rear_ball_receiver", receiver, "basket")
    gasket = cylinder(60, 8, (0, 98, inlet_z), (0, 1, 0)).cut(
        cylinder(53.8, 8, (0, 98, inlet_z), (0, 1, 0)))
    add("Ball_inlet_sealing_collar", gasket, "rubber")
    lid = box(360, 230, 5, 0, 215, 440.5)
    add("Removable_airtight_receiver_lid", lid, "basket")
    rim_gasket = box(352, 222, 2, 0, 215, 437).cut(box(342, 212, 4, 0, 215, 437))
    add("Lid_perimeter_gasket_ENVELOPE", rim_gasket, "rubber")
    for x in (-187, 187):
        add(f"Lid_latch_{x}_ENVELOPE", box(18, 30, 22, x, 235, 426), "metal")
        add(f"Receiver_support_{x}", box(22, 200, 128, x, 215, 164), "frame")
    handle = box(110, 16, 10, 0, 215, 464)
    for x in (-48, 48):
        handle = handle.fuse(box(14, 16, 20, x, 215, 453))
    add("Lid_lifting_handle", handle, "sensor")
    add("Soft_rear_impact_liner_ENVELOPE", box(170, 4, 100, 0, 324, inlet_z), "rubber")

    air_path = cq.Wire.assembleEdges([
        cq.Edge.makeLine(cq.Vector(125, 10, 225), cq.Vector(125, 10, 324)),
        cq.Edge.makeThreePointArc(cq.Vector(125, 10, 324), cq.Vector(125, 27.5735931, 366.4264069), cq.Vector(125, 70, 384)),
        cq.Edge.makeLine(cq.Vector(125, 70, 384), cq.Vector(125, 109, 384))])
    air_plane = cq.Plane(origin=(125, 10, 225), xDir=(1, 0, 0), normal=(0, 0, 1))
    add("50mm_ID_receiver_to_blower_air_branch", cq.Workplane(air_plane).circle(28).circle(25).sweep(air_path, isFrenet=True), "metal")
    add("Air_branch_sealing_collar", tube_between((125, 98, 384), (125, 106, 384), 27.8, 4.2), "rubber")
    screen = box(78, 3, 100, 125, 117, 384)
    for xx in range(95, 156, 10):
        for zz in range(344, 425, 10):
            screen = screen.cut(cylinder(3.5, 10, (xx, 112, zz), (0, 1, 0)))
    add("Ball_retention_screen_7mm_holes", screen, "metal")
    for x in (90, 160):
        add(f"Screen_standoff_{x}", box(8, 14, 98, x, 110, 384), "metal")
    funnel_plane = lambda z, yy: cq.Plane(origin=(0, yy, z), xDir=(1, 0, 0), normal=(0, 0, 1))
    funnel_outer = cq.Solid.makeLoft([cq.Workplane(funnel_plane(244, 280)).circle(42).val(),
                                    cq.Workplane(funnel_plane(310, 215)).rect(348, 218).val()])
    funnel_inner = cq.Solid.makeLoft([cq.Workplane(funnel_plane(244, 280)).circle(38).val(),
                                    cq.Workplane(funnel_plane(310, 215)).rect(340, 210).val()])
    add("Gravity_hopper_to_one_ball_dispenser", funnel_outer.cut(funnel_inner), "metal")
    outlet_pipe = dispenser()
    add("Pressure_equalization_valve_ENVELOPE", cylinder(12, 18, (-120, 150, 443), (0, 0, 1)), "sensor")
    add("Receiver_pressure_sensor_ENVELOPE", box(25, 20, 15, -130, 145, 456), "sensor")
    blower = box(96, 110, 140, 125, -10, 155)
    add("Suction_blower_plus_filter_ENVELOPE", blower, "sensor")
    add("Blower_mounting_plate", box(118, 124, 4, 125, -10, 83), "metal")
    outlet = cylinder(28, 12, (125, -77, 165), (0, 1, 0)).cut(
        cylinder(24, 12, (125, -77, 165), (0, 1, 0)))
    add("Blower_air_exhaust_ENVELOPE", outlet, "metal")
    add("Battery_ENVELOPE", box(150, 90, 45, -100, -15, 105), "sensor")
    add("Control_box_ENVELOPE", box(100, 60, 40, -125, -100, 102), "metal")
    add("Camera_mast", box(15, 20, 231, -211, 12, 227.5), "metal")
    add("Camera_crossbar", box(152, 16, 12, -143, 12, 337), "metal")
    add("Forward_camera_ENVELOPE", box(65, 38, 32, -75, 0, 337), "sensor")
    add("Camera_lens", cylinder(10, 6, (-75, -25, 337), (0, 1, 0)), "lens")
    add("Estop_bracket_standoff", box(18, 20, 20, 215, 285, 122), "metal")
    add("Estop_bracket", box(52, 60, 4, 225, 285, 133), "metal")
    add("Emergency_stop_base_ENVELOPE", cylinder(15, 18, (225, 285, 135), (0, 0, 1)), "sensor")
    add("Emergency_stop_mushroom", cylinder(23, 12, (225, 285, 153), (0, 0, 1)), "stop")
    for row in range(2):
        for col in range(5):
            add(f"REFERENCE_stored_ball_{row}_{col}", cq.Solid.makeSphere(33,
                cq.Vector(-136 + col * 68, 160 + row * 68, 348),
                angleDegrees1=-90, angleDegrees2=90), "ball", True)
    add("REFERENCE_approaching_ball", cq.Solid.makeSphere(33, cq.Vector(0, -440, 33),
        angleDegrees1=-90, angleDegrees2=90), "ball", True)
    return ball_pipe, receiver, inlet_z, outlet_pipe


def validate(ball_pipe, inlet_z, outlet_pipe):
    base.ROBOT.export(str(CAD / "Padel_Collector_V4_Auto_Dispense.step"))
    base.ROBOT.export(str(CAD / "Padel_Collector_V4_Auto_Dispense.glb"))
    parts = [s for _, s, _, ref in base.PARTS if not ref]
    restored = cq.importers.importStep(str(CAD / "Padel_Collector_V4_Auto_Dispense.step")).solids().vals()
    before, after = sum(s.Volume() for s in parts), sum(s.Volume() for s in restored)
    assert all(s.isValid() for s in restored)
    assert len(parts) == len(restored)
    assert math.isclose(before, after, rel_tol=1e-7)
    for pos in CENTRELINE_SAMPLES:
        ball = cq.Solid.makeSphere(33.85, pos, angleDegrees1=-90, angleDegrees2=90)
        assert ball_pipe.intersect(ball).Volume() < 1e-5, "Reference ball intersects pipe wall"
    bb = cq.Compound.makeCompound(parts).BoundingBox()
    for t in (0, 0.25, 0.5, 0.75, 1):
        a = t * math.pi / 2
        pos = point(360 - 80 * math.cos(a), 142 - 80 * math.sin(a))
        ball = cq.Solid.makeSphere(33.85, pos, angleDegrees1=-90, angleDegrees2=90)
        assert outlet_pipe.intersect(ball).Volume() < 1e-5
    gates = [s for name, s, _, _ in base.PARTS if name.endswith("servo_slide_gate_CLOSED")]
    for gate, z in zip(gates, (230, 150)):
        port = cylinder(38, 4, (0, 280, z - 2), (0, 0, 1))
        assert math.isclose(gate.intersect(port).Volume(), port.Volume(), rel_tol=1e-7)
        assert gate.translate((94, 0, 0)).intersect(port).Volume() < 1e-5
    assert 230 - 2 - (150 + 2) > 67.7
    assert 230 - 2 - (150 + 2) < 2 * 63.5
    report = {"version": "V4 suction with automatic dispensing concept", "parameters_mm": P,
        "receiver_inlet_axis_height_mm": inlet_z,
        "valid_component_count": len(parts), "STEP_reimport_solid_count": len(restored),
        "STEP_volume_relative_difference": abs(before-after)/before,
        "sampled_ball_to_pipe_wall_checks": len(CENTRELINE_SAMPLES),
        "sampled_ball_to_outlet_wall_checks": 5,
        "gate_closed_port_coverage_and_open_clearance": "pass for both plates",
        "one_ball_pocket_clear_height_mm": 76,
        "overall_dimensions_mm": [bb.xlen, bb.ylen, bb.zlen],
        "minimum_model_z_mm": bb.zmin,
        "limits": ["No airflow, pickup-force or blower sizing validation", "No structural or whole-assembly collision certification",
                   "Concept envelopes require supplier geometry and prototype testing"]}
    (CHECKS / "geometry-checks.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    pipe, receiver, z, outlet = build()
    validate(pipe, z, outlet)
    base.render("suction-v4-preview.png", (850, -1050, 800))
    base.render("suction-v4-rear.png", (850, 1100, 750))
    base.render("suction-v4-side.png", (1200, -30, 155), parallel=True)
    base.render("suction-v4-top.png", (0, -30, 1600), parallel=True)
    # Cutaway only affects this explanatory render; export retains sealed walls.
    cut = box(210, 260, 230, 100, 215, 345)
    visible = []
    for name, shape, rgb, ref in base.PARTS:
        if name in ("Removable_airtight_receiver_lid", "Lid_lifting_handle", "Lid_perimeter_gasket_ENVELOPE") or name.startswith("Lid_latch"):
            continue
        if name == "SEALED_rear_ball_receiver":
            shape = shape.cut(cut)
        if name == "Soft_rear_impact_liner_ENVELOPE":
            shape = shape.cut(cut)
        visible.append((name, shape, rgb, ref))
    base.PARTS[:] = visible
    base.render("suction-v4-cutaway.png", (850, 1100, 750))
