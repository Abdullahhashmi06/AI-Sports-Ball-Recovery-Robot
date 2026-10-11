"""Parametric concept CAD, not fabrication drawings. Units: millimetres.

Install: python -m pip install cadquery vtk pillow
Run: python build_robot.py
Creates STEP, GLB, geometry validation, and rendered previews beside this file.
"""

from pathlib import Path
import json
import math
import cadquery as cq

OUT = Path(__file__).resolve().parent
P = {
    "ball_diameter": 66.0,
    "body_width": 460.0,
    "wheel_radius": 75.0,
    "belt_width": 350.0,
    "belt_front_y": -270.0,
    "belt_front_z": 25.0,
    "belt_rear_y": 100.0,
    "belt_rear_z": 190.0,
    "brush_radius": 40.0,
    "brush_axis_y": -247.0,
    "brush_axis_z": 141.0,
    "basket_floor_z": 145.0,
}
COLORS = {
    "frame": (0.12, 0.47, 0.48),
    "metal": (0.66, 0.70, 0.72),
    "rubber": (0.10, 0.12, 0.13),
    "belt": (0.88, 0.43, 0.16),
    "basket": (0.22, 0.56, 0.37),
    "sensor": (0.24, 0.27, 0.30),
    "lens": (0.24, 0.65, 0.75),
    "stop": (0.85, 0.10, 0.11),
    "ball": (0.79, 0.89, 0.14),
}
PARTS = []
ROBOT = cq.Assembly(name="Padel_Collector_V2_CONCEPT_mm")


def add(name, shape, material="metal", reference=False):
    if isinstance(shape, cq.Workplane):
        shape = shape.val()
    if not shape.isValid() or shape.Volume() <= 0:
        raise ValueError(f"Invalid solid: {name}")
    PARTS.append((name, shape, COLORS[material], reference))
    if not reference:
        ROBOT.add(shape, name=name, color=cq.Color(*COLORS[material]))
    return shape


def box(w, l, h, x, y, z):
    return cq.Workplane("XY").box(w, l, h).translate((x, y, z)).val()


def cylinder(r, length, start, direction=(1, 0, 0)):
    return cq.Solid.makeCylinder(r, length, cq.Vector(*start), cq.Vector(*direction))


ANGLE = math.degrees(math.atan2(P["belt_rear_z"] - P["belt_front_z"],
                              P["belt_rear_y"] - P["belt_front_y"]))
LENGTH = math.hypot(P["belt_rear_y"] - P["belt_front_y"],
                    P["belt_rear_z"] - P["belt_front_z"])
CENTRE = (0, (P["belt_front_y"] + P["belt_rear_y"]) / 2,
          (P["belt_front_z"] + P["belt_rear_z"]) / 2)


def belt_transform(shape):
    return shape.rotate((0, 0, 0), (1, 0, 0), ANGLE).translate(CENTRE)


