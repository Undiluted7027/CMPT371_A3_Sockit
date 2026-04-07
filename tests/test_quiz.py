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
                "options": ["3", "4"],
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
                "options": ["A", "B"],
                "answer": 5,
            }
        ],
    }

    file = tmp_path / "quiz.json"
    file.write_text(json.dumps(quiz_data))

    with pytest.raises(ValueError):
        load_quiz(str(file))
