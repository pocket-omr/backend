"""OMR grading seam.

This module is the single integration point for the OMR grading model.
Everything around it (image upload, MinIO storage, StudentSubmission
lifecycle, aggregate recomputation, mobile wiring) is already implemented and
works end-to-end.

`grade_sheet` runs the answer-bubble pipeline: segment the sheet, detect bubbles
with YOLO, classify each filled/empty with the CNN, and compare the detected
answers against the teacher's key (Question.correct_answer). If the models or
the sheet can't be processed, it raises `GradingNotAvailable` so the sheet is
recorded as PENDING and can be re-graded later (ExamService.regrade_pending).
"""

import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from app.models.exam import Exam

logger = logging.getLogger("uvicorn.error")

# Process-wide singleton so the YOLO + CNN models load only once.
_grader = None
_grader_failed = False


@dataclass
class GradingResult:
    """What the grading model returns for a single scanned sheet.

    - answers: detected choice index per question, ordered like exam.questions
      (Question.order_index). Use None for blank / unreadable.
    - score / max_score: raw points. If omitted (defaults 0), the caller will
      derive them by comparing `answers` against each Question.correct_answer.
    - confidence: 0..100 detection confidence for the sheet.
    - first_name / last_name / student_id: student identity if the model reads
      it off the sheet; otherwise leave blank and assign later.
    """

    answers: list[int | None] = field(default_factory=list)
    score: int = 0
    max_score: int = 0
    confidence: float = 0.0
    first_name: str = ""
    last_name: str = ""
    student_id: str = ""
    # Human-review flag: the model was unsure of one or more answers.
    needs_review: bool = False
    # 1-based question numbers that need review.
    flagged_questions: list[int] = field(default_factory=list)


class GradingNotAvailable(Exception):
    """Raised by grade_sheet while the model is not yet implemented.

    The caller catches this and records the sheet as a PENDING submission
    instead of failing the upload.
    """


def _correct_set(question) -> set:
    """Correct choice indices for a question (supports multiple correct answers).

    Prefers the multi-value `correct_answers`; falls back to the legacy single
    `correct_answer` for exams created before multi-answer support.
    """
    answers = getattr(question, "correct_answers", None)
    if answers:
        return {int(a) for a in answers}
    single = getattr(question, "correct_answer", None)
    return {int(single)} if single is not None else set()


def _get_grader():
    """Lazily build the bubble grader singleton (loads YOLO + CNN once)."""
    global _grader, _grader_failed
    if _grader is not None:
        return _grader
    if _grader_failed:
        return None
    try:
        from app.core.config import settings
        from app.recognition.bubble_grading import BubbleGrader

        _grader = BubbleGrader(
            settings.bubble_yolo_path,
            settings.bubble_cnn_path,
            conf=settings.bubble_conf_threshold,
            iou=settings.bubble_iou_threshold,
            review_threshold=settings.bubble_review_threshold,
        )
        logger.info("Bubble grading models loaded.")
    except Exception as e:  # missing models / ultralytics not installed
        _grader_failed = True
        logger.error("Bubble grading unavailable: %s", e)
        return None
    return _grader


def _segment(image_bytes: bytes):
    """Segment a sheet photo once; return the crops grading needs, or None.

    Returns a dict with:
    - detect: bold ``answers.png`` (best for YOLO bubble detection),
    - classify: colour ``answers_color.png`` (preserves filled/empty for the CNN),
    - personal_info: colour ``personal_info_color.png`` (for name recognition).
    """
    import cv2

    from app.recognition.sheet_pipeline_v11 import process_sheet

    with tempfile.TemporaryDirectory(prefix="omr_grade_") as tmp:
        img_path = Path(tmp) / "sheet.png"
        img_path.write_bytes(image_bytes)
        try:
            info = process_sheet(str(img_path), tmp)
        except Exception:
            return None
        if not info.get("ok"):
            return None
        regions = Path(tmp) / info["name"] / "regions"

        def _read(name):
            p = regions / name
            return cv2.imread(str(p)) if p.is_file() else None

        detect = _read("answers.png")
        classify = _read("answers_color.png")
        if detect is None:
            detect = classify
        if detect is None:
            return None
        if classify is None:
            classify = detect
        return {
            "detect": detect,
            "classify": classify,
            "personal_info": _read("personal_info_color.png"),
        }


