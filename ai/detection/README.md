# Ball Detection — Training & Inference Pipeline (V1 → V3 history, V3 is current)

**Package:** Module 1 (Perception / AI) — laptop-side, software-only.
**Status:** V3 TRAINED AND EVALUATED ON PUBLIC DATA (corrected COCO sports-ball
dataset). NOT robot-ready — no real-robot footage has been collected or
evaluated (§4.4). Current authoritative baseline: **V3 @ 640 px**
(test: P 0.786 / R 0.500 / small-ball R 0.474; formal test eval:
mAP50 0.547, mAP50-95 0.378; val split at epoch 15: mAP50 0.604,
mAP50-95 0.414) — details in §4.7. V4 exists **as a frozen PLAN only**
(§4.7), not a run.
**This file is NOT authoritative.** `docs/ARCHITECTURE.md`, `docs/COMMUNICATION_PROTOCOL.md`,
`docs/TEAM_INTERFACE_CONTRACT.md`, and `docs/DECISION_LOG.md` remain authoritative. This
document makes no hardware choices and resolves no OD-01…OD-16.

---

## 0. Version history at a glance

| Version | Dataset | Outcome |
|---|---|---|
| V1 | Synthetic circle fixtures | Proves the train→evaluate→inference→perception machinery only. No performance claim. |
| V2 | COCO extraction — **defective**: used YOLO class index 32 (= "tie" in official COCO JSON) instead of annotation id 37 ("sports ball") | Trained/evaluated on neckties. All V2 numbers deprecated; dataset quarantined (§4.2 warning). Defect fixed in code + regression-guarded. |
| **V3 (current)** | Corrected COCO cat-37 sports-ball dataset, 4,262/127/42 images, 6,610 verified boxes | YOLO11n transfer learning @ 640 px, 15/15 epochs. Measured: 640 px decisively beats 320 px for small balls (§4.7). Public-dataset validation only. |
| V4 | — | **PLAN ONLY** (§4.7). Not started. Requires full-data run on an uninterrupted machine. |

## 1. What this is (and is not)

This package contains a **real, trainable ML ball detector** (not another placeholder
abstraction) plus the glue that feeds its output into the *existing* perception boundary:

```
camera image (future hardware / test image)
    → ai.detection.inference  (detector backend, bbox → center → confidence)
    → raw perception payload dict          (image-space only — NO world coordinates)
    → integration.perception.validate_perception_payload   (existing boundary, unchanged)
    → PerceptionFrame (ball.visible=True, ball.position=None)
    → ApplicationOrchestrator.ingest_perception()          (existing seam, unchanged)
    → derive_objective_from_perception()                    (existing seam, unchanged)
```

Deliberately **not** implemented here:

* **No world coordinates.** A 2D bounding box does not identify a world position.
  `BallObservation.position` stays `None` until a real localization/calibration source
  exists (OD-01 remains open). Image-space data travels only in payload `metadata`
  (e.g. `ball_image`), which the validation boundary preserves per its additive policy.
* **No camera model, calibration, mounting geometry, lens parameters, or resolution
  assumptions.** None exist in the authoritative docs.
* **No robot-specific distance thresholds or lighting assumptions.**
* **No CMD_MOVE / motion of any kind.** Perception cannot reach the command boundary
  except through the existing planner + gate chain.

## 2. Model choice — YOLO11n (ultralytics)

| Factor | Why YOLO11n |
|---|---|
| Pretrained | COCO-pretrained weights (`yolo11n.pt`) → transfer learning, never trained from scratch |
| Lightweight | nano scale — smallest/fastest YOLO11 variant, the realistic candidate for eventual edge deployment |
| Ball class exists | COCO class 32 `sports ball` gives a direct pretrained starting point |
| Single-class fine-tune | Standard ultralytics training flow; one `ball` class |
| Repository fit | torch/ultralytics are pure-Python pip installs; no compiled laptop-side deps beyond torch itself |

Alternatives considered: YOLOv8n (older sibling, near-identical profile), training from
scratch (rejected: no dataset scale), heavier YOLO sizes (rejected until a laptop/GPU
budget is actually measured).

## 3. Environment

Project-local venv (the global Python torch install is **broken** on this machine —
`import torch` fails; do not rely on it):

```bash
cd ai
python -m venv .venv
.venv/Scripts/python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv/Scripts/python.exe -m pip install -r detection/requirements.txt
```

