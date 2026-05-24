"""OMR grading seam.

This module is the single integration point for the OMR grading model.
Everything around it (image upload, MinIO storage, StudentSubmission
lifecycle, aggregate recomputation, mobile wiring) is already implemented and
works end-to-end: until `grade_sheet` is implemented, uploaded sheets are
stored and recorded as PENDING submissions, and can be graded later by
re-running the grader (see ExamService.regrade_pending).

To plug in the real model, implement `grade_sheet(...)` below so it returns a
`GradingResult`. Do not change the signature without updating
ExamService.upload_images / regrade_pending accordingly.
"""

from dataclasses import dataclass, field

from app.models.exam import Exam


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


class GradingNotAvailable(Exception):
    """Raised by grade_sheet while the model is not yet implemented.

    The caller catches this and records the sheet as a PENDING submission
    instead of failing the upload.
    """


def grade_sheet(image_bytes: bytes, exam: Exam) -> GradingResult:
    """Grade one scanned answer sheet. IMPLEMENTED BY THE OMR MODEL OWNER.

    Args:
        image_bytes: raw bytes of a single grid-sheet image (jpg/png).
        exam: the Exam ORM object. The answer key is exam.questions[*]
            .correct_answer (ordered by order_index); exam.choices_per_question
            and exam.grid_layout describe the sheet geometry.

    Returns:
        GradingResult for the sheet.

    Raises:
        GradingNotAvailable: while no model is wired in (current default).
    """
    raise GradingNotAvailable("OMR grading model not implemented yet")
