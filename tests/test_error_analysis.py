"""Ball Detection V2 error-analysis tests — deterministic fake-detector seam.

No ultralytics, no real model, no network: the analysis core is exercised with
synthetic boxes/images. The V2 model and dataset are never touched.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ai"))

from ai.detection.error_analysis import (  # noqa: E402
    NEAR_MISS_IOU,
    POOR_LOCALIZATION_AREA_RATIO,
    SMALL_BALL_FRACTION,
    aggregate,
    annotate_images,
    classify_image,
    iou,
    load_ground_truth,
    run_analysis,
    yolo_to_xyxy,
)
from ai.detection.inference import BallDetection  # noqa: E402


def det(cx_px, cy_px, w_px, h_px, conf, img_w=100, img_h=100):
    """Build a BallDetection from pixel center/size on a fixed-size image."""
    return BallDetection(
        x=cx_px, y=cy_px, width=w_px, height=h_px, confidence=conf
    )


def gt_box(x0, y0, x1, y1, img_w=100, img_h=100):
    """GT entry as classify_image expects (absolute corners + area fraction)."""
    return {
        "class": 0,
        "box": (x0, y0, x1, y1),
        "area_fraction": ((x1 - x0) * (y1 - y0)) / (img_w * img_h),
    }


class TestIou(unittest.TestCase):
    def test_identical_boxes(self):
        self.assertAlmostEqual(iou((0, 0, 10, 10), (0, 0, 10, 10)), 1.0)

    def test_disjoint_boxes(self):
        self.assertEqual(iou((0, 0, 10, 10), (20, 20, 30, 30)), 0.0)

    def test_partial_overlap(self):
        # 10x10 boxes offset by 5 in x, full overlap in y:
        # intersection 5x10=50, union 100+100-50=150 → 1/3
        self.assertAlmostEqual(iou((0, 0, 10, 10), (5, 0, 15, 10)), 50.0 / 150.0)

    def test_touching_edges(self):
        self.assertEqual(iou((0, 0, 10, 10), (10, 0, 20, 10)), 0.0)


class TestYoloConversion(unittest.TestCase):
    def test_center_conversion(self):
        # center (0.5, 0.5), size (0.2, 0.4) on 100x200 → corners (40,60)-(60,140)
        self.assertEqual(yolo_to_xyxy(0.5, 0.5, 0.2, 0.4, 100, 200), (40.0, 60.0, 60.0, 140.0))

    def test_clamps_to_image(self):
        box = yolo_to_xyxy(0.02, 0.02, 0.2, 0.2, 100, 100)
        self.assertGreaterEqual(box[0], 0.0)
        self.assertGreaterEqual(box[1], 0.0)
        self.assertLessEqual(box[3], 100.0)


class TestClassifyImage(unittest.TestCase):
    def test_perfect_detection_is_tp(self):
        gts = [gt_box(10, 10, 30, 30)]
        res = classify_image(gts, [det(20, 20, 20, 20, 0.9)], image_size=(100, 100))
        self.assertEqual(res["counts"]["tp"], 1)
        self.assertEqual(res["counts"]["fn"], 0)
        self.assertEqual(res["counts"]["fp"], 0)
        self.assertAlmostEqual(res["tp"][0]["iou"], 1.0)

    def test_no_detection_is_missed(self):
        gts = [gt_box(10, 10, 30, 30)]
        res = classify_image(gts, [], image_size=(100, 100))
        self.assertEqual(res["counts"]["fn"], 1)
        self.assertEqual(res["missed"][0]["gt_index"], 0)

    def test_low_confidence_prediction_ignored(self):
        gts = [gt_box(10, 10, 30, 30)]
        res = classify_image(gts, [det(20, 20, 20, 20, 0.1)], image_size=(100, 100), conf_threshold=0.25)
        self.assertEqual(res["counts"]["fn"], 1)
        self.assertEqual(res["counts"]["tp"], 0)
        self.assertEqual(res["num_predictions"], 0)

    def test_disjoint_prediction_is_fp(self):
        gts = [gt_box(10, 10, 30, 30)]
        res = classify_image(gts, [det(80, 80, 10, 10, 0.9)], image_size=(100, 100))
        self.assertEqual(res["counts"]["fp"], 1)
        self.assertEqual(res["counts"]["tp"], 0)

    def test_duplicate_detections_on_same_gt(self):
        gts = [gt_box(10, 10, 30, 30)]
        preds = [det(20, 20, 20, 20, 0.9), det(21, 21, 19, 19, 0.7)]
        res = classify_image(gts, preds, image_size=(100, 100))
        self.assertEqual(res["counts"]["tp"], 1)
        # The second detection still overlaps the (already taken) GT strongly:
        # standard convention keeps it inside FP, tagged kind=duplicate.
        self.assertEqual(res["counts"]["fp"], 1)
        self.assertEqual(res["counts"]["duplicates"], 1)
        self.assertEqual(res["duplicates"][0]["kind"], "duplicate")
        # (21,21,19,19) vs GT (10,10,30,30): IoU ≈ 0.817 — well above the
        # 0.5 threshold, so the extra detection is tagged as a duplicate.
        self.assertGreaterEqual(res["duplicates"][0]["best_any_iou"], 0.5)

    def test_poor_localization_flagged(self):
        # GT 20x20 (area 400); prediction 16x16 (area 256) well inside it:
        # IoU = 256/400 = 0.64 ≥ 0.5 (TP) and 400 ≥ 1.5×256 → poor localization
        gts = [gt_box(10, 10, 30, 30)]
        res = classify_image(gts, [det(20, 20, 16, 16, 0.9)], image_size=(100, 100))
        self.assertEqual(res["counts"]["tp"], 1)
        self.assertEqual(res["counts"]["poor_localization"], 1)

    def test_good_localization_not_flagged(self):
        gts = [gt_box(10, 10, 30, 30)]
        res = classify_image(gts, [det(21, 21, 19, 19, 0.9)], image_size=(100, 100))
        self.assertEqual(res["counts"]["poor_localization"], 0)

    def test_small_ball_flag_recorded(self):
        # GT area 4 px² of 10,000 → 0.0004 < SMALL_BALL_FRACTION
        gts = [gt_box(10, 10, 12, 12)]
        res_no_det = classify_image(gts, [], image_size=(100, 100))
        self.assertTrue(res_no_det["missed"][0]["small_ball"])
        res_det = classify_image(gts, [det(11, 11, 2, 2, 0.9)], image_size=(100, 100))
        self.assertTrue(res_det["tp"][0]["gt_small"])

    def test_fp_near_miss_iou_recorded(self):
        # Prediction overlapping the GT but below the IoU threshold → FP with high near-miss IoU
        gts = [gt_box(10, 10, 30, 30)]
        res = classify_image(gts, [det(25, 25, 20, 20, 0.9)], image_size=(100, 100), iou_threshold=0.9)
        self.assertEqual(res["counts"]["fp"], 1)
        self.assertGreaterEqual(res["fp"][0]["best_unmatched_iou"], NEAR_MISS_IOU)


class TestAggregate(unittest.TestCase):
    def _image(self, tp=0, fp=0, fn=0, tp_small=False, fp_kinds=()):
        tp_entry = {
            "pred_index": 0, "gt_index": 0, "iou": 0.9, "confidence": 0.8,
            "gt_area_fraction": 0.0004 if tp_small else 0.04,
            "gt_small": tp_small, "poor_localization": False,
        }
        missed = [{"gt_index": 0, "area_fraction": 0.0004 if tp_small else 0.04, "small_ball": tp_small}]
        return {
            "counts": {"tp": tp, "fp": fp, "fn": fn, "duplicates": 0, "poor_localization": 0},
            "tp": [tp_entry] * tp,
            "fp": [
                {"pred_index": i, "best_unmatched_iou": 0.3, "best_any_iou": 0.3,
                 "kind": k, "confidence": 0.5}
                for i, k in enumerate(fp_kinds)
            ],
            "missed": missed * fn,
            "num_gt": tp + fn,
        }

    def test_recall_precision_math(self):
        agg = aggregate([self._image(tp=2, fp=1, fn=2)])
        self.assertEqual(agg["counts"], {"tp": 2, "fp": 1, "fn": 2, "duplicates": 0, "poor_localization": 0})
        self.assertAlmostEqual(agg["recall_at_thresholds"], 0.5)
        self.assertAlmostEqual(agg["precision_at_thresholds"], 2.0 / 3.0, places=3)

    def test_recall_by_ball_size(self):
        rows = [self._image(tp=1, tp_small=True, fn=1, fp=0),
                self._image(tp=1, tp_small=False, fn=1)]
        agg = aggregate(rows)
        self.assertEqual(agg["recall_by_ball_size"]["small"]["found"], 1)
        self.assertEqual(agg["recall_by_ball_size"]["small"]["total"], 2)
        self.assertAlmostEqual(agg["recall_by_ball_size"]["small"]["recall"], 0.5)
        self.assertAlmostEqual(agg["recall_by_ball_size"]["normal"]["recall"], 0.5)

    def test_fp_breakdown_by_kind(self):
        agg = aggregate([
            self._image(tp=1, fp=2, fp_kinds=("near_miss", "near_miss")),
            self._image(tp=0, fn=1, fp=1, fp_kinds=("background",)),
        ])
        self.assertEqual(agg["fp_breakdown"]["near_miss"], 2)
        self.assertEqual(agg["fp_breakdown"]["background"], 1)
        self.assertEqual(agg["fp_breakdown"]["duplicate"], 0)
        self.assertAlmostEqual(agg["fp_breakdown"]["near_miss"] / 3.0, 2 / 3.0, places=3)

    def test_empty_aggregate_is_none_safe(self):
        agg = aggregate([self._image()])
        self.assertIsNone(agg["recall_at_thresholds"])
        self.assertIsNone(agg["missed_gt_area_fraction"]["median"])


class TestRunAnalysisEndToEnd(unittest.TestCase):
    """Full pipeline over synthetic PNGs + label files with a fake detector."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.img_dir = base / "images"
        self.lbl_dir = base / "labels"
        self.img_dir.mkdir()
        self.lbl_dir.mkdir()

        from PIL import Image, ImageDraw

        # Image A: one large GT ball, detector finds it (TP) + one FP.
        Image.new("RGB", (100, 100), (10, 10, 10)).save(self.img_dir / "a.png")
        (self.lbl_dir / "a.txt").write_text("0 0.2 0.2 0.2 0.2\n", encoding="utf-8")
        im = Image.new("RGB", (100, 100), (10, 10, 10))
        ImageDraw.Draw(im).ellipse((10, 10, 30, 30), fill=(200, 200, 200))
        im.save(self.img_dir / "a.png")

        # Image B: one tiny GT ball (small/distant evidence), detector misses it.
        Image.new("RGB", (100, 100), (5, 5, 5)).save(self.img_dir / "b.png")
        (self.lbl_dir / "b.txt").write_text("0 0.8 0.8 0.02 0.02\n", encoding="utf-8")

        self.images = [self.img_dir / "a.png", self.img_dir / "b.png"]
        self.labels = [self.lbl_dir / "a.txt", self.lbl_dir / "b.txt"]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_run_analysis_full_report(self):
        class FakeDetector:
            def detect(self, image):
                if image.endswith("a.png"):
                    return [det(20, 20, 20, 20, 0.9), det(70, 70, 6, 6, 0.4)]
                return []

        report = run_analysis(FakeDetector(), self.images, self.labels, conf_threshold=0.25)
        self.assertEqual(report["num_images"], 2)
        a, b = report["per_image"]
        self.assertEqual(a["counts"], {"tp": 1, "fp": 1, "fn": 0, "duplicates": 0, "poor_localization": 0})
        self.assertEqual(b["counts"]["fn"], 1)
        self.assertTrue(b["missed"][0]["small_ball"])
        agg = report["aggregate"]
        self.assertAlmostEqual(agg["recall_at_thresholds"], 0.5)
        self.assertAlmostEqual(agg["recall_by_ball_size"]["small"]["recall"], 0.0)
        self.assertAlmostEqual(agg["recall_by_ball_size"]["normal"]["recall"], 1.0)
        # predictions/GT recorded for the JSON export
        self.assertEqual(len(a["predictions"]), 2)
        self.assertEqual(len(a["ground_truth"]), 1)

    def test_annotate_images_renders_files(self):
        class FakeDetector:
            def detect(self, image):
                return [det(20, 20, 20, 20, 0.9)] if image.endswith("a.png") else []

        report = run_analysis(FakeDetector(), self.images, self.labels, conf_threshold=0.25)
        out_dir = Path(self._tmp.name) / "annotated"
        n = annotate_images(report, self.img_dir, out_dir)
        self.assertEqual(n, 2)
        names = sorted(p.name for p in out_dir.iterdir())
        self.assertEqual(names, ["a.png", "b.png"])

    def test_analysis_is_readonly_over_inputs(self):
        """The analysis must not modify the input images or labels."""
        before = {p: p.read_bytes() for p in self.images}
        before_lbl = {p: p.read_bytes() for p in self.labels}

        class FakeDetector:
            def detect(self, image):
                return []

        run_analysis(FakeDetector(), self.images, self.labels)
        for p, data in before.items():
            self.assertEqual(p.read_bytes(), data, f"image modified: {p}")
        for p, data in before_lbl.items():
            self.assertEqual(p.read_bytes(), data, f"label modified: {p}")

    def test_no_training_capability_in_module(self):
        """The analysis module must not import training machinery."""
        import ai.detection.error_analysis as ea

        src = Path(ea.__file__).read_text(encoding="utf-8")
        self.assertNotIn("from ultralytics import YOLO", src)
        self.assertNotIn("model.train(", src)
        self.assertNotIn(".fit(", src)


class TestGroundTruthLoading(unittest.TestCase):
    def test_load_ground_truth_computes_area_fraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            lbl = Path(tmp) / "x.txt"
            lbl.write_text("0 0.5 0.5 0.1 0.1\n\n0 0.1 0.1 0.02 0.02\n", encoding="utf-8")
            gts = load_ground_truth(lbl, 100, 100)
            self.assertEqual(len(gts), 2)
            self.assertAlmostEqual(gts[0]["area_fraction"], 0.01)
            self.assertAlmostEqual(gts[1]["area_fraction"], 0.0004)

    def test_missing_label_file_is_empty(self):
        self.assertEqual(load_ground_truth(Path("does/not/exist.txt"), 100, 100), [])

    def test_malformed_lines_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            lbl = Path(tmp) / "x.txt"
            lbl.write_text("0 0.5 0.5 0.1\nnot a box\n0 0.1 0.1 0.02 0.02\n", encoding="utf-8")
            gts = load_ground_truth(lbl, 100, 100)
            self.assertEqual(len(gts), 1)


if __name__ == "__main__":
    unittest.main()
