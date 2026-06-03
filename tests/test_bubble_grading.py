"""Tests for the answer-bubble grading logic (model-free, fast).

The YOLO detector and CNN are exercised manually/in production; here we cover the
deterministic pieces: grid assignment, scoring detected answers against the
teacher's key, and the low-confidence review flag. Heavy models are mocked so
these run without weights or network.
"""

import types

import numpy as np
import pytest

import app.services.grading as grading
from app.recognition.bubble_grading import BubbleGrader, SheetAnswers, assign_grid


def _grid_boxes(nq, nc, cell=70, origin=40, r=18):
    """Synthetic detections: a clean nq x nc grid of bubble boxes."""
    boxes = []
    for qi in range(nq):
        y = origin + qi * cell
        for ci in range(nc):
            x = origin + ci * cell
            x1, y1, x2, y2 = x, y, x + 40, y + 40
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            boxes.append((cx, cy, r, x1, y1, x2, y2, 0.9))
    return boxes


def _grader_without_models(review_threshold=0.80):
    """A BubbleGrader instance whose heavy models are bypassed."""
    g = BubbleGrader.__new__(BubbleGrader)
    g.conf = 0.30
    g.iou = 0.30
    g.review_threshold = review_threshold
    return g


def test_assign_grid_single_block():
    nq, nc = 4, 4
    grid = assign_grid(_grid_boxes(nq, nc), nq, nc)
    assert len(grid) == nq * nc
    assert set(grid.keys()) == {(q, c) for q in range(1, nq + 1) for c in range(1, nc + 1)}


def test_grader_flags_uncertain_and_double_marks():
    nq, nc = 3, 3
    boxes = _grid_boxes(nq, nc)
    g = _grader_without_models(review_threshold=0.80)
    g._detect = lambda img, conf: boxes

    # Q1: confident A filled. Q2: borderline B (0.60) -> flagged. Q3: A and C
    # both filled -> double mark -> flagged, answer None.
    probs = np.array([
        0.99, 0.01, 0.02,
        0.03, 0.60, 0.02,
        0.97, 0.02, 0.96,
    ])
    g._classify = lambda crops: probs

    img = np.zeros((nq * 70 + 60, nc * 70 + 60, 3), np.uint8)
    res = g.grade(img, nq, nc)

    assert res.answers == [0, 1, None]
    assert res.needs_review is True
    assert res.flagged_questions == [2, 3]
    assert res.question_confidence[0] >= 95
    assert res.question_confidence[1] == 60.0


def test_grader_no_flags_when_all_confident():
    nq, nc = 2, 3
    boxes = _grid_boxes(nq, nc)
    g = _grader_without_models()
    g._detect = lambda img, conf: boxes
    g._classify = lambda crops: np.array([0.99, 0.01, 0.02, 0.02, 0.98, 0.01])
    res = g.grade(np.zeros((200, 280, 3), np.uint8), nq, nc)
    assert res.answers == [0, 1]
    assert res.needs_review is False
    assert res.flagged_questions == []


def _exam(correct, choices_per_question=4, points=None):
    points = points or [1] * len(correct)
    questions = [
        types.SimpleNamespace(order_index=i, correct_answer=c, points=p)
        for i, (c, p) in enumerate(zip(correct, points))
    ]
    return types.SimpleNamespace(questions=questions, choices_per_question=choices_per_question)


def _detected(answers, flagged=None, confidence=88.0, filled=None):
    n = len(answers)
    if filled is None:
        filled = [[a] if a is not None else [] for a in answers]
    return SheetAnswers(
        answers=answers,
        confidence=confidence,
        question_confidence=[100.0] * n,
        flagged_questions=flagged or [],
        needs_review=bool(flagged),
        detected_bubbles=n * 4,
        expected_bubbles=n * 4,
        filled=filled,
    )


def _mock_pipeline(monkeypatch, result):
    """Patch grade_sheet's segmentation + grader to return `result`."""
    seg = {"detect": object(), "classify": object(), "personal_info": None}
    monkeypatch.setattr(grading, "_segment", lambda image_bytes: seg)
    grader = types.SimpleNamespace(grade=lambda img, nq, nc, classify_bgr=None: result)
    monkeypatch.setattr(grading, "_get_grader", lambda: grader)


def test_grade_sheet_scores_against_key(monkeypatch):
    # Key: Q1->B(1), Q2->A(0), Q3->D(3). Student got Q1, Q2 right, Q3 wrong.
    exam = _exam([1, 0, 3])
    _mock_pipeline(monkeypatch, _detected([1, 0, 2]))
    result = grading.grade_sheet(b"sheet", exam)
    assert result.answers == [1, 0, 2]
    assert result.score == 2
    assert result.max_score == 3
    assert result.needs_review is False


