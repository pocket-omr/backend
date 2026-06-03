import io
import uuid
import zipfile
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.exam import (
    Choice,
    Exam,
    ExamStatus,
    ExamStudent,
    Question,
    StudentSubmission,
)
from app.schemas.exam import (
    ChoiceOut,
    ExamCreate,
    ExamFormOut,
    ExamOut,
    ExamUpdate,
    FailedSheetOut,
    HistoryExamOut,
    MobileExamOut,
    QuestionOut,
    StudentOut,
    StudentResultOut,
)
from app.core.config import settings
from app.services import storage
from app.services.base import ServiceError
from app.services.grading import GradingNotAvailable, GradingResult, grade_sheet

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def _is_image(name: str) -> bool:
    return any(name.lower().endswith(ext) for ext in IMAGE_EXTENSIONS)


def _content_type(name: str) -> str:
    for ext, ct in _CONTENT_TYPES.items():
        if name.lower().endswith(ext):
            return ct
    return "application/octet-stream"


def _extract_images(files: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    """Flatten uploaded files into (filename, bytes) image pairs.

    Each upload may be a single image or a .zip archive of images; zips are
    expanded and their non-image members ignored.
    """
    images: list[tuple[str, bytes]] = []
    for filename, content in files:
        if filename.lower().endswith(".zip") or zipfile.is_zipfile(io.BytesIO(content)):
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                for member in zf.namelist():
                    if member.endswith("/") or not _is_image(member):
                        continue
                    base = member.rsplit("/", 1)[-1]
                    # Skip macOS zip cruft: __MACOSX/ entries and AppleDouble
                    # "._name" resource-fork files (not real images).
                    if "__MACOSX" in member or base.startswith("._"):
                        continue
                    images.append((base, zf.read(member)))
        elif _is_image(filename):
            images.append((filename, content))
    return images


def _segmentation_reason(content: bytes, filename: str) -> str | None:
    """Why a sheet failed segmentation, or None. Skips the check (returns None)
    if the OpenCV recognition deps aren't installed in this environment."""
    try:
        from app.recognition.segmentation_check import segmentation_failure_reason
    except Exception:  # recognition deps unavailable -> don't block uploads
        return None
    try:
        return segmentation_failure_reason(content, filename)
    except Exception:
        # Never let the check itself fail an upload; treat as "couldn't read".
        return "could not process image"


def _build_student(exam_id: uuid.UUID, s) -> ExamStudent:
    """Build an ExamStudent from an incoming StudentIn record.

    `name` is kept as the joined full name for backward compatibility with
    consumers that still read the single-name column.
    """
    return ExamStudent(
        exam_id=exam_id,
        first_name=s.firstName,
        last_name=s.lastName,
        group_name=s.group,
        registration_number=s.registrationNumber,
        name=f"{s.firstName} {s.lastName}".strip(),
    )


def _parse_date(d: str) -> date | None:
    if not d:
        return None
    try:
        return date.fromisoformat(d)
    except ValueError:
        return None


def _exam_to_response(exam: Exam) -> ExamOut:
    questions_out = []
    for q in exam.questions:
        choices_out = [ChoiceOut(text=c.text) for c in q.choices]
        questions_out.append(
            QuestionOut(
                id=q.id,
                text=q.text,
                choices=choices_out,
                correct=_question_correct(q),
                points=q.points,
            )
        )

    form = ExamFormOut(
        title=exam.title,
        module=exam.module,
        university=exam.university,
        department=exam.department,
        date=str(exam.exam_date) if exam.exam_date else "",
        duration=exam.duration,
        numQuestions=str(exam.num_questions),
        choices=str(exam.choices_per_question),
        questionsPerPage=str(exam.questions_per_page),
        instructions=exam.instructions,
    )

    students = [
        StudentOut(
            firstName=s.first_name,
            lastName=s.last_name,
            group=s.group_name,
            registrationNumber=s.registration_number,
        )
        for s in exam.students
    ]

    return ExamOut(
        id=exam.id,
        form=form,
        questions=questions_out,
        checkboxType=exam.checkbox_type,
        gridLayout=exam.grid_layout,
        students=students,
        createdAt=exam.created_at,
        updatedAt=exam.updated_at,
    )


def _eager_exam_query():
    return (
        select(Exam)
        .options(
            selectinload(Exam.questions).selectinload(Question.choices),
            selectinload(Exam.students),
        )
    )


class ExamService:
    @staticmethod
    async def create(db: AsyncSession, user_id: uuid.UUID, payload: ExamCreate) -> ExamOut:
        form = payload.form
        exam = Exam(
            user_id=user_id,
            title=form.title,
            module=form.module,
            university=form.university,
            department=form.department,
            exam_date=_parse_date(form.date),
            duration=form.duration,
            num_questions=int(form.num_questions) if form.num_questions else 0,
            choices_per_question=int(form.choices) if form.choices else 4,
            questions_per_page=int(form.questions_per_page) if form.questions_per_page else 20,
            instructions=form.instructions,
            checkbox_type=payload.checkbox_type,
            grid_layout=payload.grid_layout,
        )
        db.add(exam)
        await db.flush()

        for qi, q_in in enumerate(payload.questions):
            correct = sorted(set(q_in.correct or []))
            question = Question(
                exam_id=exam.id,
                order_index=qi,
                text=q_in.text,
                correct_answers=correct,
                correct_answer=correct[0] if correct else None,
                points=q_in.points,
            )
            db.add(question)
            await db.flush()

            for ci, c_in in enumerate(q_in.choices):
                db.add(Choice(question_id=question.id, order_index=ci, text=c_in.text))

        for s_in in payload.students:
            db.add(_build_student(exam.id, s_in))

        await db.commit()

        # Re-fetch with eager loading
        result = await db.execute(_eager_exam_query().where(Exam.id == exam.id))
        return _exam_to_response(result.scalar_one())

    @staticmethod
    async def list_by_user(db: AsyncSession, user_id: uuid.UUID) -> list[ExamOut]:
        result = await db.execute(
            _eager_exam_query().where(Exam.user_id == user_id).order_by(Exam.created_at.desc())
        )
        return [_exam_to_response(e) for e in result.scalars().all()]

    @staticmethod
    async def get(db: AsyncSession, exam_id: uuid.UUID, user_id: uuid.UUID) -> ExamOut:
        result = await db.execute(
            _eager_exam_query().where(Exam.id == exam_id, Exam.user_id == user_id)
        )
        exam = result.scalar_one_or_none()
        if not exam:
            raise ServiceError("Exam not found")
        return _exam_to_response(exam)

    @staticmethod
    async def update(
        db: AsyncSession, exam_id: uuid.UUID, user_id: uuid.UUID, payload: ExamUpdate
    ) -> ExamOut:
        result = await db.execute(
            _eager_exam_query().where(Exam.id == exam_id, Exam.user_id == user_id)
        )
        exam = result.scalar_one_or_none()
        if not exam:
            raise ServiceError("Exam not found")

        form = payload.form
        exam.title = form.title
        exam.module = form.module
        exam.university = form.university
        exam.department = form.department
        exam.exam_date = _parse_date(form.date)
        exam.duration = form.duration
        exam.num_questions = int(form.num_questions) if form.num_questions else 0
        exam.choices_per_question = int(form.choices) if form.choices else 4
        exam.questions_per_page = int(form.questions_per_page) if form.questions_per_page else 20
        exam.instructions = form.instructions
        exam.checkbox_type = payload.checkbox_type
        exam.grid_layout = payload.grid_layout

        # Replace questions
        for q in exam.questions:
            await db.delete(q)
        await db.flush()

        for qi, q_in in enumerate(payload.questions):
            correct = sorted(set(q_in.correct or []))
            question = Question(
                exam_id=exam.id,
                order_index=qi,
                text=q_in.text,
                correct_answers=correct,
                correct_answer=correct[0] if correct else None,
                points=q_in.points,
            )
            db.add(question)
            await db.flush()

            for ci, c_in in enumerate(q_in.choices):
                db.add(Choice(question_id=question.id, order_index=ci, text=c_in.text))

        # Replace students — clear the collection and add new ones
        exam.students.clear()
        await db.flush()

        for s_in in payload.students:
            exam.students.append(_build_student(exam.id, s_in))

        await db.commit()
        await db.refresh(exam, ["students", "questions"])

        # Re-fetch to ensure eager loading of nested choices
        result = await db.execute(_eager_exam_query().where(Exam.id == exam.id))
        return _exam_to_response(result.scalar_one())

    @staticmethod
    async def delete(db: AsyncSession, exam_id: uuid.UUID, user_id: uuid.UUID) -> None:
        result = await db.execute(select(Exam).where(Exam.id == exam_id, Exam.user_id == user_id))
        exam = result.scalar_one_or_none()
        if not exam:
            raise ServiceError("Exam not found")
        await db.delete(exam)
        await db.commit()

    # --- Mobile app endpoints ---

    @staticmethod
    async def list_recent(
        db: AsyncSession, user_id: uuid.UUID, limit: int = 10
    ) -> list[MobileExamOut]:
        result = await db.execute(
            select(Exam)
            .options(selectinload(Exam.submissions))
            .where(Exam.user_id == user_id)
            .order_by(Exam.updated_at.desc())
            .limit(limit)
        )
        return [_exam_to_mobile(e) for e in result.scalars().all()]

    @staticmethod
    async def list_to_correct(
        db: AsyncSession, user_id: uuid.UUID, search: str | None = None
    ) -> list[MobileExamOut]:
        # All of the teacher's exams are correctable — even fully-graded ones,
        # since more student copies can always be scanned and added later.
        query = (
            select(Exam)
            .options(selectinload(Exam.submissions))
            .where(Exam.user_id == user_id)
            .order_by(Exam.created_at.desc())
        )
        if search:
            query = query.where(Exam.title.ilike(f"%{search}%"))
        result = await db.execute(query)
        return [_exam_to_mobile(e) for e in result.scalars().all()]

    @staticmethod
    async def list_history(
        db: AsyncSession, user_id: uuid.UUID, search: str | None = None
    ) -> list[HistoryExamOut]:
        query = (
            select(Exam)
            .options(selectinload(Exam.submissions))
            .where(Exam.user_id == user_id)
            .order_by(Exam.created_at.desc())
        )
        if search:
            query = query.where(Exam.title.ilike(f"%{search}%"))
        result = await db.execute(query)
        return [_exam_to_history(e) for e in result.scalars().all()]

    @staticmethod
    async def get_mobile(
        db: AsyncSession, exam_id: uuid.UUID, user_id: uuid.UUID
    ) -> MobileExamOut:
        result = await db.execute(
            select(Exam)
            .options(selectinload(Exam.submissions))
            .where(Exam.id == exam_id, Exam.user_id == user_id)
        )
        exam = result.scalar_one_or_none()
        if not exam:
            raise ServiceError("Exam not found")
        return _exam_to_mobile(exam)

    @staticmethod
    async def upload_images(
        db: AsyncSession,
        exam_id: uuid.UUID,
        user_id: uuid.UUID,
        files: list[tuple[str, bytes]],
    ) -> MobileExamOut:
        """Store scanned sheets and grade each one.

        Each item in `files` is a (filename, bytes) pair and may be a single
        image or a .zip of images. Every image is stored in MinIO and recorded
        as a StudentSubmission. If the grading model isn't wired in yet, the
        submission is left PENDING and can be graded later via regrade_pending.
        """
        exam = await ExamService._load_for_grading(db, exam_id, user_id)

        images = _extract_images(files)
        failed: list[dict[str, str]] = []
        for filename, content in images:
            # Reject sheets we can't segment up front: don't store or grade them,
            # so the client can cleanly retry just those (no half-ingested rows).
            reason = (
                _segmentation_reason(content, filename)
                if settings.segmentation_precheck
                else None
            )
            if reason is not None:
                failed.append({"filename": filename, "reason": reason})
                continue

            key = storage.build_key(exam.id, filename)
            await storage.put_object(key, content, _content_type(filename))

            submission = StudentSubmission(sheet_image_path=key)
            _apply_grading(submission, content, exam)
            exam.submissions.append(submission)

        _recompute_aggregates(exam)
        await db.commit()

        mobile = await ExamService.get_mobile(db, exam_id, user_id)
        return mobile.model_copy(
            update={"failedSheets": [FailedSheetOut(**f) for f in failed]}
        )

    @staticmethod
    async def regrade_pending(
        db: AsyncSession, exam_id: uuid.UUID, user_id: uuid.UUID
    ) -> MobileExamOut:
        """Re-run grading on every PENDING submission (e.g. after the model lands)."""
        exam = await ExamService._load_for_grading(db, exam_id, user_id)

        for submission in exam.submissions:
            if submission.status != "pending":
                continue
            if not submission.sheet_image_path:
                continue
            content = await storage.get_object(submission.sheet_image_path)
            _apply_grading(submission, content, exam)

        _recompute_aggregates(exam)
        await db.commit()
        return await ExamService.get_mobile(db, exam_id, user_id)

    @staticmethod
    async def export_results_xlsx(
        db: AsyncSession, exam_id: uuid.UUID, user_id: uuid.UUID
    ) -> tuple[bytes, str]:
        """Build an .xlsx list of the exam's graded students. Returns (bytes, title)."""
        from openpyxl import Workbook

        exam = await ExamService._load_for_grading(db, exam_id, user_id)
        # Look up each submission's group from the roster (matched by reg number).
        by_reg = {
            str(s.registration_number): s
            for s in exam.students
            if s.registration_number
        }

        wb = Workbook()
        ws = wb.active
        ws.title = "Grades"
        ws.append(
            ["First Name", "Last Name", "Group", "Registration Number",
             "Score", "Max Score", "Percentage", "Needs Review"]
        )
        for sub in exam.submissions:
            student = by_reg.get(str(sub.student_id))
            group = student.group_name if student else ""
            pct = round(sub.score / sub.max_score * 100, 1) if sub.max_score else 0
            ws.append([
                sub.first_name, sub.last_name, group, sub.student_id,
                sub.score, sub.max_score, pct, "yes" if sub.needs_review else "",
            ])

        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue(), exam.title

    @staticmethod
    async def _load_for_grading(
        db: AsyncSession, exam_id: uuid.UUID, user_id: uuid.UUID
    ) -> Exam:
        result = await db.execute(
            select(Exam)
            .options(
                selectinload(Exam.submissions),
                selectinload(Exam.questions),
                selectinload(Exam.students),
            )
            .where(Exam.id == exam_id, Exam.user_id == user_id)
        )
        exam = result.scalar_one_or_none()
        if not exam:
            raise ServiceError("Exam not found")
        return exam


def _correct_set(q) -> set:
    """Correct choice indices for a question (multiple-correct supported)."""
    if q.correct_answers:
        return {int(a) for a in q.correct_answers}
    return {int(q.correct_answer)} if q.correct_answer is not None else set()


def _question_correct(q) -> list[int]:
    """Correct choice indices as a sorted list (for API output)."""
    return sorted(_correct_set(q))


def _gradeable_max(exam: Exam) -> int:
    """Total points over questions that have a correct answer set."""
    return sum(q.points for q in exam.questions if _correct_set(q))


def _derive_score(result: GradingResult, exam: Exam) -> tuple[int, int]:
    """Score a result from its detected answers vs the answer key.

    Used only when the model didn't return its own score/max_score (the bubble
    pipeline scores itself). Single-answer match: a question is correct when its
    single detected answer equals its (single) correct choice.
    """
    questions = sorted(exam.questions, key=lambda q: q.order_index)
    max_score = _gradeable_max(exam)
    score = sum(
        q.points
        for i, q in enumerate(questions)
        if _correct_set(q)
        and i < len(result.answers)
        and result.answers[i] is not None
        and {result.answers[i]} == _correct_set(q)
    )
    return score, max_score


def _apply_grading(submission: StudentSubmission, image: bytes, exam: Exam) -> None:
    """Run the grading model on one sheet and write results onto the submission.

    Falls back to a PENDING submission if the model isn't wired in yet.
    """
    try:
        result = grade_sheet(image, exam)
    except GradingNotAvailable:
        submission.status = "pending"
        submission.score = 0
        submission.max_score = _gradeable_max(exam)
        submission.confidence = 0.0
        submission.needs_review = False
        submission.flagged_questions = []
        return

    score, max_score = result.score, result.max_score
    if max_score == 0:
        score, max_score = _derive_score(result, exam)

    submission.status = "graded"
    submission.score = score
    submission.max_score = max_score
    submission.confidence = result.confidence
    submission.needs_review = result.needs_review
    submission.flagged_questions = result.flagged_questions
    submission.first_name = result.first_name
    submission.last_name = result.last_name
    submission.student_id = result.student_id
    if result.first_name or result.last_name:
        submission.recognized_name = f"{result.first_name} {result.last_name}".strip()


def _recompute_aggregates(exam: Exam) -> None:
    """Refresh denormalized exam stats from its current submissions."""
    subs = exam.submissions
    graded = [s for s in subs if s.status == "graded"]
    pending = [s for s in subs if s.status == "pending"]

    exam.total_students = len(subs)
    exam.corrected_count = len(graded)
    exam.total_pages = len(subs)
    exam.pending_pages = len(pending)
    exam.avg_confidence = (
        round(sum(s.confidence for s in graded) / len(graded), 1) if graded else 0.0
    )

    if not subs:
        return
    if pending:
        exam.status = ExamStatus.in_progress
    else:
        exam.status = ExamStatus.completed


def _exam_to_mobile(exam: Exam) -> MobileExamOut:
    students_out = [
        StudentResultOut(
            firstName=s.first_name,
            lastName=s.last_name,
            studentId=s.student_id,
            score=s.score,
            maxScore=s.max_score,
            confidence=s.confidence,
            needsReview=bool(s.needs_review),
            flaggedQuestions=s.flagged_questions or [],
        )
        for s in exam.submissions
    ]
    return MobileExamOut(
        id=exam.id,
        title=exam.title,
        totalStudents=exam.total_students,
        correctedCount=exam.corrected_count,
        avgConfidence=exam.avg_confidence,
        students=students_out,
    )


def _exam_to_history(exam: Exam) -> HistoryExamOut:
    submissions = exam.submissions
    avg_score = 0.0
    if submissions:
        total = sum(s.score for s in submissions)
        max_total = sum(s.max_score for s in submissions) or 1
        avg_score = round((total / max_total) * 100, 1)

    return HistoryExamOut(
        id=exam.id,
        title=exam.title,
        pages=exam.total_pages,
        date=exam.created_at,
        avgScore=avg_score,
        avgConfidence=exam.avg_confidence,
        pendingPages=exam.pending_pages,
        totalPages=exam.total_pages,
    )
