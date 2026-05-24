"""Seed the database with mock exams + submissions for manual mobile testing.

Attaches the data to the most recently registered user, so you can log into the
mobile app with that account and immediately see populated Recent / To-correct /
History screens.

Run:
    PYTHONPATH=. ./.venv/bin/python scripts/seed_mock_data.py

Re-running is safe: it deletes its own previously-seeded exams (matched by title)
for that user before inserting fresh ones.
"""

import asyncio
import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from app.db.session import AsyncSessionLocal
from app.models.exam import (
    Choice,
    Exam,
    ExamStatus,
    ExamStudent,
    Question,
    StudentSubmission,
)
from app.models.user import User

random.seed(42)

FIRST_NAMES = [
    "Ahmed", "Ines", "Yacine", "Lina", "Mohamed", "Sara", "Bilal", "Nour",
    "Imene", "Walid", "Amine", "Rania", "Karim", "Salma", "Anis", "Meriem",
    "Riad", "Hiba", "Sofiane", "Asma", "Adel", "Khadija", "Reda", "Manel",
    "Oussama", "Wassila", "Hamza", "Chaima", "Islam", "Yasmine",
]
LAST_NAMES = [
    "Benali", "Cherif", "Haddad", "Bouzid", "Saadi", "Khaldi", "Mansouri",
    "Belkacem", "Brahimi", "Ferhat", "Larbi", "Naceri", "Zerrouki", "Toumi",
    "Boukhalfa", "Hamdi", "Slimani", "Aziz", "Meziane", "Belhadj",
]

# Titles this script owns — used to clean up before re-seeding.
SEED_TITLES = [
    "Mathematics Final 2026",
    "Physics Midterm",
    "Algorithms Quiz",
    "Biology Exam 2025",
    "English Placement (new)",
]


def _make_questions(exam: Exam, n: int, choices: int) -> None:
    for qi in range(n):
        q = Question(
            order_index=qi,
            text=f"Question {qi + 1}",
            correct_answer=random.randrange(choices),
        )
        for ci in range(choices):
            q.choices.append(Choice(order_index=ci, text=f"Choice {ci + 1}"))
        exam.questions.append(q)


def _make_submissions(
    exam: Exam, graded: int, pending: int, max_score: int
) -> None:
    for _ in range(graded):
        exam.submissions.append(
            StudentSubmission(
                first_name=random.choice(FIRST_NAMES),
                last_name=random.choice(LAST_NAMES),
                student_id=str(random.randint(20210000, 20259999)),
                score=random.randint(max(1, max_score // 2), max_score),
                max_score=max_score,
                confidence=round(random.uniform(82, 99), 1),
                status="graded",
            )
        )
    for _ in range(pending):
        exam.submissions.append(
            StudentSubmission(
                first_name="",
                last_name="",
                student_id="",
                score=0,
                max_score=max_score,
                confidence=0.0,
                status="pending",
                sheet_image_path=f"exams/seed/pending_{random.randint(1000, 9999)}.jpg",
            )
        )


def _apply_aggregates(exam: Exam, status: ExamStatus) -> None:
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
    exam.status = status


def _build_exam(
    user_id,
    title: str,
    *,
    module: str,
    num_questions: int,
    choices: int,
    graded: int,
    pending: int,
    status: ExamStatus,
    days_ago: int,
) -> Exam:
    created = datetime.now(UTC) - timedelta(days=days_ago)
    exam = Exam(
        user_id=user_id,
        title=title,
        module=module,
        university="ESI-SBA",
        department="2CS",
        exam_date=created.date(),
        duration="1h30",
        num_questions=num_questions,
        choices_per_question=choices,
        questions_per_page=20,
        instructions="Use a black pen only.",
        checkbox_type="Fill",
        grid_layout="Linear",
        created_at=created,
        updated_at=created + timedelta(hours=2),
    )
    _make_questions(exam, num_questions, choices)
    # roster
    for _ in range(graded + pending):
        exam.students.append(
            ExamStudent(name=f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}")
        )
    _make_submissions(exam, graded, pending, max_score=num_questions)
    _apply_aggregates(exam, status)
    return exam


async def main() -> None:
    async with AsyncSessionLocal() as db:
        user = (
            await db.execute(select(User).order_by(User.created_at.desc()).limit(1))
        ).scalar_one_or_none()
        if user is None:
            print("No users found — register an account first.")
            return

        # Clean previous seed for idempotency.
        await db.execute(
            delete(Exam).where(Exam.user_id == user.id, Exam.title.in_(SEED_TITLES))
        )

        exams = [
            _build_exam(
                user.id, "Mathematics Final 2026", module="Mathematics",
                num_questions=20, choices=4, graded=24, pending=0,
                status=ExamStatus.completed, days_ago=28,
            ),
            _build_exam(
                user.id, "Physics Midterm", module="Physics",
                num_questions=15, choices=4, graded=12, pending=6,
                status=ExamStatus.in_progress, days_ago=9,
            ),
            _build_exam(
                user.id, "Algorithms Quiz", module="Algorithms",
                num_questions=10, choices=5, graded=0, pending=5,
                status=ExamStatus.ready, days_ago=3,
            ),
            _build_exam(
                user.id, "Biology Exam 2025", module="Biology",
                num_questions=25, choices=4, graded=30, pending=0,
                status=ExamStatus.completed, days_ago=40,
            ),
            _build_exam(
                user.id, "English Placement (new)", module="English",
                num_questions=12, choices=4, graded=0, pending=0,
                status=ExamStatus.draft, days_ago=0,
            ),
        ]
        db.add_all(exams)
        await db.commit()

        print(f"Seeded {len(exams)} exams for: {user.email} ({user.first_name} {user.last_name})")
        for e in exams:
            print(
                f"  - {e.title:28s} status={e.status.value:12s} "
                f"students={e.total_students:2d} graded={e.corrected_count:2d} "
                f"pending={e.pending_pages:2d} avgConf={e.avg_confidence}"
            )


if __name__ == "__main__":
    asyncio.run(main())
