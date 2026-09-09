"""Ball Detection V1 tests — fake-detector-at-seam, no ML deps required.

The committed suite exercises the detection seam with a deterministic fake
backend (chosen policy: fake detector at seam). Ultralytics-backed paths are
covered by the proof run performed in ``ai/.venv``; they are NOT part of the
committed suite because weights/datasets cannot be committed and tests must
stay deterministic.

E2E flow proven here:
    synthetic image → fake detector → BallDetection
      → detections_to_perception_payload()
      → integration.perception.validate_perception_payload()   (existing)
      → PerceptionFrame → ApplicationOrchestrator.ingest_perception()
      → derive_objective_from_perception()                      (existing seam)
"""

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ai"))

from ai.detection import (  # noqa: E402
    BALL_CLASS_NAME,
    BallDetection,
    DetectionError,
    UltralyticsBallDetector,
    bbox_center,
    detections_to_perception_payload,
    payload_to_frame,
)
# NOTE on import order: integration.perception <-> integration.system have a
# latent circular import (perception/seams.py -> system.state_machine triggers
# system/__init__ -> application.py -> perception.seams while still
# initializing). Importing ANY integration.system module first resolves it —
# the same convention every existing test file uses. Reported as a
# non-blocking issue; not restructured in this milestone.
from integration.system.application import ApplicationOrchestrator  # noqa: E402  (import first, see note)
from integration.system.state_machine import MissionState  # noqa: E402
from integration.communication.laptop_link import LaptopLink  # noqa: E402
from integration.communication.transport import create_memory_pair  # noqa: E402
from integration.perception.validation import (  # noqa: E402
    PerceptionValidationError,
    validate_perception_payload,
)
from navigation.planning.interfaces import Objective  # noqa: E402
from navigation.planning.zones import ZoneId  # noqa: E402

from fake_esp32 import FakeEsp32  # noqa: E402
from telemetry_replay import boot_frame, tele_frame  # noqa: E402


class FakeBallDetector:
    """Deterministic BallDetector double (SIMULATION / TEST ONLY).

    Returns a fixed, configurable detection list for any image; never touches
    ultralytics/torch/camera/serial.
    """

    def __init__(self, detections=None, error: Exception | None = None):
        self.detections = list(detections) if detections is not None else []
        self.error = error
        self.calls: list = []

    def detect(self, image):
        self.calls.append(image)
        if self.error is not None:
            raise self.error
        return list(self.detections)


class TestBBoxCenter(unittest.TestCase):
    def test_center_of_simple_box(self):
        self.assertEqual(bbox_center(0, 0, 10, 10), (5.0, 5.0))

    def test_center_of_offset_box(self):
        self.assertEqual(bbox_center(10, 20, 30, 60), (20.0, 40.0))

    def test_degenerate_box(self):
        self.assertEqual(bbox_center(5, 5, 5, 5), (5.0, 5.0))

    def test_inverted_box_rejected(self):
        with self.assertRaises(DetectionError):
            bbox_center(10, 0, 0, 10)

    def test_non_finite_rejected(self):
        with self.assertRaises(DetectionError):
            bbox_center(float("nan"), 0, 1, 1)
        with self.assertRaises(DetectionError):
            bbox_center(0, float("inf"), 1, 1)


class TestBallDetectionValidation(unittest.TestCase):
    def test_valid_detection(self):
        d = BallDetection(x=100.0, y=50.0, width=20.0, height=20.0, confidence=0.9)
        self.assertEqual(d.center, (100.0, 50.0))

    def test_zero_size_valid(self):
        BallDetection(x=1, y=1, width=0, height=0, confidence=0.0)

    def test_negative_size_rejected(self):
        with self.assertRaises(DetectionError):
            BallDetection(x=1, y=1, width=-5, height=5, confidence=0.5)

    def test_confidence_bounds_enforced(self):
        with self.assertRaises(DetectionError):
            BallDetection(x=1, y=1, width=5, height=5, confidence=1.5)
        with self.assertRaises(DetectionError):
            BallDetection(x=1, y=1, width=5, height=5, confidence=-0.1)
        BallDetection(x=1, y=1, width=5, height=5, confidence=0.0)
        BallDetection(x=1, y=1, width=5, height=5, confidence=1.0)

    def test_non_finite_fields_rejected(self):
        for kwargs in (
            dict(x=float("nan"), y=1, width=5, height=5, confidence=0.5),
            dict(x=1, y=1, width=float("inf"), height=5, confidence=0.5),
        ):
            with self.assertRaises(DetectionError):
                BallDetection(**kwargs)

    def test_boolean_fields_rejected(self):
        with self.assertRaises(DetectionError):
            BallDetection(x=True, y=1, width=5, height=5, confidence=0.5)

    def test_ball_class_name(self):
        self.assertEqual(BALL_CLASS_NAME, "ball")