CPU-only torch is used because no GPU is available; the pipeline is hardware-agnostic.
Committed unit tests deliberately require **none** of this — they run on the plain
system Python with a deterministic fake detector injected at the seam.

## 4. Datasets

### 4.1 COCO8 (pipeline proof — used for the committed, reproducible proof run)

* Source: Ultralytics COCO8 — an 8-image/4-val-image subset of COCO train2017/val2017
  with YOLO-format labels. Auto-downloaded by ultralytics on first training run
  (`https://ultralytics.com/assets/coco8.zip`, ≈1 MB).
* Purpose: prove the **train → validate → evaluate → inference** mechanics end-to-end.
  It is NOT a performance claim — 8 images prove plumbing, nothing more.
* License: images inherit COCO image terms (Flickr Terms of Use; COCO images are for
  non-commercial research use); annotations inherit COCO's CC BY 4.0.

### 4.2 COCO sports-ball extraction (the real public-data run — Ball Detection V2)

> ⚠️ **CATEGORY-ID DEFECT (found 2026-09-06, post-V2):** V2 was built using the
> ultralytics YOLO 80-class index (32) instead of the official COCO JSON
> annotation id (37) for "sports ball". In official COCO JSON, **32 = "tie"**.
> Proven: every GT box in the V2 dataset (train + val + test) matches a COCO
> "tie" annotation with IoU 1.0. **V2 was trained and evaluated on neckties,
> not balls** — all §4.2/§4.3 metrics are metrics of a tie detector and must
> not be quoted as ball-detection results. The pipeline defect is fixed
> (`COCO_SPORTS_BALL_CATEGORY_ID = 37`, with `COCO8_YOLO_SPORTS_BALL_CLASS_INDEX
> = 32` kept separate for COCO8's YOLO-format labels) and guarded by
> `TestCocoCategoryGuard` regression tests. The defective V2 dataset is
> preserved intact (evidence) under `ai/datasets/_quarantine_v2_ties_20260906/`
> (incl. its `STALE_CATEGORY_BUG.md` marker); the reusable official annotation
> JSONs were kept in `ai/datasets/coco-ball-v2/_raw/annotations/`.
> The V1 synthetic run is unaffected (geometric circles, no COCO conversion).
>
> **Corrected rebuild (V3 dataset, 2026-09-06):** re-downloaded only images with
> official category_id=37 (4,431 images, 0 failures, sha256 manifest) and built
> `ai/datasets/coco-ball-v3`: **train 4,262 imgs / 6,347 boxes · val 127 / 197 ·
> test 42 / 66** (seeded split, val/test uncapped). Every label box in every
> split verified IoU≥0.5 against the authoritative JSON cat-37 annotations with
> exact per-image count parity; robot_dataset validator ok for all splits; 0
> malformed labels. Sanity: box aspect-ratio median 0.77 (ball-like; tie boxes
> were ≈0.25), 52% near-square. Annotated samples for human review:
> `runs/ball_v3_dataset_samples/` (gitignored). **V3 is ready for training; the
> V2 metrics above remain tie-detector metrics and are retained only as the
> defect record.**

* `detection/data/download_coco_balls.py` downloads the official COCO 2017
  annotation ZIP once (~250 MB) and then fetches **only the images that
  actually contain sports-ball annotations** (3,810 train / 145 val) from the
  official COCO CDN — a ~1.3 GB subset instead of the full ~19 GB archive.
  Resumable, sha256 manifest with sources/licenses in `_raw/manifest.json`.
* `detection/data/prepare_dataset.py --coco-json … --max-train-images 2500`
  converts them to single-class YOLO with a seeded train/val/test split
  (train capped at 2,500 as a software CPU budget only; val/test uncapped):
  **2,500 train / 109 val / 36 test, all positive**.
* Config: `detection/configs/ball_v2.yaml` (YOLO11n transfer learning,
  epochs=15 configured — run **finalized at epoch 8/best** because CPU epochs
  grew to ~20 min and the tooling caps commands at ~10 min, making later
  epochs unchunkable; the mAP trend was already established).
* **V2 results (public-dataset validation ONLY):** test split (36 images,
  75 instances) → **P=0.631, R=0.347, mAP50=0.349, mAP50-95=0.177**;
  ~0.32 s/image workstation CPU (NOT a robot latency budget). Report:
  `runs/ball_v2/eval_report.json` (gitignored). Training curve: mAP50 0.263
  (epoch 1) → 0.394 (epoch 7), confirming the pipeline learns on real data.
* License: COCO annotations CC BY 4.0; images Flickr Terms of Use
  (non-commercial research use). Recorded in PROVENANCE.md + manifest.json.

### 4.3 V2 ERROR ANALYSIS — why recall is 0.347 (measured, not speculated)

`detection/error_analysis.py` ran `runs/ball_v2/weights/best.pt` over the V2
**test** split (36 images, 75 GT balls) with configurable thresholds (report:
`runs/ball_v2/error_analysis.json`, annotated images:
`runs/ball_v2/error_analysis_images/` — both gitignored). At IoU≥0.5,
conf≥0.25:

| Measure | Value |
|---|---|
| TP / FP / FN | 27 / 12 / 48 (recall 0.360 @ conf 0.25; precision 0.692) |
| **Recall by ball size** | **normal balls (≥0.5% img area): 0.778 · small balls (<0.5%): 0.125** |
| Missed-ball median area | 0.06% of the image (max 34.4% — one outlier) |
| FP breakdown | 2 duplicate · 5 near-miss (IoU≥0.1 with an unmatched ball) · 5 background |
| TP quality | median IoU 0.77, median confidence 0.51 |
| Missed balls with a ≥0.1-IoU candidate box at conf 0.01 | **26 of 48** (14 fully invisible, 8 weak overlap) |

**Dominant failure mode: small/distant balls.** Small (<0.5 % of image area)
balls are missed 87.5 % of the time while normal-size balls are found 77.8 %
of the time — recall is almost entirely a **ball-size/scale** problem. This is
consistent with the 320 px training/inference resolution: a COCO sports ball
occupying 0.05 % of a 640-px image is ~2–4 px at inference size. Supporting
evidence: 26/48 missed balls still have a plausible candidate box at conf
0.01 (the features are *seen* but scored below the operating point), FPs are
split between nearby-object confusions (5) and background (5), and TP
localization quality is good (median IoU 0.77, zero poor-localization TPs).

**What would most likely improve recall (evidence-ranked, no retraining
performed in this milestone):**

1. **More small-ball training examples** — the strongest lever. COCO's
   'sports ball' distribution under-represents tiny balls; the future robot
   dataset (§5) should deliberately include small/distant balls.
2. **Higher inference resolution** for the *operating point* (e.g. inference
   at 640 px while keeping training data unchanged) — directly attacks the
   measured scale failure; latency cost must be weighed against OD-05
   (open).
3. **Operating-point/lower-confidence pass** — 26 missed balls already have
   candidate boxes below conf 0.25; a lower threshold trades precision for
   recall and is a *configurable analysis/detection parameter*, not an
   engineering requirement.
4. **Only after 1–3:** further training (more epochs / full uncapped dataset)
   is justified by this analysis — but is deliberately **not** part of this
   milestone.

What would NOT help, per the data: localization improvements (median TP IoU
0.77 is already good; zero poor-localization TPs) and duplicate suppression
(only 2 duplicate FPs).

All thresholds above are **analysis parameters** (CLI-configurable), not
engineering requirements; no OD-01…OD-16 is resolved.

#### 4.3.1 Measured: 640 px inference experiment (no retraining)

Recommendation 2 was **tested**: `error_analysis.py --imgsz 640` over the same
36-image test split, identical matching parameters (IoU≥0.5, conf≥0.25).
Reports: `runs/ball_v2/error_analysis_640.json` + annotated images
(gitignored). Results:

| Metric (conf 0.25) | 320 px (baseline) | 640 px |
|---|---|---|
| Recall (all) | 0.360 | **0.507** |
| **Small-ball recall** | **0.125** | **0.417** (3.3×) |
| Normal-ball recall | 0.778 | 0.667 |
| Precision | 0.692 | 0.427 |
| FP count (background/near-miss/duplicate) | 12 (5/5/2) | 51 (33/9/10) |
| Latency (12-img avg, workstation CPU) | ~1073 ms/img | ~1239 ms/img (1.16×) |

Confidence sweep (both resolutions, same stored raw predictions):

| conf | 320 P/R | 640 P/R |
|---|---|---|
| 0.10 | 0.356 / 0.413 | 0.180 / **0.640** |
| 0.25 | 0.692 / 0.360 | 0.427 / 0.507 |
| 0.40 | 0.909 / 0.267 | 0.578 / 0.347 |

**Conclusion: resolution directly attacks the measured failure mode.** Small-ball
recall rose 0.125 → 0.417 (the remaining gap is now much smaller), and total
recall improved at every confidence level. The cost is background FPs from
low-confidence responses to small objects — at conf 0.10 precision is 0.18, at
0.40 it is 0.58. **There is no single dominant operating point:** the right
(conf, imgsz) choice depends on the robot's error costs (missing a ball vs
investigating a non-ball), which is exactly OD-01/OD-05 territory — left open.
The measured latencies are workstation-CPU numbers only, not a robot budget.
No model, dataset, or checkpoint was modified by this experiment.