def test_grade_sheet_weights_by_points(monkeypatch):
    # Q1 worth 2, Q2 worth 3, Q3 worth 1. Student gets Q1 and Q3 right.
    exam = _exam([0, 1, 2], points=[2, 3, 1])
    _mock_pipeline(monkeypatch, _detected([0, 0, 2]))
    result = grading.grade_sheet(b"sheet", exam)
    assert result.max_score == 6   # 2 + 3 + 1
    assert result.score == 3       # Q1 (2) + Q3 (1)


def test_grade_sheet_propagates_review_flag(monkeypatch):
    exam = _exam([1, 0, 3])
    _mock_pipeline(monkeypatch, _detected([1, 0, 2], flagged=[3]))
    result = grading.grade_sheet(b"sheet", exam)
    assert result.needs_review is True
    assert result.flagged_questions == [3]


def test_grade_sheet_ignores_questions_without_key(monkeypatch):
    exam = _exam([0, None, 1])
    _mock_pipeline(monkeypatch, _detected([0, 0, 1]))
    result = grading.grade_sheet(b"sheet", exam)
    assert result.max_score == 2  # only Q1 and Q3 count
    assert result.score == 2


def test_grade_sheet_identifies_student_from_exam_roster(monkeypatch):
    import app.services.name_recognition as nr

    seg = {"detect": object(), "classify": object(), "personal_info": object()}
    monkeypatch.setattr(grading, "_segment", lambda image_bytes: seg)
    monkeypatch.setattr(
        grading, "_get_grader",
        lambda: types.SimpleNamespace(grade=lambda *a, **k: _detected([0])),
    )
    monkeypatch.setattr(grading, "_get_recognition", lambda: object())
    # Recognised name (slightly noisy) should still match the right student.
    monkeypatch.setattr(
        nr, "_recognize_personal_info",
        lambda assets, img: {
            "first_name": "TAHAN", "last_name": "LOKMAN",
            "group": "09", "registration_number": "202303030202",
        },
    )
    exam = _exam([0])
    exam.students = [
        types.SimpleNamespace(
            first_name="TAHAN", last_name="LOKMANE",
            group_name="09", registration_number="202303030202",
        ),
        types.SimpleNamespace(
            first_name="OTHER", last_name="PERSON",
            group_name="01", registration_number="111111111111",
        ),
    ]
    result = grading.grade_sheet(b"x", exam)
    assert result.first_name == "TAHAN"
    assert result.last_name == "LOKMANE"
    assert result.student_id == "202303030202"


def test_grade_sheet_multiple_correct_answers(monkeypatch):
    # Q1 has two correct answers {0,2}; the student must fill exactly that set.
    q1 = types.SimpleNamespace(order_index=0, correct_answer=0, correct_answers=[0, 2], points=1)
    q2 = types.SimpleNamespace(order_index=1, correct_answer=1, correct_answers=[1], points=1)
    exam = types.SimpleNamespace(questions=[q1, q2], choices_per_question=4, students=[])
    # Student filled {0,2} for Q1 (exact match) and {1} for Q2 (correct) -> 2/2.
    result = SheetAnswers(
        answers=[None, 1], confidence=90.0, question_confidence=[90.0, 90.0],
        flagged_questions=[], needs_review=False, detected_bubbles=8, expected_bubbles=8,
        filled=[[0, 2], [1]],
    )
    _mock_pipeline(monkeypatch, result)
    r = grading.grade_sheet(b"x", exam)
    assert r.score == 2 and r.max_score == 2


def test_grade_sheet_partial_multi_answer_is_wrong(monkeypatch):
    # Q1 correct {0,2} but student filled only {0} -> not an exact match -> wrong.
    q1 = types.SimpleNamespace(order_index=0, correct_answer=0, correct_answers=[0, 2], points=1)
    exam = types.SimpleNamespace(questions=[q1], choices_per_question=4, students=[])
    result = SheetAnswers(
        answers=[0], confidence=90.0, question_confidence=[90.0],
        flagged_questions=[], needs_review=False, detected_bubbles=4, expected_bubbles=4,
        filled=[[0]],
    )
    _mock_pipeline(monkeypatch, result)
    r = grading.grade_sheet(b"x", exam)
    assert r.score == 0 and r.max_score == 1


def test_grade_sheet_unsegmentable_falls_back(monkeypatch):
    monkeypatch.setattr(grading, "_segment", lambda image_bytes: None)
    with pytest.raises(grading.GradingNotAvailable):
        grading.grade_sheet(b"garbage", _exam([0]))
