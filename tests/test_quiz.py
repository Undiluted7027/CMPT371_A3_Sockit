"""Test quiz related functionality."""

import json
from pathlib import Path

import pytest

from src.quiz import load_quiz


# Test valid quiz loads correctly
def test_valid_quiz(tmp_path: Path) -> None:
    """Test valid quiz loads correctly."""
    quiz_data = {
        "title": "Test Quiz",
        "questions": [
            {
                "text": "2+2?",
                "type": "multiple_choice",
                "options": ["3", "4", "5", "6"],
                "answer": 1,
            }
        ],
    }

    file = tmp_path / "quiz.json"
    file.write_text(json.dumps(quiz_data))

    quiz = load_quiz(str(file))  # convert to string

    assert quiz.title == "Test Quiz"
    assert len(quiz.questions) == 1
    assert quiz.questions[0].answer == 1


# Missing title
def test_missing_title(tmp_path: Path) -> None:
    """Test missing title."""
    quiz_data: dict[str, object] = {"questions": []}

    file = tmp_path / "quiz.json"
    file.write_text(json.dumps(quiz_data))

    with pytest.raises(ValueError):
        load_quiz(str(file))


# Invalid answer index
def test_invalid_answer(tmp_path: Path) -> None:
    """Test invalid answer."""
    quiz_data = {
        "title": "Bad Quiz",
        "questions": [
            {
                "text": "Test",
                "type": "multiple_choice",
                "options": ["A", "B", "C", "D"],
                "answer": 5,
            }
        ],
    }

    file = tmp_path / "quiz.json"
    file.write_text(json.dumps(quiz_data))

    with pytest.raises(ValueError):
        load_quiz(str(file))


def test_missing_type_field(tmp_path: Path) -> None:
    """Question missing 'type' field raises ValueError."""
    quiz_data = {
        "title": "Bad Quiz",
        "questions": [{"text": "Test", "options": ["A", "B", "C", "D"], "answer": 0}],
    }
    file = tmp_path / "quiz.json"
    file.write_text(json.dumps(quiz_data))
    with pytest.raises(ValueError):
        load_quiz(str(file))


def test_missing_text_field(tmp_path: Path) -> None:
    """Question missing 'text' field raises ValueError."""
    quiz_data = {
        "title": "Bad Quiz",
        "questions": [
            {"type": "multiple_choice", "options": ["A", "B", "C", "D"], "answer": 0}
        ],
    }
    file = tmp_path / "quiz.json"
    file.write_text(json.dumps(quiz_data))
    with pytest.raises(ValueError):
        load_quiz(str(file))


def test_true_false_wrong_options(tmp_path: Path) -> None:
    """true_false question with non-canonical options raises ValueError."""
    quiz_data = {
        "title": "Bad Quiz",
        "questions": [
            {
                "text": "Yes or no?",
                "type": "true_false",
                "options": ["Yes", "No"],
                "answer": 0,
            }
        ],
    }
    file = tmp_path / "quiz.json"
    file.write_text(json.dumps(quiz_data))
    with pytest.raises(ValueError):
        load_quiz(str(file))


def test_mc_wrong_option_count(tmp_path: Path) -> None:
    """multiple_choice question with fewer than 4 options raises ValueError."""
    quiz_data = {
        "title": "Bad Quiz",
        "questions": [
            {
                "text": "Pick one",
                "type": "multiple_choice",
                "options": ["A", "B", "C"],
                "answer": 0,
            }
        ],
    }
    file = tmp_path / "quiz.json"
    file.write_text(json.dumps(quiz_data))
    with pytest.raises(ValueError):
        load_quiz(str(file))


def test_per_question_time_limit(tmp_path: Path) -> None:
    """Per-question time_limit overrides the settings default_time_limit."""
    quiz_data = {
        "title": "Timed Quiz",
        "settings": {"default_time_limit": 30},
        "questions": [
            {
                "text": "Fast question",
                "type": "multiple_choice",
                "options": ["A", "B", "C", "D"],
                "answer": 0,
                "time_limit": 5,
            }
        ],
    }
    file = tmp_path / "quiz.json"
    file.write_text(json.dumps(quiz_data))
    quiz = load_quiz(str(file))
    assert quiz.questions[0].time_limit == 5


def test_invalid_settings_type(tmp_path: Path) -> None:
    """Non-bool show_correct_answer in settings raises ValueError."""
    quiz_data = {
        "title": "Bad Settings",
        "settings": {"show_correct_answer": "yes"},
        "questions": [
            {
                "text": "Test",
                "type": "multiple_choice",
                "options": ["A", "B", "C", "D"],
                "answer": 0,
            }
        ],
    }
    file = tmp_path / "quiz.json"
    file.write_text(json.dumps(quiz_data))
    with pytest.raises(ValueError):
        load_quiz(str(file))