#### 4.3.2 FP analysis at 640 px (51 FPs classified) — and what it means post-defect

All 51 FPs from the §4.3.1 640 px run were cross-referenced against the
**complete official COCO annotations** for the test images (all 80 categories,
from the on-disk val2017 JSON — no inference re-run needed). Classification
against the (tie) label set, with the tie-defect caveat applied:

| FP kind | n | % of 51 | Evidence |
|---|---|---|---|
| **Background** — no unmatched label object nearby | 32 | 63% | Of these: 23 overlap *no* annotated object ≥0.1 IoU (true background), 13 sit on COCO-annotated objects — **7 person** (heads/faces/bodies at 0.24–0.02 IoU; conf up to 0.54), **5 tie** (≤0.12 IoU, i.e. the *rest* of a tie whose knot region was the GT), 1 elephant |
| **Near-miss** — IoU≥0.1 with an unmatched GT | 9 | 18% | **All 9 are tie-knot regions**: 0.23–0.49 IoU with the GT (knot) box, conf 0.26–0.67 |
| **Duplicate** — repeat detection of a matched GT | 10 | 20% | Median box area 7.3% of image (vs GT knots ≪1%) — duplicate boxes are systematically much larger than the GT they repeat |

Interpretation **after** removing the tie contamination:

* The dominant *real* confusion is **person regions** (7 of 13 object-overlap
  background FPs; the 5 tie-overlap FPs and all 9 near-misses are artifacts of
  the tie labels and would not exist against ball GTs).
