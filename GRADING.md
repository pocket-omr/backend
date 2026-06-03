# OMR Grading

This backend has two grading pipelines:

1. **Answer-bubble grading** (`app/services/grading.py` + `app/recognition/bubble_grading.py`)
   — scores a student's filled MCQ bubbles against the teacher's answer key.
   Used by the mobile upload flow (`POST /api/v1/exams/{id}/upload-images`).
2. **Name recognition + roster matching** (`/roster`, `/grade`) — identifies the
   student from handwritten name fields. Documented further below.

---

## Answer-bubble grading

`grade_sheet(image_bytes, exam)` does, per sheet:

1. **Segment** the photo (`sheet_pipeline_v11.process_sheet`) and take the warped
   `answers.png` region.
2. **Detect** every bubble with a YOLOv8 detector (`bubble_yolo.pt`).
3. **Order** the boxes into a (question, choice) grid (`assign_grid`,
   multi-block aware).
4. **Classify** each 64×64 crop filled/empty with a TorchScript CNN
   (`bubble_cnn_scripted.pt`, sigmoid > 0.5).
5. **Score**: a question is correct when exactly one bubble is filled and it
   equals `Question.correct_answer`. Zero or multiple filled → unanswered (wrong).
   Each correct question is worth its `Question.points` (teacher-set in the web
   app, default 1); `max_score` is the sum of points over keyed questions.
6. **Review flag**: a question is flagged for human review when the model's
   filled/empty decision on any of its bubbles is uncertain — decision
   confidence `max(p, 1-p) < BUBBLE_REVIEW_THRESHOLD` (default **0.80**) — or the
   student double-marked. The result carries `needs_review` + `flagged_questions`
   (1-based), persisted on the submission and surfaced per student in the mobile
   results (orange flag + "Review Q2, Q5"). Returns
   `GradingResult(answers, score, max_score, confidence, needs_review, flagged_questions)`.

The YOLO + CNN load once (process-wide singleton). If the models are missing,
`ultralytics` isn't installed, or a sheet can't be segmented, `grade_sheet`
raises `GradingNotAvailable` and the sheet is recorded **PENDING** — it can be
graded later via `POST /api/v1/exams/{id}/regrade`.

**Models** live in `recognition_models/` (configurable via env):

| File | Setting | What |
|------|---------|------|
| `bubble_yolo.pt` | `BUBBLE_YOLO_PATH` | YOLOv8 bubble detector |
| `bubble_cnn_scripted.pt` | `BUBBLE_CNN_PATH` | filled/empty CNN (TorchScript) |

Thresholds: `BUBBLE_CONF_THRESHOLD` (0.30), `BUBBLE_IOU_THRESHOLD` (0.30),
`BUBBLE_REVIEW_THRESHOLD` (0.80, the filled/empty certainty below which a
question is flagged for review).

Install needs `ultralytics` (in `requirements.txt`); CPU inference is fine.

---

# OMR Sheet Grading (name recognition + roster matching)

Grades scanned OMR answer sheets by reading the student's **handwritten name
fields** (first name, last name, group, registration number) and matching them
against a teacher-uploaded roster.

Pipeline per uploaded image:

1. **Segment** the photographed sheet (`sheet_pipeline_v11.process_sheet`) →
   warps the page and writes region crops + `report.json` (incl. decoded QR).
2. **Detect** the personal-info character cells on `personal_info_color.png`
   (`name_preprocess.detect_boxes` → `assign_fields`).
3. **Recognise** each non-empty cell with the TorchScript MobileNetV3 char model
   (`binarize_crop` → `(1,1,48,48)` tensor → softmax → `idx_to_char`).
4. **Post-process** each field (look-alike letter/digit correction).
5. **Match** the four predicted fields against the roster (QR fast-path, else
   weighted Levenshtein where `registration_number` dominates).

## Assets

Inference loads two files from `recognition_models/` (path configurable via
`RECOGNITION_MODELS_DIR`), generated from the trained checkpoint in `../models/`:

| File | What |
|------|------|
| `MobileNetV3_scripted.pt` | TorchScript char classifier (loaded with `torch.jit.load`) |
| `name_reco_labels.json` | `idx_to_char`, `img_size`, `normalization`, `max_field_lengths`, `known_fields` |

### Build / regenerate the assets

