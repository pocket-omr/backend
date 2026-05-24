import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.exam import Choice, Exam, ExamStatus, ExamStudent, Question
from app.schemas.exam import (
    ChoiceOut,
    ExamCreate,
    ExamFormOut,
    ExamOut,
    ExamUpdate,
    HistoryExamOut,
    MobileExamOut,
    QuestionOut,
    StudentResultOut,
)
from app.services.base import ServiceError


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
            QuestionOut(id=q.id, text=q.text, choices=choices_out, correct=q.correct_answer)
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

    students = [s.name for s in exam.students]

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
            question = Question(
                exam_id=exam.id,
                order_index=qi,
                text=q_in.text,
                correct_answer=q_in.correct,
            )
            db.add(question)
            await db.flush()

            for ci, c_in in enumerate(q_in.choices):
                db.add(Choice(question_id=question.id, order_index=ci, text=c_in.text))

        for name in payload.students:
            db.add(ExamStudent(exam_id=exam.id, name=name))

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
            question = Question(
                exam_id=exam.id,
                order_index=qi,
                text=q_in.text,
                correct_answer=q_in.correct,
            )
            db.add(question)
            await db.flush()

            for ci, c_in in enumerate(q_in.choices):
                db.add(Choice(question_id=question.id, order_index=ci, text=c_in.text))

        # Replace students — clear the collection and add new ones
        exam.students.clear()
        await db.flush()

        for name in payload.students:
            exam.students.append(ExamStudent(exam_id=exam.id, name=name))

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
        query = (
            select(Exam)
            .options(selectinload(Exam.submissions))
            .where(
                Exam.user_id == user_id,
                Exam.status.in_([ExamStatus.draft, ExamStatus.ready, ExamStatus.in_progress]),
            )
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
        db: AsyncSession, exam_id: uuid.UUID, user_id: uuid.UUID, file_paths: list[str]
    ) -> MobileExamOut:
        """Stub for image upload. Stores file paths; actual ML processing is not yet implemented."""
        result = await db.execute(
            select(Exam)
            .options(selectinload(Exam.submissions))
            .where(Exam.id == exam_id, Exam.user_id == user_id)
        )
        exam = result.scalar_one_or_none()
        if not exam:
            raise ServiceError("Exam not found")

        exam.total_pages += len(file_paths)
        exam.pending_pages += len(file_paths)
        if exam.status == ExamStatus.draft:
            exam.status = ExamStatus.ready

        await db.commit()
        await db.refresh(exam)
        return _exam_to_mobile(exam)


def _exam_to_mobile(exam: Exam) -> MobileExamOut:
    students_out = [
        StudentResultOut(
            firstName=s.first_name,
            lastName=s.last_name,
            studentId=s.student_id,
            score=s.score,
            maxScore=s.max_score,
            confidence=s.confidence,
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