* Duplicates share a signature: box area ≫ GT area (median 0.073 vs the
  matched knot's ~0.001) — a scale-mismatched second detection. Real NMS
  should suppress these; worth re-checking after the corrected rebuild.
* **Hard negatives the future robot dataset must deliberately contain** (from
  the measured confusions): (1) people near/above small round objects — heads,
  hands, badge/logo circles on clothing; (2) small round objects that are NOT
  balls (buttons, knots, knobs, lights); (3) partial/occluded round objects at
  image edges; (4) background clutter at the size scale of distant balls.
  The corrected COCO rebuild supplies (1) and (4) naturally (person is
  present in most ball images); (2) and (3) are exactly what robot-footage
  labeling must add.

**Should 640 px remain the V3 resolution?** Yes, provisionally: the resolution
mechanism (more pixels per small object) is label-independent, and 640 is
YOLO11's native pretrained resolution. But the 3.3× small-recall figure was
measured against tie GTs — **re-run the 320-vs-640 error analysis after the
corrected V3 rebuild** and confirm the gain on real balls before committing to
a robot operating point (which remains OD-01/OD-05 territory).

**V3 dataset composition (corrected COCO rebuild):** official category 37
only; ~4,262 train / 169 val images already identified in the on-disk
annotation files (~5.6 k train ball boxes — no new download needed for the
image pool); keep the seeded split + val/test uncapped discipline; include
naturally-co-occurring persons as in-context negatives; preserve the
PROVENANCE + validation-domain separation. Robot-specific hard negatives
(§4.3 list items 2–3) come from future real footage, per §4.4.

### 4.4 REAL-ROBOT VALIDATION — mandatory separation

**Public-dataset metrics prove only that the training pipeline works.** They do not and
cannot prove robot-ready detection because the real deployment domain differs
(mounting, lighting, motion blur, court background, ball size at range).

Before any robot-confidence claim, the team must collect real robot/court footage and:

1. label it into the same single-class YOLO format,
2. re-run evaluation on it as a domain test set,
3. fine-tune on it (domain adaptation) if metrics demand.

Until step 1 happens, every metric reported by `evaluate.py` is a **public-dataset**
number and must be labelled as such. No file in this package claims otherwise.

### 4.5 Pipeline dry run (video → dataset, software-only, verified)

Before real footage exists, the complete robot-dataset chain was exercised
end-to-end with fixtures derived from existing V2 test images (16 frames as two
"sessions", synthetic MP4s via OpenCV, labels carried over from the COCO test
split — all clearly test fixtures, `datasets/dryrun/`, gitignored):

```
videos (2 MP4)  →  frames extract --stride 1   → 16 frames (8 per session)
                →  labels (carried from COCO test split)  → 40 boxes
                →  validate          → ok=True, 0 errors
                →  split --session-pattern "(sess[A-Z])_" --val-frac 0.5
                   → train = sessB only, val = sessA only (zero straddling)
                →  validate output   → ok=True for every split
                →  provenance        → PROVENANCE.md written (domain=robot, marked dry-run)
                →  dedupe live check → injected exact copy dropped ("1 dropped")
```

The tooling is ready for real footage; only the footage itself is missing.
This dry run is NOT robot validation — it validates the tooling.

### 4.6 Robot data collection specification (capture plan for when hardware arrives)

**Purpose.** Concrete capture scenarios targeting the measured V3 failure
mode (small/extreme-small balls) plus the coverage a domain-adaptation
dataset needs. All numbers below are capture targets — they are *dataset
design*, not hardware specifications; no camera model, mounting geometry, or
robot parameter is assumed (OD-01…OD-16 untouched).

**Camera settings (targets, adapt to the actual camera on arrival):**

* Record at the **native maximum resolution ≥ 1280×720** and export frames at
  the detector's operating resolution class (≥ 640 px on the long side).
  Rationale: 640 px is the operating resolution and the small-ball mechanism.
* **25–30 FPS** video. Detection runs on single frames; high FPS exists so
  motion-blurred and sharp frames of the same scene can both be harvested.
* Lock exposure/focus settings per session where possible and **write down
  what was used** — it goes into PROVENANCE.md. Record one reference clip of a
  known-size object at known distances per session as a scale reference.
* Mounting: capture from **at least two heights/positions** (e.g. a low
  robot-eye view and a higher human-held view) — until OD-01 fixes the real
  mount, diversity beats guessing it.

**Capture matrix — each scenario = ~2–3 min of footage at 2–3 distances
(near ≈ 1–2 m, mid ≈ 3–5 m, far ≈ 7–10 m, approximate and relative to the
court):**

| # | Scenario | Why (V3 evidence link) |
|---|---|---|
| S1 | Static ball, each distance band, centered | baseline; far band = small-ball evidence |
| S2 | Ball at image edges/corners at each distance | edge-clipped small balls |
| S3 | Ball partially occluded (behind legs, bags, court furniture) | visible-part labeling; occlusion robustness |
| S4 | Ball near/under people (standing, walking) | person confusion hard negatives |
| S5 | Multiple balls in frame (2–5), same and different distances | multi-ball scenes; overlapping-box labeling |
| S6 | Rolling/bouncing ball (slow and fast passes) | motion blur harvest |
| S7 | Camera moving (walk/pan) with static ball | robot-motion blur, ego-motion |
| S8 | Lighting sweep: lights on/off, blinds/curtains, overcast vs sunny if outdoor available | illumination domain shift |
| S9 | Shadow scenes: ball half in shadow, ball near shadow edges | shadows are a classic FP/FN source |
| S10 | Cluttered background: equipment piles, benches, bags, round objects (caps, knobs, cups) | hard negatives for background FPs |
| S11 | **Empty-court sweeps (no ball)** at each lighting/position | negative frames — target ≈ 20–30 % of all frames |
| S12 | Small round objects that are NOT balls, at ball-like distances (the hard-negative set) | directly attacks FP behaviour |
| S13 | Ball at extreme small scale: far band + zoomed-out framing until the ball is a few pixels | **the priority scenario** — 8/66 V3 test balls were invisible even at conf 0.01 |
| S14 | Indoor and (if available) outdoor sessions | domain robustness |
| S15 | Throw/kick sessions with the ball approaching the camera | approaching-ball appearance change |

**Deliberately difficult cases (record explicitly, log in PROVENANCE):** balls
at the edge of visibility; ball against a similarly-colored background patch;
white/bright balls on bright floors; dark balls in shadow; balls behind net
or fence mesh; ball half-cut by the frame boundary; reflections of balls
(labeled NO — reflections are not balls); multiple balls where one is at 1 m
and another at 10 m in the same frame.

**Session discipline (what makes the data auditable):** one directory per
session, filenames `sess<id>_<frame>` (the split tool's `--session-pattern`
consumes this); a `notes.txt` per session (date, location, lighting,
camera position, anomalies); no scene change mid-session.

**Volume target:** ≈ 30–60 min raw footage ≈ 3–6 k candidate frames → after
stride/dedupe ≈ 1.5–3 k labeled frames. Enough for domain fine-tuning, not
for from-scratch training (which remains forbidden — transfer learning only).

### 4.7 V3 baseline (current authoritative detector) and V4 experiment plan (PLAN ONLY — not run)

**V3 baseline — the current authoritative numbers** (corrected cat-37 COCO
dataset, 42-image test split, `runs/ball_v3/weights/best.pt`, epoch 15/15,
conf 0.25, IoU 0.5):

| Metric | V3 @ 320 px | V3 @ 640 px |
|---|---|---|
| Precision | 0.800 | 0.786 (formal test eval: 0.749) |
| Recall | 0.303 | 0.500 (val split, epoch 15: 0.589) |
| Small-ball recall | 0.246 | 0.474 |
| mAP50 / mAP50-95 | — | — (formal test eval: 0.547 / 0.378; val split epoch 15: 0.604 / 0.414) |

Failure modes (measured): small-ball confidence calibration (15/66 GTs have
≥0.5-IoU candidates below conf 0.25), 8/66 balls invisible even at conf 0.01,
normal-ball recall flat at 0.667, localization already strong (median TP IoU
0.85), duplicates/near-misses negligible. All V2-era numbers (ties dataset)
are deprecated — do not quote them.

**V4 experiment plan (frozen before any run — compare against V3 @ 640 px):**

*Fixed (identical to V3, so the comparison isolates the change):* model
`yolo11n.pt` transfer learning (never from scratch), `data` = corrected
coco-ball-v3, `imgsz=640`, `batch=8`, `seed=20260906`, `deterministic=true`,
`device=cpu`, same V3 test split (42 imgs / 66 balls) and the same
error-analysis methodology (conf 0.25 / IoU 0.5, small-ball fraction 0.005).

*Changed:* `fraction=1.0` (full 4,262-image train set — requires one
uninterrupted ~24 h machine window at the measured ~20 min/epoch × 15
epochs; NOT chunkable on this workstation because ultralytics discards
partial epochs), `epochs=40` with `patience=10` (V3's fitness was still
climbing at epoch 15; the longer schedule targets the small-ball calibration
deficit). No other hyperparameter changes — one variable at a time.

*Metrics to compare (V4 vs V3, same test split):* precision, recall, mAP50,
mAP50-95, small-ball recall, normal-ball recall, invisible-ball count
(best-IoU≥0.5 at conf 0.01), FP count by category, latency (workstation;
OD-05 budget remains open).

*Acceptance criteria — V4 is better than V3 iff:* recall ≥ +0.05 absolute
AND precision does not drop > 0.05 AND small-ball recall improves ≥ +0.05
AND mAP50-95 improves. (Rationale: the plan's purpose is the small-ball
failure mode; a pure precision trade would not serve it.)

*Stop-retraining rule:* if V4 meets the acceptance criteria, adopt it and
move effort to **robot-footage collection + domain validation** (§4.4) —
public-data iterations have hit diminishing returns. If V4 misses the
criteria on a clean run, do NOT iterate further on COCO: the COCO domain is
exhausted for this failure mode; collect robot data first.

### 4.8 V2 status: public-dataset validated, NOT robot-ready

Ball Detection V2 was trained and evaluated on real public COCO sports-ball
data. That validates the **pipeline** (download → convert → train → evaluate →
inference → perception boundary) on real photographs, nothing more. The
numbers in §4.2 are **public-dataset metrics, not robot validation**:real court/robot footage collection, labeling, domain evaluation, and fine-tuning
(§4.3) remain mandatory before any deployment claim. Latency was measured on
a workstation CPU and implies nothing about the robot's compute budget (OD-05
open). No OD-01…OD-16 is resolved by any of this work. The §4.3.1 640 px
experiment likewise changes an **analysis/operating parameter**, not any
documented decision.