class TestPayloadConversion(unittest.TestCase):
    def test_single_detection_highest_confidence_wins(self):
        dets = [
            BallDetection(x=10, y=10, width=4, height=4, confidence=0.6),
            BallDetection(x=50, y=60, width=8, height=8, confidence=0.95),
        ]
        payload = detections_to_perception_payload(dets, timestamp=123.0)
        self.assertTrue(payload["ball"]["visible"])
        self.assertIsNone(payload["ball"]["position"])  # image space only
        self.assertEqual(payload["ball"]["confidence"], 0.95)
        self.assertEqual(payload["timestamp"], 123.0)

    def test_image_space_data_travels_in_metadata(self):
        dets = [BallDetection(x=320, y=240, width=30, height=30, confidence=0.8)]
        payload = detections_to_perception_payload(dets, image_size=(640, 480))
        dets_meta = payload["perception_metadata"]["ball_image"]["detections"]
        self.assertEqual(len(dets_meta), 1)
        self.assertEqual(dets_meta[0]["x"], 320)
        self.assertEqual(dets_meta[0]["y"], 240)
        self.assertEqual(payload["perception_metadata"]["image_size"], {"width": 640, "height": 480})

    def test_no_detections_gives_no_ball(self):
        payload = detections_to_perception_payload([])
        self.assertIsNone(payload["ball"])

    def test_extra_metadata_preserved(self):
        payload = detections_to_perception_payload(
            [BallDetection(x=1, y=2, width=3, height=3, confidence=0.5)],
            extra_metadata={"frame_id": 42, "custom": {"a": 1}},
        )
        self.assertEqual(payload["perception_metadata"]["frame_id"], 42)
        self.assertEqual(payload["perception_metadata"]["custom"], {"a": 1})

    def test_invalid_image_size_rejected(self):
        with self.assertRaises(DetectionError):
            detections_to_perception_payload([], image_size=(0, 100))
        with self.assertRaises(DetectionError):
            detections_to_perception_payload([], image_size=(100,))

    def test_payload_passes_existing_validation(self):
        dets = [BallDetection(x=12, y=34, width=6, height=6, confidence=0.77)]
        payload = detections_to_perception_payload(dets, timestamp=5.0)
        frame = validate_perception_payload(payload)  # must not raise
        self.assertIsNotNone(frame.ball)
        self.assertTrue(frame.ball.visible)
        self.assertIsNone(frame.ball.position)  # no invented world coordinates
        self.assertEqual(frame.ball.confidence, 0.77)
        # image-space data preserved via additive metadata policy
        self.assertEqual(
            frame.metadata["perception_metadata"]["ball_image"]["detections"][0]["x"], 12
        )

    def test_no_ball_payload_validation(self):
        frame = validate_perception_payload(detections_to_perception_payload([]))
        self.assertIsNone(frame.ball)


class TestMalformedDetectorOutput(unittest.TestCase):
    def test_detector_error_propagates(self):
        det = FakeBallDetector(error=DetectionError("backend failure"))
        with self.assertRaises(DetectionError):
            det.detect("image")

    def test_malformed_box_rejected_by_dataclass(self):
        with self.assertRaises(DetectionError):
            BallDetection(x="left", y=1, width=5, height=5, confidence=0.5)

    def test_malformed_payload_rejected_at_boundary(self):
        # A malformed-but-dict payload must fail safely at the validation
        # boundary, never crash downstream consumers.
        with self.assertRaises(PerceptionValidationError):
            validate_perception_payload({"ball": {"visible": True, "confidence": "high"}})

    def test_non_mapping_payload_rejected(self):
        with self.assertRaises(PerceptionValidationError):
            validate_perception_payload(["not", "a", "mapping"])


class TestUltralyticsGuard(unittest.TestCase):
    def test_confidence_threshold_validation(self):
        with self.assertRaises(ValueError):
            UltralyticsBallDetector("unused.pt", confidence=1.5)
        with self.assertRaises(ValueError):
            UltralyticsBallDetector("unused.pt", confidence=-0.1)

    def test_lazy_import_fails_cleanly_without_ultralytics(self):
        det = UltralyticsBallDetector("unused.pt")
        # On this machine ultralytics lives only in ai/.venv; the system
        # interpreter running the committed suite has none.
        try:
            import ultralytics  # noqa: F401

            self.skipTest("ultralytics installed in test interpreter")
        except ImportError:
            pass
        with self.assertRaises(DetectionError):
            det.detect("image")


