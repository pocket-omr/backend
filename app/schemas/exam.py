import uuid
from datetime import datetime

from pydantic import Field

from app.schemas.base import APIModel


class ChoiceIn(APIModel):
    text: str = ""


class QuestionIn(APIModel):
    text: str = ""
    choices: list[ChoiceIn] = Field(default_factory=list)
    # Correct choice indices (0-based). Multiple => multiple-correct question.
    correct: list[int] = Field(default_factory=list)
    points: int = 1


class StudentIn(APIModel):
    firstName: str = ""
    lastName: str = ""
    group: str = ""
    registrationNumber: str = ""


class ExamCreate(APIModel):
    form: "ExamFormIn"
    questions: list[QuestionIn] = Field(default_factory=list)
    checkbox_type: str = Field(default="Fill", alias="checkboxType")
    grid_layout: str = Field(default="Linear", alias="gridLayout")
    students: list[StudentIn] = Field(default_factory=list)

    class Config:
        populate_by_name = True


class ExamFormIn(APIModel):
    title: str = ""
    module: str = ""
    university: str = ""
    department: str = ""
    date: str = ""
    duration: str = ""
    num_questions: str = Field(default="", alias="numQuestions")
    choices: str = "4"
    questions_per_page: str = Field(default="20", alias="questionsPerPage")
    instructions: str = ""

    class Config:
        populate_by_name = True


class ExamUpdate(ExamCreate):
    pass


# --- Responses ---


class ChoiceOut(APIModel):
    text: str


class QuestionOut(APIModel):
    id: uuid.UUID
    text: str
    choices: list[ChoiceOut]
    correct: list[int] = Field(default_factory=list)
    points: int = 1


class ExamFormOut(APIModel):
    title: str
    module: str
    university: str
    department: str
    date: str
    duration: str
    numQuestions: str
    choices: str
    questionsPerPage: str
    instructions: str


class StudentOut(APIModel):
    firstName: str
    lastName: str
    group: str
    registrationNumber: str


class ExamOut(APIModel):
    id: uuid.UUID
    form: ExamFormOut
    questions: list[QuestionOut]
    checkboxType: str
    gridLayout: str
    students: list[StudentOut]
    createdAt: datetime
    updatedAt: datetime


# --- Mobile app responses ---


class StudentResultOut(APIModel):
    firstName: str
    lastName: str
    studentId: str
    score: int
    maxScore: int
    confidence: float
    # True when the grader was unsure of one or more answers (human review).
    needsReview: bool = False
    # 1-based question numbers flagged as uncertain.
    flaggedQuestions: list[int] = Field(default_factory=list)


class FailedSheetOut(APIModel):
    filename: str
    reason: str


class MobileExamOut(APIModel):
    id: uuid.UUID
    title: str
    totalStudents: int
    correctedCount: int
    avgConfidence: float
    students: list[StudentResultOut]
    # Sheets that couldn't be segmented on this upload (not stored/graded).
    failedSheets: list[FailedSheetOut] = Field(default_factory=list)


class HistoryExamOut(APIModel):
    id: uuid.UUID
    title: str
    pages: int
    date: datetime
    avgScore: float
    avgConfidence: float
    pendingPages: int
    totalPages: int