def build():
    # U-shaped chassis leaves an unobstructed conveyor channel.
    chassis = box(P["body_width"], 500, 6, 0, 60, 78)
    chassis = chassis.cut(box(374, 350, 40, 0, -25, 78))
    for x in (-211, 211):
        for y in (-130, 70, 255):
            chassis = chassis.cut(cylinder(3.2, 20, (x, y, 68), (0, 0, 1)))
    add("U_chassis_6mm_mounting_holes", chassis, "frame")
    for x, side in ((-211, "Left"), (211, "Right")):
        add(f"{side}_side_rail", box(32, 450, 30, x, 70, 97), "frame")
        motor_start = -205 if x < 0 else 147
        motor = cylinder(23, 58, (motor_start, 190, 75))
        add(f"{side}_drive_gearmotor_ENVELOPE", motor, "sensor")
        add(f"{side}_motor_bracket", box(8, 68, 65, x, 190, 99), "metal")
        add(f"{side}_drive_shaft", cylinder(6, 27, (-232 if x < 0 else 205, 190, 75)))
        add(f"{side}_nose_rail", box(32, 66, 30, x, -188, 97), "frame")
        wheel_start = -275 if x < 0 else 230
        tyre = cylinder(P["wheel_radius"], 45, (wheel_start, 190, 75))
        tyre = tyre.cut(cylinder(45, 45, (wheel_start, 190, 75)))
        add(f"{side}_150mm_nonmarking_drive_tyre", tyre, "rubber")
        add(f"{side}_wheel_hub", cylinder(44, 43, (wheel_start + 1, 190, 75)), "metal")
        add(f"{side}_hub_cap", cylinder(16, 47, (wheel_start - 1, 190, 75)), "frame")
        caster_x = -205 if x < 0 else 205
        add(f"{side}_caster_wheel_ENVELOPE", cylinder(30, 24, (caster_x - 12, -135, 30)), "rubber")
        add(f"{side}_caster_fork_ENVELOPE", box(34, 32, 17, caster_x, -135, 62), "metal")
        add(f"{side}_caster_swivel_ENVELOPE", cylinder(12, 11, (caster_x, -135, 70), (0, 0, 1)))

    for y in (-85, 80):
        add(f"Conveyor_crossmember_{y}", box(450, 16, 6, 0, y, 78), "frame")
        top = P["belt_front_z"] + (y - P["belt_front_y"]) * math.tan(math.radians(ANGLE)) - 7
        for x in (-181, 181):
            add(f"Conveyor_support_{x}_{y}", box(10, 16, top - 81, x, y, (81 + top) / 2), "metal")

    # Belt upper run, return run and two small idlers form a layout envelope.
    add("Conveyor_upper_run_2mm", belt_transform(box(P["belt_width"], LENGTH, 2, 0, 0, -1)), "belt")
    add("Conveyor_return_run_2mm", belt_transform(box(P["belt_width"], LENGTH, 2, 0, 0, -23)), "rubber")
    for i in range(5):
        pos = -LENGTH / 2 + 38 + i * 80
        add(f"Conveyor_flexible_cleat_{i+1}", belt_transform(box(338, 5, 16, 0, pos, 8)), "belt")
    for i, local_y in enumerate((-LENGTH / 2, LENGTH / 2)):
        pulley = cylinder(10, 360, (-180, local_y, -12))
        shaft = cylinder(4, 396, (-198, local_y, -12))
        add(f"Conveyor_idler_{i+1}_20mm", belt_transform(pulley), "metal")
        add(f"Conveyor_shaft_{i+1}", belt_transform(shaft), "sensor")
    for x, side in ((-181, "Left"), (181, "Right")):
        shield = box(4, LENGTH + 20, 74, x, 0, 27)
        add(f"{side}_conveyor_side_shield", belt_transform(shield), "frame")
    add("Conveyor_drive_motor_ENVELOPE", cylinder(22, 46, (185, 105, 174)), "sensor")
    ramp = cq.Workplane("YZ", origin=(-175, 0, 0)).polyline([
        (-325, 3), (-270, 25), (-270, 22), (-325, 1)
    ]).close().extrude(350)
    add("Compliant_entry_scoop_replaceable", ramp, "metal")
    for sign, side in ((-1, "Left"), (1, "Right")):
        pts = [(sign * 251, -380), (sign * 258, -376),
               (sign * 181, -310), (sign * 181, -249),
               (sign * 175, -249), (sign * 175, -313)]
        guide = cq.Workplane("XY").polyline(pts).close().extrude(65).translate((0, 0, 10))
        add(f"{side}_flared_intake_guide", guide, "frame")
        add(f"{side}_soft_guide_tip", cylinder(10, 70, (sign * 252, -377, 8), (0, 0, 1)), "rubber")
        bracket = box(7, 66, 100, sign * 182, -247, 124)
        slot = box(16, 9, 30, sign * 182, -247, 141)
        bracket = bracket.cut(slot)
        add(f"{side}_slotted_brush_bearing_mount", bracket, "metal")
        add(f"{side}_brush_bearing_ENVELOPE", cylinder(13, 10, (sign * 182 - 5, -247, 141)), "sensor")
    add("Brush_shaft", cylinder(6, 396, (-198, -247, P["brush_axis_z"])), "metal")
    for i in range(10):
        x = -173 + i * 35
        roller = cylinder(P["brush_radius"], 31, (x, -247, P["brush_axis_z"]))
        roller = roller.cut(cylinder(6.1, 31, (x, -247, P["brush_axis_z"])))
        add(f"Brush_soft_segment_{i+1}_ENVELOPE", roller, "rubber")
    guard = cylinder(46, 352, (-176, -247, 141))
    guard = guard.cut(cylinder(43, 352, (-176, -247, 141)))
    guard = guard.intersect(box(360, 100, 60, 0, -247, 171))
    add("Brush_upper_guard", guard, "metal")
    add("Brush_motor_ENVELOPE", cylinder(19, 43, (193, -247, 141)), "sensor")
    for x, side in ((-211, "Left"), (211, "Right")):
        add(f"{side}_sensor_bracket", box(28, 26, 6, x, -209, 113), "metal")
        add(f"{side}_front_range_sensor_ENVELOPE", box(28, 18, 22, x, -209, 125), "sensor")
        add(f"{side}_range_sensor_window", box(19, 2, 12, x, -219, 125), "lens")
        add(f"{side}_soft_contact_bumper", box(26, 10, 42, x, -224, 95), "rubber")

    # Basket front lip stays below the discharge surface, allowing entry.
    basket = box(380, 190, 4, 0, 220, P["basket_floor_z"] + 2)
    for x in (-135, -81, -27, 27, 81, 135):
        basket = basket.cut(box(12, 135, 10, x, 220, 147))
    rear = box(380, 4, 136, 0, 313, 215)
    for x in (-154, -110, -66, -22, 22, 66, 110, 154):
        rear = rear.cut(box(24, 12, 90, x, 313, 220))
    basket = basket.fuse(rear)
    for x in (-188, 188):
        wall = box(4, 190, 136, x, 220, 215)
        for y in (156, 198, 240, 282):
            wall = wall.cut(box(12, 22, 90, x, y, 220))
        basket = basket.fuse(wall)
    basket = basket.fuse(box(380, 4, 17, 0, 127, 155.5))
    add("Removable_vented_ball_basket", basket, "basket")
    for x in (-200, 200):
        add(f"Basket_latch_{'L' if x < 0 else 'R'}_ENVELOPE", box(16, 28, 18, x, 245, 140), "metal")
        add(f"Basket_support_{'L' if x < 0 else 'R'}", box(22, 180, 29, x, 220, 129.5), "frame")
    add("Basket_rear_handle", box(120, 16, 12, 0, 326, 271), "sensor")
    add("Battery_ENVELOPE_150x90x45", box(150, 90, 45, 80, 200, 105), "sensor")
    add("Electronics_ENVELOPE_150x95x45", box(150, 95, 45, -85, 232, 105), "metal")
    add("Battery_service_cover", box(340, 174, 3, 0, 222, 135), "frame")
    add("Camera_mast", box(15, 20, 231, -211, 72, 227.5), "metal")
    add("Camera_crossbar", box(226, 16, 12, -105.5, 72, 337), "metal")
    add("Forward_camera_ENVELOPE", box(65, 38, 32, 0, 60, 337), "sensor")
    add("Camera_lens", cylinder(10, 6, (0, 35, 337), (0, 1, 0)), "lens")
    add("Estop_bracket_standoff", box(18, 20, 20, 215, 285, 122), "metal")
    add("Estop_bracket", box(52, 60, 4, 225, 285, 133), "metal")
    add("Emergency_stop_base_ENVELOPE", cylinder(15, 18, (225, 285, 135), (0, 0, 1)), "sensor")
    add("Emergency_stop_mushroom", cylinder(23, 12, (225, 285, 153), (0, 0, 1)), "stop")
    add("Front_beam_sensor_L_ENVELOPE", box(10, 12, 10, -172, -193, 94), "sensor")
    add("Front_beam_sensor_R_ENVELOPE", box(10, 12, 10, 172, -193, 94), "sensor")
    # These yellow balls are scale references for the preview, excluded from STEP.
    for row in range(2):
        for col in range(5):
            ball = cq.Solid.makeSphere(P["ball_diameter"] / 2,
                                     cq.Vector(-136 + col * 68, 165 + row * 68, 180),
                                     angleDegrees1=-90, angleDegrees2=90)
            add(f"REFERENCE_ball_{row}_{col}", ball, "ball", reference=True)
    add("REFERENCE_ball_approaching", cq.Solid.makeSphere(33, cq.Vector(15, -405, 33),
        angleDegrees1=-90, angleDegrees2=90), "ball", reference=True)