class TestEndToEndSyntheticFlow(unittest.TestCase):
    """E2E: synthetic image → fake detector → payload → existing boundary → objective."""

    def setUp(self):
        self.laptop_end, self.esp_end = create_memory_pair()
        self.fake = FakeEsp32(self.esp_end, tele_period_s=0.02, hb_period_s=0.02)
        self.link = LaptopLink(self.laptop_end, command_timeout_s=0.5)
        self.app = ApplicationOrchestrator(self.link)
        self.link.start()

    def tearDown(self) -> None:
        self.link.stop()
        self.fake.stop()

    def _startup_ready(self):
        self.app.handle_message(boot_frame(1))
        self.app.handle_message(tele_frame(2))
        res = self.app.complete_startup()
        self.assertTrue(res.ok)

    def test_ball_image_flows_to_objective_seam(self):
        self._startup_ready()
        detector = FakeBallDetector(
            [BallDetection(x=320, y=240, width=40, height=40, confidence=0.91)]
        )
        detections = detector.detect("SYNTHETIC_TEST_IMAGE")
        payload = detections_to_perception_payload(detections, timestamp=1.0)
        frame = self.app.ingest_perception(payload)
        self.assertTrue(frame.ball.visible)

        # Mission NAVIGATING + ball visible but position=None → seam must NOT
        # invent a world target (OD-01): NAVIGATING branch requires position.
        self.app.set_mission(MissionState.NAVIGATING)
        from integration.perception.seams import derive_objective_from_perception

        objective = derive_objective_from_perception(frame, self.app.machine.mission)
        # With position=None the ball branch can't fire; no exit hint either →
        # conservative NO_OBJECTIVE, never a fabricated world point.
        self.assertEqual(objective.objective_type.value, "no_objective")

    def test_exit_hint_still_works_through_payload_boundary(self):
        self._startup_ready()
        payload = {
            "exit_hint": {
                "exit_zone": ZoneId.WEST.value,
                "estimated_position": {"x": 1.0, "y": 2.0},
                "confidence": 0.7,
            },
            "timestamp": 2.0,
        }
        frame = self.app.ingest_perception(payload)
        self.assertIsNotNone(frame.exit_hint)
        self.app.set_mission(MissionState.NAVIGATING)
        from integration.perception.seams import derive_objective_from_perception

        objective = derive_objective_from_perception(frame, self.app.machine.mission)
        self.assertEqual(objective.objective_type.value, "search_zone")

    def test_detection_never_creates_motion_commands(self):
        """Detector output must not produce CMD_MOVE (zero-CMD_MOVE invariant)."""
        self._startup_ready()
        # External pose (as real localization would supply; none exists yet) so
        # the planner reaches intent generation rather than NO_POSE.
        from navigation.localization.state import Pose

        self.app.set_pose(Pose(2.0, 2.0, 0.0))
        self.app.set_mission(MissionState.NAVIGATING)
        detector = FakeBallDetector(
            [BallDetection(x=100, y=100, width=10, height=10, confidence=0.99)]
        )
        payload = detections_to_perception_payload(detector.detect("SYNTHETIC_TEST_IMAGE"))
        # A real perception frame may carry a Module-1 exit hint alongside the
        # detector output; the hint supplies the (world) target for planning —
        # the 2D detection itself never does.
        payload["exit_hint"] = {
            "exit_zone": ZoneId.WEST.value,
            "estimated_position": {"x": 1.0, "y": 2.0},
            "confidence": 0.8,
        }
        self.app.ingest_perception(payload)
        tick = self.app.tick()
        # Directive is DEFERRED (OD-07/OD-13) — nothing movement-like is sent.
        from integration.system.commands import DirectiveKind

        self.assertEqual(tick.directive.kind, DirectiveKind.DEFERRED)
        self.assertIn("CMD_MOVE", tick.directive.reason)
        # And nothing reached the wire: zero CMD_MOVE at the fake ESP32.
        moves = [c for c in self.fake.executed_commands if c.get("type") == "CMD_MOVE"]
        self.assertEqual(moves, [])

    def test_empty_frame_ingest_does_not_crash(self):
        self._startup_ready()
        frame = self.app.ingest_perception(detections_to_perception_payload([]))
        self.assertIsNone(frame.ball)