## 5. Labeling guide (ball bounding boxes)

This guide governs **future robot-footage labeling** (the `robot_dataset` package
assumes exactly this convention). It adds no hardware assumptions — it constrains
only how a human labels frames in image space.

* **One class only: `ball` (class id `0`).** No other class may appear in a label
  file; the validators reject them.
* **Format:** one `.txt` per image, same basename as the image; one object per
  line: `class_id cx cy w h`, all normalized to `[0, 1]` relative to image
  width/height. Empty file (or no file) = negative frame (no ball) — both are
  valid and both are needed for calibration of false-positive behaviour.
* **What is a ball:** every real ball fully or partially visible in the frame,
  however small or distant, as long as a human can point at it. If you can see
  *any* ball-shaped evidence, label it. **Tiny and distant balls are the most
  valuable examples in the robot dataset**: the V3 error analysis (corrected
  cat-37 dataset, 42-image test split) measured small-ball recall at 0.474
  (640 px) and found 8/66 balls completely invisible even at confidence 0.01 —
  the dominant failure mode. Under-labeling small balls would reproduce
  exactly that failure in V4. Only skip a ball if you genuinely cannot point
  at it (then flag the frame per *Ambiguity* below).
* **Edge-clipped balls:** label them. Tight box around the visible part; the
  box may touch the image border (validators accept boxes partially outside —
  they reject only fully-outside boxes). Frames with balls clipped by the
  frame edge are deliberately valuable: the robot's camera will see them
  constantly near image boundaries.