# Lazily-loaded name-recognition assets (TorchScript char model + labels).
_reco = None
_reco_failed = False


def _get_recognition():
    """Load the name-recognition assets once (char model + idx_to_char)."""
    global _reco, _reco_failed
    if _reco is not None:
        return _reco
    if _reco_failed:
        return None
    try:
        from app.core.config import settings
        from app.services.name_recognition import load_recognition_assets

        _reco = load_recognition_assets(settings.recognition_models_dir)
    except Exception as e:
        _reco_failed = True
        logger.error("Name recognition unavailable: %s", e)
        return None
    return _reco


def _match_student(predicted: dict, students) -> "object | None":
    """Fuzzy-match recognised name fields against the EXAM's student roster
    (the Excel the teacher uploaded at exam creation). Returns the best
    ExamStudent or None."""
    from app.recognition.fuzzy_match import _norm_name, match

    catalogue = [
        {
            "_student": s,
            "first_name": _norm_name(s.first_name),
            "last_name": _norm_name(s.last_name),
            "group": str(s.group_name or ""),
            "registration_number": str(s.registration_number or ""),
        }
        for s in students
    ]
    if not catalogue:
        return None
    hits = match(predicted, catalogue, top_k=1)
    return hits[0][1]["_student"] if hits else None


def _identify_student(seg: dict, exam: Exam, result: GradingResult) -> None:
    """Recognise the handwritten name and match it to an exam student; write the
    identity onto `result`. Best-effort — silently skips if unavailable."""
    students = getattr(exam, "students", None) or []
    if not students or seg.get("personal_info") is None:
        return
    reco = _get_recognition()
    if reco is None:
        return
    try:
        from app.services.name_recognition import _recognize_personal_info

        predicted = _recognize_personal_info(reco, seg["personal_info"])
        matched = _match_student(predicted, students)
    except Exception as e:  # recognition/matching must never fail grading
        logger.warning("Name identification failed: %s", e)
        return
    if matched is not None:
        result.first_name = matched.first_name or ""
        result.last_name = matched.last_name or ""
        result.student_id = str(matched.registration_number or "")


def grade_sheet(image_bytes: bytes, exam: Exam) -> GradingResult:
    """Grade one scanned answer sheet against the teacher's answer key.

    Pipeline: segment the sheet -> detect bubbles (YOLO) -> classify
    filled/empty (CNN) -> per-question answer -> compare to
    Question.correct_answer.

    Args:
        image_bytes: raw bytes of a single answer-sheet image (jpg/png).
        exam: the Exam ORM object. Answer key is exam.questions[*].correct_answer
            (ordered by order_index); exam.choices_per_question is the bubble
            count per row.

    Returns:
        GradingResult with detected answers, score and confidence.

    Raises:
        GradingNotAvailable: if the models aren't available or the sheet can't
            be segmented (caller records the sheet as PENDING).
    """
    # Segment first (cheap, no model load) so unreadable sheets fall back fast.
    seg = _segment(image_bytes)
    if seg is None:
        raise GradingNotAvailable("could not segment the answers region")

    grader = _get_grader()
    if grader is None:
        raise GradingNotAvailable("bubble grading models not available")

    questions = sorted(exam.questions, key=lambda q: q.order_index)
    num_questions = len(questions)
    num_choices = exam.choices_per_question or 4
    if num_questions == 0:
        raise GradingNotAvailable("exam has no questions to grade against")

    read = grader.grade(
        seg["detect"], num_questions, num_choices, classify_bgr=seg["classify"]
    )

    # Score: each keyed question is worth its `points` (default 1) when the
    # student's filled set EXACTLY equals the correct set (supports multiple
    # correct answers — the student must mark all of them and nothing else).
    filled = read.filled or [[] for _ in questions]
    max_score = sum(q.points for q in questions if _correct_set(q))
    score = sum(
        q.points
        for i, q in enumerate(questions)
        if _correct_set(q)
        and i < len(filled)
        and set(filled[i]) == _correct_set(q)
    )

    result = GradingResult(
        answers=read.answers,
        score=score,
        max_score=max_score,
        confidence=read.confidence,
        needs_review=read.needs_review,
        flagged_questions=read.flagged_questions,
    )
    # Identify the student by recognising the name and matching it to the exam's
    # uploaded student roster.
    _identify_student(seg, exam, result)
    return result