class TestConfigAndDatasetHelpers(unittest.TestCase):
    """Unit tests for pure helpers (no ultralytics needed)."""

    def test_validate_config_accepts_pretrained(self):
        from detection.train import validate_config

        validate_config(
            {"model": "yolo11n.pt", "data": "datasets/coco8-ball/data.yaml",
             "epochs": 20, "batch": 4, "imgsz": 320}
        )

    def test_validate_config_rejects_scratch_training(self):
        from detection.train import validate_config

        for bad in ("scratch.yaml", "from_scratch", "my_model.pt.bak"):
            with self.assertRaises(ValueError):
                validate_config({"model": bad, "data": "d", "epochs": 1, "batch": 1, "imgsz": 32})

    def test_ball_v3_config_valid(self):
        """V3 config: corrected dataset, 640px, transfer learning, and the
        fraction-based chunking parameter present (tooling, not a model claim)."""
        from detection.common import load_config
        from detection.train import validate_config

        cfg_path = Path("ai/detection/configs/ball_v3.yaml")
        if not cfg_path.exists():
            self.skipTest("ball_v3.yaml not present")
        cfg = load_config(cfg_path)
        validate_config(cfg)  # must not raise (pretrained-model guard)
        self.assertEqual(cfg["imgsz"], 640)
        self.assertIn("coco-ball-v3", cfg["data"])
        self.assertEqual(cfg["epochs"], 15)
        frac = float(cfg.get("fraction", 1.0))
        self.assertTrue(0.0 < frac <= 1.0)
        data_yaml = Path("ai/datasets/coco-ball-v3/data.yaml")
        if data_yaml.exists():
            self.assertIn("ball", data_yaml.read_text(encoding="utf-8"))

    def test_validate_config_missing_keys(self):
        from detection.train import validate_config

        with self.assertRaises(ValueError):
            validate_config({"model": "yolo11n.pt"})

    def test_coco_box_conversion(self):
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ai", "detection", "data"
        ))
        from prepare_dataset import _coco_to_yolo_box

        # COCO [x,y,w,h]=[10,20,30,40] in a 100x100 image → center (25,40), size (.3,.4)
        cx, cy, bw, bh = _coco_to_yolo_box([10, 20, 30, 40], 100, 100)
        self.assertAlmostEqual(cx, 0.25)
        self.assertAlmostEqual(cy, 0.40)
        self.assertAlmostEqual(bw, 0.30)
        self.assertAlmostEqual(bh, 0.40)

    def test_coco_box_conversion_clamps(self):
        from prepare_dataset import _coco_to_yolo_box

        # Box extending past all edges clips to the full image:
        # corners clamp to (0,0)-(100,100) → center (0.5, 0.5), full size.
        cx, cy, bw, bh = _coco_to_yolo_box([-10, -10, 200, 200], 100, 100)
        self.assertEqual((cx, cy, bw, bh), (0.5, 0.5, 1.0, 1.0))

        # Box partially off the left/top edge clips, keeping the in-bounds part.
        cx, cy, bw, bh = _coco_to_yolo_box([-10, -10, 30, 30], 100, 100)
        self.assertEqual((cx, cy, bw, bh), (0.1, 0.1, 0.2, 0.2))

    def test_split_fraction_math(self):
        # sanity on the seeded-split arithmetic used by prepare_from_coco_json
        n = 100
        val_frac, test_frac = 0.15, 0.05
        self.assertEqual(int(round(n * val_frac)), 15)
        self.assertEqual(int(round(n * test_frac)), 5)


def _iou(a, b):
    """IoU of two (x0, y0, x1, y1) boxes (test-local helper)."""
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    aa = (a[2] - a[0]) * (a[3] - a[1])
    ab = (b[2] - b[0]) * (b[3] - b[1])
    u = aa + ab - inter
    return inter / u if u > 0 else 0.0