def export_and_validate():
    ROBOT.export(str(OUT / "Padel_Collector_V2.step"))
    ROBOT.export(str(OUT / "Padel_Collector_V2.glb"))
    parts = [shape for _, shape, _, ref in PARTS if not ref]
    compound = cq.Compound.makeCompound(parts)
    bounds = compound.BoundingBox()
    imported = cq.importers.importStep(str(OUT / "Padel_Collector_V2.step"))
    solids = imported.solids().vals()
    original_volume = sum(shape.Volume() for shape in parts)
    imported_volume = sum(shape.Volume() for shape in solids)
    assert all(s.isValid() for s in solids), "STEP reimport produced invalid solids"
    assert math.isclose(original_volume, imported_volume, rel_tol=1e-7), "STEP volume mismatch"
    report = {
        "status": "Concept geometry only; not fabrication or autonomous-control validation",
        "parameters_mm": P,
        "belt_angle_degrees": round(ANGLE, 2),
        "belt_length_mm": round(LENGTH, 2),
        "physical_component_count": len(parts),
        "step_reimport_solid_count": len(solids),
        "all_solids_valid": True,
        "step_volume_relative_difference": abs(imported_volume - original_volume) / original_volume,
        "overall_bounds_mm": {"width": bounds.xlen, "length": bounds.ylen, "height": bounds.zlen},
        "limitations": ["No original F3D feature history imported", "No collision-free certification",
                        "No motor sizing or structural verification", "No pickup performance test",
                        "Envelope motors, sensors and casters need replacement with supplier CAD"],
    }
    (OUT / "geometry-checks.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (OUT / "parameters.json").write_text(json.dumps(P, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


def render(filename, position, parallel=False, size=(1600, 1200)):
    import vtk
    renderer = vtk.vtkRenderer()
    renderer.SetBackground(0.965, 0.971, 0.977)
    for name, shape, rgb, reference in PARTS:
        vertices, faces = shape.tessellate(0.8, 0.2)
        points = vtk.vtkPoints()
        for v in vertices:
            points.InsertNextPoint(v.x, v.y, v.z)
        cells = vtk.vtkCellArray()
        for face in faces:
            cells.InsertNextCell(3, face)
        data = vtk.vtkPolyData()
        data.SetPoints(points)
        data.SetPolys(cells)
        normals = vtk.vtkPolyDataNormals()
        normals.SetInputData(data)
        normals.SetFeatureAngle(55)
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(normals.GetOutputPort())
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        prop = actor.GetProperty()
        prop.SetColor(*rgb)
        prop.SetAmbient(0.28)
        prop.SetDiffuse(0.72)
        prop.SetSpecular(0.18)
        prop.SetSpecularPower(24)
        renderer.AddActor(actor)
    camera = renderer.GetActiveCamera()
    camera.SetPosition(*position)
    camera.SetFocalPoint(0, -30, 155)
    camera.SetViewUp(0, 1, 0) if filename.endswith("top.png") else camera.SetViewUp(0, 0, 1)
    if parallel:
        camera.ParallelProjectionOn()
    renderer.ResetCamera()
    camera.Zoom(1.18)
    window = vtk.vtkRenderWindow()
    window.SetOffScreenRendering(1)
    window.SetSize(*size)
    window.SetMultiSamples(4)
    window.AddRenderer(renderer)
    window.Render()
    capture = vtk.vtkWindowToImageFilter()
    capture.SetInput(window)
    capture.Update()
    writer = vtk.vtkPNGWriter()
    writer.SetFileName(str(OUT / filename))
    writer.SetInputConnection(capture.GetOutputPort())
    writer.Write()
    window.Finalize()


if __name__ == "__main__":
    build()
    export_and_validate()
    render("improved-preview.png", (850, -1050, 850))
    render("improved-side.png", (1200, -30, 155), parallel=True)
    render("improved-top.png", (0, -30, 1600), parallel=True)