* **Overlapping / multiple balls:** label each ball with its own box, even
  when balls overlap each other. Draw each tight box around its own ball;
  where two balls overlap, give each the box you would draw if the other were
  absent (estimate the occluded boundary from the visible arc). Do NOT merge
  two touching balls into one box.
* **Box extent:** tight bounding rectangle around the *visible* portion of the
  ball, including motion blur around it; do not pad, do not include hands,
  equipment, shadows on the floor, or the ball's reflection.
* **Occlusion:** label the visible part only. If roughly ≥ half the ball is
  visible, label it; if less, skip it (do not guess the hidden extent).
* **Hard negatives (frames WITHOUT ball labels):** empty/negative frames are
  first-class training data — they calibrate false-positive behaviour. Every
  capture scenario in the collection plan includes deliberate no-ball frames,
  and **hard-negative scenes containing small round objects that are NOT the
  ball** (door knobs, bottle caps, ball-style lamp globes, court markings,
  round lights, people's heads at distance). Label NOTHING in a negative
  frame — the emptiness is the signal. If a scene contains a real ball, it is
  not a negative frame.
* **Minimum labeling rules (per session):** (a) every exported frame must have
  a label file, even if empty; (b) every ball a human can point at gets a box;
  (c) boxes are tight (no padding); (d) class id is always `0`; (e) ambiguous
  objects are flagged, never guessed. Frames violating (a)–(d) fail the
  validators; (e) is a human review step.
* **Ambiguity:** if the object *might* be a ball but cannot be confirmed
  (distant blob, partial glimpse), do **not** label it — note the frame in the
  review notes instead. When in doubt, leave it out and flag it.
* **Duplicates:** near-duplicate frames are filtered by the split tooling, but
  do not intentionally label the same physical ball twice in near-identical
  frames; keep one representative frame per scene state.
* **Split integrity:** frames from the same recording session must not straddle
  train/val/test — use `robot_dataset.split --session-pattern "(sess\d+)_"`
  (regex extracting the session id from filenames) so whole sessions are
  assigned to one split and the test set measures generalization, not
  memorized footage. Verified property: the session-grouped planner never
  assigns frames of one session to two splits (unit-tested; exercised in the
  §4.5 dry run).

### 5.1 Quality-control procedure (every labeling batch)

1. **Machine validation** — run `robot_dataset.validate` on the labeled
   directory; every error (malformed line, class id, out-of-bounds box,
   unpaired file) must be fixed before proceeding.
2. **Dataset audit** — run `robot_dataset.audit` on the assembled dataset:
   cross-split contamination, session leakage, and missing splits are hard
   failures; suspicious tiny/huge boxes are listed for human review. The audit
   never deletes data — a human adjudicates every flag.
3. **Tiny-box review** — for every `suspicious_tiny_boxes` flag: confirm the
   box is a real (tiny) ball and not a mislabel. Keep confirmed tiny balls —
   they are the priority evidence; delete only confirmed mislabels (by hand,
   never automatically).
4. **Spot-check render** — annotate a random sample (≥ 10 frames or 10 %,
   whichever is larger) with drawn boxes and eyeball them: boxes tight, no
   missed balls, no labels on non-balls.
5. **Negative-frame balance** — check the negative (empty-label) frame ratio;
   the collection plan targets roughly 20–30 % negatives. Out of range →
   capture more of the missing kind.
6. **Provenance** — write/update `PROVENANCE.md` (`robot_dataset.provenance`,
   `--domain robot`) with session list, dates, camera context, and any known
   capture quirks before any training run consumes the data.

## 6. Reproducible commands

```bash
# (one-time) prepare data. SYNTHETIC circle fixtures for the machinery proof
# (positive samples; COCO8 itself has NO sports-ball positives). For the real
# public-data run use --coco-json with official COCO annotations (§4.2).
cd ai
.venv/Scripts/python.exe detection/data/prepare_dataset.py --synthetic --out ai/datasets/coco8-ball

# train (transfer learning from yolo11n.pt, single 'ball' class, seeded)
.venv/Scripts/python.exe detection/train.py --config detection/configs/ball_v1.yaml

# ---- V2 (real COCO data) ----
# 1. download ball-annotated COCO images only (~1.3 GB, resumable, sha256 manifest)
python detection/data/download_coco_balls.py --data-dir ai/datasets/coco-ball-v2/_raw --workers 10
# 2. convert + split (seeded; train capped at 2500 as a CPU budget)
.venv/Scripts/python.exe detection/data/prepare_dataset.py \
    --coco-json ai/datasets/coco-ball-v2/_raw/annotations/instances_train2017.json \
    --coco-val-json ai/datasets/coco-ball-v2/_raw/annotations/instances_val2017.json \
    --images-dir ai/datasets/coco-ball-v2/_raw --out ai/datasets/coco-ball-v2 --max-train-images 2500
# 3. train (long CPU runs: --resume continues from runs/<name>/weights/last.pt)
# V3 (corrected cat-37 dataset): create configs/ball_v3.yaml mirroring ball_v2.yaml
# with data: ai/datasets/coco-ball-v3/data.yaml before training.
.venv/Scripts/python.exe detection/train.py --config detection/configs/ball_v2.yaml
.venv/Scripts/python.exe detection/train.py --config detection/configs/ball_v2.yaml --resume
# 4. evaluate (see runs/ball_v2/eval_report.json for the recorded run)
.venv/Scripts/python.exe detection/evaluate.py --weights ../runs/ball_v2/weights/best.pt \
    --data ai/datasets/coco-ball-v2/data.yaml --split test --report ../runs/ball_v2/eval_report.json

# evaluate (precision/recall/mAP + latency → JSON report under repo-root runs/)
.venv/Scripts/python.exe detection/evaluate.py --weights ../runs/ball_v1/weights/best.pt \
    --data datasets/coco8-ball/data.yaml --report ../runs/ball_v1/eval_report.json

# single-image inference → perception payload
.venv/Scripts/python.exe detection/inference.py --weights ../runs/ball_v1/weights/best.pt \
    ../datasets/coco8-ball/images/test/synth_test_0000.jpg
```

Determinism: fixed `seed` in the config; ultralytics `deterministic=True`. Data splits
are generated with a fixed seed. CPU-vs-GPU nondeterminism may still vary results
slightly across machines.

## 7. Files

| File | Purpose |
|---|---|
| `requirements.txt` | Pinned ML deps for `ai/.venv` (CPU torch index documented) |
| `configs/ball_v1.yaml` | Training config — pipeline-proof hyperparameters, explicitly NOT tuned |
| `data/prepare_dataset.py` | COCO → single-class YOLO conversion + seeded splits |
| `train.py` | Transfer-learning training entry point |
| `evaluate.py` | Metrics + latency → JSON report |
| `inference.py` | Detector wrappers, `BallDetection`, bbox math, perception payload conversion |
| `error_analysis.py` | TP/FP/FN matching + failure-mode taxonomy → JSON + annotated images (§4.3) |
| `robot_dataset/` | Robot-footage → dataset tooling: frame extraction, label validation, session-aware splits, provenance, **whole-dataset audit** (§5 labeling guide, §4.6 capture plan) |
| `tests` (repo `tests/test_ball_detection.py`, `tests/test_error_analysis.py`, `tests/test_robot_dataset.py`) | Fake-detector-at-seam unit + E2E tests (no ML deps) |

Datasets (`ai/datasets/`, gitignored), trained weights (`ai/runs/`, `*.pt`, gitignored), and
downloaded archives are **not committed** — nothing large enters the repository.

## 8. Open decisions respected

OD-01 (ball world position / approach), OD-05 (latency budget — the measured CPU latency
here is a workstation number, not a robot budget), and all other ODs remain **OPEN**.
This package invents no pins, sensors, thresholds, dimensions, or calibration values.