class TestCocoCategoryGuard(unittest.TestCase):
    """Regression guard for the V2 category-id defect (ties-as-balls).

    Official COCO JSON ids are NON-CONTIGUOUS (32 = tie, 37 = sports ball);
    YOLO's 80-class index for sports ball is 32. V2 used the YOLO index against
    official JSON and silently built a tie dataset. This guard proves the
    constants are now distinct and correct against the real annotation files
    when those are present on disk (they are part of the V2 raw pool).
    """

    TRAIN_JSON = Path("ai/datasets/coco-ball-v2/_raw/annotations/instances_train2017.json")
    VAL_JSON = Path("ai/datasets/coco-ball-v2/_raw/annotations/instances_val2017.json")

    def test_constants_are_distinct_and_documented(self):
        from detection.data import prepare_dataset as pd

        self.assertEqual(pd.COCO_SPORTS_BALL_CATEGORY_ID, 37)  # official JSON id
        self.assertEqual(pd.COCO8_YOLO_SPORTS_BALL_CLASS_INDEX, 32)  # YOLO index
        self.assertNotEqual(
            pd.COCO_SPORTS_BALL_CATEGORY_ID,
            pd.COCO8_YOLO_SPORTS_BALL_CLASS_INDEX,
            "official COCO JSON ids and YOLO class indices must not be conflated",
        )

    def test_official_coco_json_ids_verified(self):
        """When the official annotation files are on disk (V2 raw pool), verify
        32=tie and 37=sports ball directly from the authoritative source."""
        if not (self.VAL_JSON.exists() and self.TRAIN_JSON.exists()):
            self.skipTest("official COCO annotation JSONs not on disk")
        val = json.loads(self.VAL_JSON.read_text(encoding="utf-8"))
        cats = {c["id"]: c["name"] for c in val["categories"]}
        self.assertEqual(cats[32], "tie")
        self.assertEqual(cats[37], "sports ball")
        train = json.loads(self.TRAIN_JSON.read_text(encoding="utf-8"))
        cats_t = {c["id"]: c["name"] for c in train["categories"]}
        self.assertEqual(cats_t[37], "sports ball")

    def test_built_dataset_labels_are_real_balls(self):
        """Every GT box in the built dataset's test split must match a REAL
        sports-ball (official category 37) annotation with IoU>=0.5 — the
        regression guard for the V2 ties-as-balls defect. Applies to the
        corrected V3 build; skips if neither exists on disk."""
        if not self.VAL_JSON.exists():
            self.skipTest("official COCO val2017 annotations not on disk")

        val = json.loads(self.VAL_JSON.read_text(encoding="utf-8"))
        imgs = {im["file_name"]: im for im in val["images"]}
        anns = {}
        for a in val["annotations"]:
            anns.setdefault(a["image_id"], []).append(a)
        test_lbl = Path("ai/datasets/coco-ball-v3/labels/test")
        if not test_lbl.exists():
            self.skipTest("corrected V3 dataset not built on disk")
        stale_marker = test_lbl.parent.parent / "STALE_CATEGORY_BUG.md"
        if stale_marker.exists():
            self.skipTest("dataset predates the category fix; rebuild required")
        balls36 = {}
        for stem, im in imgs.items():
            balls36[stem[:-4]] = [
                a["bbox"] for a in anns.get(im["id"], []) if a["category_id"] == 37
            ]
        checked = 0
        for lbl in sorted(test_lbl.glob("*.txt")):
            im = imgs.get(lbl.stem + ".jpg")
            self.assertIsNotNone(im, f"{lbl.stem} not in val2017")
            W, H = im["width"], im["height"]
            for line in lbl.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                checked += 1
                p = line.split()
                cx, cy, w, h = (float(v) for v in p[1:5])
                box = ((cx - w / 2) * W, (cy - h / 2) * H, (cx + w / 2) * W, (cy + h / 2) * H)
                best = max(
                    (
                        _iou(box, (b[0], b[1], b[0] + b[2], b[1] + b[3]))
                        for b in balls36.get(lbl.stem, [])
                        if len(b) == 4
                    ),
                    default=0.0,
                )
                self.assertGreaterEqual(
                    best, 0.5, f"{lbl.stem}: GT box does not match a real sports ball"
                )
        self.assertGreater(checked, 0)


class TestUltralyticsDetectorParams(unittest.TestCase):
    """Constructor validation for imgsz (no ML dependency needed — the guard
    runs before any ultralytics import)."""

    def test_imgsz_none_accepted(self):
        d = UltralyticsBallDetector(Path("w.pt"), imgsz=None)
        self.assertIsNone(d._imgsz)

    def test_imgsz_positive_int_accepted(self):
        d = UltralyticsBallDetector(Path("w.pt"), imgsz=640)
        self.assertEqual(d._imgsz, 640)

    def test_imgsz_rejects_non_positive(self):
        with self.assertRaises(ValueError):
            UltralyticsBallDetector(Path("w.pt"), imgsz=0)

    def test_imgsz_rejects_non_int(self):
        with self.assertRaises(ValueError):
            UltralyticsBallDetector(Path("w.pt"), imgsz=640.5)

    def test_imgsz_rejects_bool(self):
        with self.assertRaises(ValueError):
            UltralyticsBallDetector(Path("w.pt"), imgsz=True)


if __name__ == "__main__":
    unittest.main()