The on-disk checkpoint `../models/omr_MobileNetV3_best.pt` is a **bare
`state_dict`** (36 classes), not a scripted model, and the label mapping was not
saved with it. Generate the serving assets with:

```bash
python scripts/build_recognition_assets.py
```

> ⚠️ **Label mapping.** The alphabet is 38 classes (`' '`, `'-'`, `0-9`, `A-Z`)
> but the trained head has **36**, so **2 rare classes were dropped** during
> training and the surviving `idx_to_char` was never persisted. Without it the
> script writes a **provisional** mapping (flagged in the JSON, in the startup
> log, and in every `/grade` response via a `warning` field). Recognition output
> is **not reliable** until you supply the real mapping:
>
> ```bash
> # Authoritative: replay the drop on the original ground-truth labels
> python scripts/build_recognition_assets.py --csv path/to/omr_dataset.xlsx
> # Or pass the 2 dropped characters explicitly
> python scripts/build_recognition_assets.py --dropped "Q,W"
> ```

## Install

```bash
pip install -r requirements.txt   # adds torch, torchvision, opencv, pandas, numpy
```

CPU inference is fine. The model + labels load **once at startup**; if the
assets are missing the API still starts (auth/exams/pdf keep working) and
`/grade` returns `503` with a clear message — run the build script above.

## Run

```bash
fastapi dev app/main.py --port 8000
```

## Endpoints

Both are mounted at the root (no `/api/v1` prefix, no auth — same as
`/generate-pdf`).

### `POST /roster` — upload the class list (.xlsx)

Required columns (headers are lowercased, trimmed, spaces→underscores):
`first_name`, `last_name`, `group`, `registration_number`.
Missing columns → `400` listing required vs found. Extra columns are ignored.
`registration_number` is the unique student identifier returned as `match_id`.

```bash
curl -s -X POST http://localhost:8000/roster \
  -F "file=@class_2A.xlsx"
```

```json
{
  "rows": 42,
  "column_mapping": {"First Name": "first_name", "Registration Number": "registration_number", "...": "..."},
  "fields": ["first_name", "last_name", "group", "registration_number"]
}
```

The built catalogue is held in app state, so upload the roster before grading.

### `POST /grade` — grade one or many sheets

Each upload may be a single image **or a `.zip` archive of images** (nested
folders are fine; non-image members are ignored). You can also mix loose images
and zips in one request.

```bash
# one sheet
curl -s -X POST http://localhost:8000/grade \
  -F "files=@scan_001.jpg"

# many sheets in one request
curl -s -X POST http://localhost:8000/grade \
  -F "files=@scan_001.jpg" -F "files=@scan_002.jpg" -F "files=@scan_003.jpg"

# a zip of scans (e.g. a whole class exported from the phone)
curl -s -X POST http://localhost:8000/grade \
  -F "files=@class_2A_scans.zip"
```

A zip containing no images returns `400`. Each extracted image is graded
independently and appears as its own entry in `results` (keyed by the member's
filename).

```json
{
  "count": 1,
  "results": [
    {
      "filename": "scan_001.jpg",
      "predicted": {
        "first_name": "MOHAMMED", "last_name": "ELAMRANI",
        "group": "01", "registration_number": "00123456"
      },
      "matched": {
        "first_name": "MOHAMMED", "last_name": "ELAMRANI",
        "group": "01", "registration_number": "00123456"
      },
      "match_id": "00123456",
      "score": 0.0,
      "qr_data": null,
      "match_source": "fuzzy"
    }
  ]
}
```

- `match_source`: `"fuzzy"` (weighted Levenshtein roster match) or `"none"`
  (empty roster). `match_id` is the matched student's `registration_number`.
- `qr_data` is the sheet's decoded QR (exam metadata: title/module/#questions/
  #choices) — informational; it is not used for student matching.
- Lower `score` = better match.
- A sheet with no detectable name cells returns
  `{"filename": "...", "error": "No character boxes detected ..."}` instead of
  failing the whole batch.

## Errors

| Situation | Response |
|-----------|----------|
| Roster not uploaded yet | `400` "No roster uploaded yet…" |
| Roster missing columns | `400` with `required` / `found` lists |
| Recognition assets missing/unloadable | `503` "Recognition model is unavailable: …" |
| A single sheet can't be segmented / has no boxes | `200`, that sheet's entry has an `error` field |
