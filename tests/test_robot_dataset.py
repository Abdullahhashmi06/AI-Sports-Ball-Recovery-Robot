"""Robot-dataset infrastructure tests — deterministic, no video/hardware.

Covers frame hashing / near-duplicate filtering, YOLO label validation,
seeded split planning, and provenance records. Video *extraction* itself
needs OpenCV + real footage and is deliberately not exercised here.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ai"))

from ai.detection.robot_dataset.audit import audit_dataset, audit_split  # noqa: E402
from ai.detection.robot_dataset.frames import (  # noqa: E402
    filter_near_duplicates,
    frame_hash,
    hamming_distance,
)
from ai.detection.robot_dataset.provenance import write_provenance  # noqa: E402
from ai.detection.robot_dataset.split import plan_split, split_dataset  # noqa: E402
from ai.detection.robot_dataset.validate import (  # noqa: E402
    validate_dataset,
    validate_label_line,
)


def make_image(path: Path, color, size=(64, 64)):
    from PIL import Image

    Image.new("RGB", size, color).save(path)


def make_gradient_image(path: Path, size=(64, 64), invert: bool = False):
    """Structured (non-uniform) image — required for meaningful mean-threshold
    hashes, which collapse uniform colors regardless of brightness."""
    from PIL import Image

    w, h = size
    im = Image.new("L", size)
    im.putdata(
        [
            ((x + y) % 256 if not invert else (255 - (x + y) % 256))
            for y in range(h)
            for x in range(w)
        ]
    )
    im.save(path)


class TestFrameHash(unittest.TestCase):
    def test_same_image_same_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.png"
            make_image(a, (100, 100, 100))
            self.assertEqual(frame_hash(a), frame_hash(a))

    def test_different_images_different_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.png", Path(tmp) / "b.png"
            make_gradient_image(a)
            make_gradient_image(b, invert=True)
            self.assertNotEqual(frame_hash(a), frame_hash(b))

    def test_uniform_images_collapse_to_same_hash(self):
        # Known property of mean-threshold hashes: any uniform image produces
        # the same bits regardless of brightness. For dedupe this is safe
        # (uniform frames are information-free), but tests must not use solid
        # colors to exercise *discrimination*.
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.png", Path(tmp) / "b.png"
            make_image(a, (10, 10, 10))
            make_image(b, (240, 240, 240))
            self.assertEqual(frame_hash(a), frame_hash(b))

    def test_identical_copies_are_true_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.png", Path(tmp) / "b.png"
            make_image(a, (50, 120, 200))
            make_image(b, (50, 120, 200))
            kept, dropped = filter_near_duplicates([a, b])
            self.assertEqual(len(kept), 1)
            self.assertIn(str(b), dropped)

    def test_distinct_images_all_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Three structurally different patterns: diagonal ramp, inverted
            # ramp, and a hard left-dark/right-bright split. (Note: shifted
            # linear ramps would NOT count as distinct — mean-threshold
            # hashing captures spatial structure, not absolute brightness.)
            from PIL import Image

            paths = []
            p1 = Path(tmp) / "img0.png"
            make_gradient_image(p1)
            paths.append(p1)

            p2 = Path(tmp) / "img1.png"
            make_gradient_image(p2, invert=True)
            paths.append(p2)

            p3 = Path(tmp) / "img2.png"
            im = Image.new("L", (64, 64))
            im.putdata([0 if x < 32 else 255 for y in range(64) for x in range(64)])
            im.save(p3)
            paths.append(p3)

            kept, dropped = filter_near_duplicates(paths)
            self.assertEqual(len(kept), 3)
            self.assertEqual(dropped, {})

    def test_hamming_distance(self):
        self.assertEqual(hamming_distance("0000", "0000"), 0)
        self.assertEqual(hamming_distance("f", "0"), 4)
        with self.assertRaises(ValueError):
            hamming_distance("00", "000")


class TestLabelValidation(unittest.TestCase):
    def test_valid_line(self):
        rec, err = validate_label_line("0 0.5 0.5 0.2 0.3", num_classes=1)
        self.assertIsNone(err)
        self.assertEqual(rec, {"class": 0, "cx": 0.5, "cy": 0.5, "w": 0.2, "h": 0.3})

    def test_wrong_field_count(self):
        rec, err = validate_label_line("0 0.5 0.5 0.2", num_classes=1)
        self.assertIsNone(rec)
        self.assertIn("expected 5 fields", err)

    def test_class_out_of_range(self):
        rec, err = validate_label_line("1 0.5 0.5 0.2 0.3", num_classes=1)
        self.assertIsNone(rec)
        self.assertIn("class 1 outside", err)

    def test_unnormalized_box(self):
        rec, err = validate_label_line("0 0.5 0.5 1.5 0.3", num_classes=1)
        self.assertIsNone(rec)
        self.assertIn("outside [0, 1]", err)

    def test_degenerate_box(self):
        rec, err = validate_label_line("0 0.5 0.5 0.0 0.3", num_classes=1)
        self.assertIsNone(rec)
        self.assertIn("degenerate", err)

    def test_box_outside_image(self):
        # A box entirely outside the image always violates center/size
        # normalization first (cx=1.5 > 1) — the dedicated outside-image check
        # remains as defense-in-depth for any path that dodges normalization.
        rec, err = validate_label_line("0 1.5 1.5 0.1 0.1", num_classes=1)
        self.assertIsNone(rec)
        self.assertIn("outside [0, 1]", err)

    def test_dataset_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            img_dir, lbl_dir = base / "images", base / "labels"
            img_dir.mkdir()
            lbl_dir.mkdir()
            make_image(img_dir / "a.jpg", (1, 2, 3))
            make_image(img_dir / "b.jpg", (4, 5, 6))
            (lbl_dir / "a.txt").write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
            (lbl_dir / "b.txt").write_text("", encoding="utf-8")  # background frame: legal
            report = validate_dataset(img_dir, lbl_dir)
            self.assertTrue(report["ok"])
            self.assertEqual(report["num_boxes"], 1)
            self.assertEqual(report["num_empty_label_files"], 1)
            self.assertEqual(report["missing_labels"], [])
            self.assertEqual(report["missing_images"], [])

    def test_dataset_report_detects_missing_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            img_dir, lbl_dir = base / "images", base / "labels"
            img_dir.mkdir()
            lbl_dir.mkdir()
            make_image(img_dir / "lonely.jpg", (1, 2, 3))
            report = validate_dataset(img_dir, lbl_dir)
            self.assertFalse(report["ok"])
            self.assertEqual(report["missing_labels"], ["lonely.jpg"])

    def test_dataset_report_detects_bad_box(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            img_dir, lbl_dir = base / "images", base / "labels"
            img_dir.mkdir()
            lbl_dir.mkdir()
            make_image(img_dir / "a.jpg", (1, 2, 3))
            (lbl_dir / "a.txt").write_text("0 0.5 0.5 2.0 0.2\n", encoding="utf-8")
            report = validate_dataset(img_dir, lbl_dir)
            self.assertFalse(report["ok"])
            self.assertEqual(len(report["errors"]), 1)


class TestSplit(unittest.TestCase):
    def test_plan_split_is_deterministic_and_exhaustive(self):
        stems = [f"f{i:03d}" for i in range(40)]
        plan = plan_split(stems, seed=20260906, val_frac=0.25, test_frac=0.25)
        plan2 = plan_split(stems, seed=20260906, val_frac=0.25, test_frac=0.25)
        self.assertEqual(plan, plan2)  # same seed ⇒ same assignment
        self.assertEqual(set(plan.values()), {"train", "val", "test"})
        self.assertEqual(len(plan), 40)
        self.assertEqual(sum(v == "val" for v in plan.values()), 10)
        self.assertEqual(sum(v == "test" for v in plan.values()), 10)

    def test_plan_split_rejects_bad_fractions(self):
        with self.assertRaises(ValueError):
            plan_split(["a"], val_frac=0.6, test_frac=0.6)

    def test_session_grouped_split_never_straddles(self):
        # 3 sessions × 10 frames: every frame of a session must land in the
        # SAME split — the core leakage property of video-derived datasets.
        stems = [f"sess{s}_frame{i:03d}" for s in range(3) for i in range(10)]
        sessions = {s: s.split("_")[0] for s in stems}
        plan = plan_split(stems, seed=7, val_frac=1 / 3, test_frac=0.0, sessions=sessions)
        by_session = {}
        for stem, split in plan.items():
            by_session.setdefault(sessions[stem], set()).add(split)
        for sess, splits in by_session.items():
            self.assertEqual(len(splits), 1, f"session {sess} straddles splits: {splits}")
        # All frames assigned, all three splits used (3 sessions / 3 splits).
        self.assertEqual(len(plan), len(stems))
        self.assertEqual({next(iter(v)) for v in by_session.values()}, {"train", "val"})

    def test_session_grouped_split_is_deterministic(self):
        stems = [f"sess{s}_f{i}" for s in range(5) for i in range(4)]
        sessions = {s: s.split("_")[0] for s in stems}
        self.assertEqual(
            plan_split(stems, seed=11, sessions=sessions),
            plan_split(stems, seed=11, sessions=sessions),
        )

    def test_plan_split_rejects_unknown_session(self):
        with self.assertRaises(ValueError):
            plan_split(["x"], sessions={})

    def test_split_dataset_with_session_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            img_dir, lbl_dir = base / "raw_images", base / "raw_labels"
            img_dir.mkdir()
            lbl_dir.mkdir()
            for s in range(3):
                for i in range(4):
                    make_image(img_dir / f"sess{s}_frame{i}.jpg", (s * 40, i * 20, 90))
                    (lbl_dir / f"sess{s}_frame{i}.txt").write_text(
                        "0 0.5 0.5 0.1 0.1\n", encoding="utf-8"
                    )
            out = base / "dataset"
            counts = split_dataset(
                img_dir,
                lbl_dir,
                out,
                seed=3,
                val_frac=1 / 3,
                test_frac=0.0,
                dedupe=False,
                session_pattern=r"(sess\d+)_",
            )
            self.assertEqual(sum(counts.values()), 12)
            # No session straddles: each images/val file's session siblings all in val.
            for split in ("train", "val", "test"):
                names = {p.stem for p in (out / "images" / split).glob("*.jpg")}
                sess_here = {n.split("_")[0] for n in names}
                for other in ("train", "val", "test"):
                    if other == split:
                        continue
                    other_names = {p.stem for p in (out / "images" / other).glob("*.jpg")}
                    overlap = sess_here & {n.split("_")[0] for n in other_names}
                    self.assertEqual(overlap, set(), f"session leakage {split}->{other}: {overlap}")

    def test_split_dataset_rejects_nonmatching_session_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            img_dir, lbl_dir = base / "raw_images", base / "raw_labels"
            img_dir.mkdir()
            lbl_dir.mkdir()
            make_image(img_dir / "nofeature.jpg", (1, 2, 3))
            (lbl_dir / "nofeature.txt").write_text("", encoding="utf-8")
            with self.assertRaises(ValueError):
                split_dataset(
                    img_dir, lbl_dir, base / "out", dedupe=False, session_pattern=r"(sess\d+)_"
                )

    def test_split_dataset_moves_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            img_dir, lbl_dir = base / "raw_images", base / "raw_labels"
            img_dir.mkdir()
            lbl_dir.mkdir()
            for i in range(6):
                make_image(img_dir / f"f{i}.jpg", (i * 30, 60, 90))
                (lbl_dir / f"f{i}.txt").write_text("0 0.5 0.5 0.1 0.1\n", encoding="utf-8")
            out = base / "dataset"
            counts = split_dataset(img_dir, lbl_dir, out, seed=1, val_frac=1 / 3, test_frac=0.0, dedupe=False)
            self.assertEqual(sum(counts.values()), 6)
            for split in ("train", "val", "test"):
                imgs = list((out / "images" / split).glob("*.jpg"))
                lbls = list((out / "labels" / split).glob("*.txt"))
                self.assertEqual(len(imgs), counts[split])
                self.assertEqual(len(lbls), len(imgs), f"label/image mismatch in {split}")
            self.assertTrue((out / "data.yaml").exists())


class TestProvenance(unittest.TestCase):
    def test_record_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "provenance.json"
            write_provenance(
                out,
                dataset_name="robot-ball-v1",
                validation_domain="robot",
                sources=[{"description": "robot bench footage"}],
                notes="first capture session",
            )
            rec = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(rec["validation_domain"], "robot")
            self.assertEqual(rec["dataset_name"], "robot-ball-v1")
            self.assertIn("created_utc", rec)
            self.assertEqual(rec["sources"][0]["description"], "robot bench footage")

    def test_invalid_domain_rejected(self):
        with self.assertRaises(ValueError):
            write_provenance(
                Path(tempfile.mkdtemp()) / "p.json",
                dataset_name="x",
                validation_domain="unclear",
                sources=[],
            )

    def test_public_domain_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "p.json"
            write_provenance(out, dataset_name="coco-v2", validation_domain="public_dataset", sources=[])
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["validation_domain"], "public_dataset")


class TestAudit(unittest.TestCase):
    """Whole-dataset audit: contamination, leakage, suspicious boxes, evidence."""

    def _make_split(self, root: Path, split: str, images: int, boxes_per_image=1, size=64):
        """Create images/<split> + labels/<split> with synthetic content."""
        from PIL import Image

        idir = root / "images" / split
        ldir = root / "labels" / split
        idir.mkdir(parents=True, exist_ok=True)
        ldir.mkdir(parents=True, exist_ok=True)
        for i in range(images):
            stem = f"{split}_{i:03d}"
            Image.new("RGB", (size, size), (i * 7 % 255, 90, 30)).save(idir / f"{stem}.png")
            lines = [
                f"0 {0.25 + 0.1 * (j % 5):.3f} 0.500 {8.0 / size:.5f} {8.0 / size:.5f}"
                for j in range(boxes_per_image)
            ]
            (ldir / f"{stem}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_clean_dataset_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_split(root, "train", 3)
            self._make_split(root, "val", 2)
            self._make_split(root, "test", 1)
            rep = audit_dataset(root)
            self.assertTrue(rep["ok"])
            self.assertEqual(rep["contamination"], [])
            self.assertEqual(rep["session_leakage"], [])
            self.assertEqual(rep["errors"], [])
            self.assertEqual(rep["totals"]["train"]["images"], 3)
            self.assertEqual(rep["totals"]["train"]["boxes"], 3)

    def test_cross_split_stem_contamination_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_split(root, "train", 2)
            self._make_split(root, "test", 2)
            # copy one train image+label into test with the SAME stem
            for sub in ("images", "labels"):
                src = root / sub / "train" / ("train_000." + ("png" if sub == "images" else "txt"))
                src.rename(src)  # no-op, keep source
                dst_dir = root / sub / "test"
                dst = dst_dir / src.name
                dst.write_bytes(src.read_bytes())
            rep = audit_dataset(root)
            self.assertFalse(rep["ok"])
            self.assertEqual(len(rep["contamination"]), 1)
            self.assertEqual(rep["contamination"][0]["splits"], ["test", "train"])
            self.assertIn("train_000", rep["contamination"][0]["stems"])

    def test_session_leakage_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_split(root, "train", 2)
            self._make_split(root, "val", 1)
            # sessA spans BOTH train and val → leakage
            sessions = {"train_000": "sessA", "train_001": "sessA", "val_000": "sessA"}
            rep = audit_dataset(root, splits=("train", "val"), sessions=sessions)
            self.assertFalse(rep["ok"])
            self.assertEqual(len(rep["session_leakage"]), 1)
            self.assertEqual(rep["session_leakage"][0]["session"], "sessA")
            self.assertEqual(rep["session_leakage"][0]["splits"], ["train", "val"])

    def test_no_leakage_when_sessions_grouped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_split(root, "train", 2)
            self._make_split(root, "val", 1)
            # sessA entirely inside train; sessB entirely inside val → no leakage
            sessions = {"train_000": "sessA", "train_001": "sessA", "val_000": "sessB"}
            rep = audit_dataset(root, splits=("train", "val"), sessions=sessions)
            self.assertEqual(rep["session_leakage"], [])
            self.assertTrue(rep["ok"])

    def test_tiny_box_flagged_but_not_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_split(root, "train", 1)
            # overwrite label with a 2×2 px box on a 64×64 image → 4 px² < 16 px² floor
            lbl = root / "labels" / "train" / "train_000.txt"
            lbl.write_text("0 0.5 0.5 0.03125 0.03125\n", encoding="utf-8")
            rep = audit_dataset(root, splits=("train",))
            self.assertTrue(rep["ok"], "tiny boxes are review flags, not structural failures")
            flags = rep["splits"]["train"]["flags_for_review"]["suspicious_tiny_boxes"]
            self.assertEqual(len(flags), 1)
            self.assertEqual(flags[0]["image"], "train_000")

    def test_huge_box_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_split(root, "train", 1)
            lbl = root / "labels" / "train" / "train_000.txt"
            lbl.write_text("0 0.5 0.5 0.98 0.98\n", encoding="utf-8")
            rep = audit_dataset(root, splits=("train",))
            self.assertTrue(rep["ok"])
            flags = rep["splits"]["train"]["flags_for_review"]["suspicious_large_boxes"]
            self.assertEqual(len(flags), 1)
            self.assertGreater(flags[0]["area_fraction"], 0.9)

    def test_small_ball_evidence_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_split(root, "train", 2)
            # 1 px box on 64 px image → area fraction 0.000244 < 0.0005 → small
            lbl = root / "labels" / "train" / "train_000.txt"
            lbl.write_text("0 0.5 0.5 0.015625 0.015625\n", encoding="utf-8")
            rep = audit_dataset(root, splits=("train",))
            stats = rep["splits"]["train"]["box_statistics"]
            self.assertEqual(stats["small_ball_boxes"], 1)
            self.assertEqual(stats["num_boxes"], 2)  # 1 small (train_000) + 1 default 8px (train_001)

    def test_missing_split_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_split(root, "train", 1)
            rep = audit_dataset(root, splits=("train", "val"))
            self.assertFalse(rep["ok"])
            self.assertTrue(any("val" in e for e in rep["errors"]))

    def test_pixel_duplicate_check_flags_identical_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_split(root, "train", 1)
            self._make_split(root, "test", 1)
            # make test_000 an exact pixel copy of train_000 (different stem → no stem contamination)
            src = root / "images" / "train" / "train_000.png"
            dst = root / "images" / "test" / "test_000.png"
            dst.write_bytes(src.read_bytes())
            rep = audit_dataset(root, pixel_duplicate_check=True)
            self.assertEqual(len(rep["pixel_duplicates"]), 1)
            self.assertEqual(rep["pixel_duplicates"][0]["a"]["split"], "test")
            self.assertEqual(rep["pixel_duplicates"][0]["b"]["split"], "train")

    def test_audit_split_report_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_split(root, "train", 2)
            rep = audit_split(root / "images" / "train", root / "labels" / "train")
            self.assertTrue(rep["ok"])
            self.assertEqual(rep["split"], "train")
            self.assertEqual(rep["box_statistics"]["num_boxes"], 2)
            self.assertIn("median_box_area_px", rep["box_statistics"])

    def test_unreadable_image_size_skips_box_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_split(root, "train", 1)
            # corrupt the image so PIL cannot read it
            img = root / "images" / "train" / "train_000.png"
            img.write_bytes(b"not a png")
            rep = audit_split(root / "images" / "train", root / "labels" / "train")
            self.assertEqual(rep["box_statistics"]["num_boxes"], 0)
            self.assertIsNone(rep["box_statistics"]["median_box_area_px"])


if __name__ == "__main__":
    unittest.main()
