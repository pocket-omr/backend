from pydantic import Field

from app.schemas.base import APIModel
from app.schemas.exam import ExamFormIn, QuestionIn


class PDFRequest(APIModel):
    type: str  # question_sheet | grid_sheet | correction_sheet
    form: ExamFormIn
    questions: list[QuestionIn] = Field(default_factory=list)
    checkbox_type: str = Field(default="Fill", alias="checkboxType")
    grid_layout: str = Field(default="Linear", alias="gridLayout")

    class Config:
        populate_by_name = True
