"""Unit tests for scoring.py."""

from src.scoring import (
    PlayerGameStats,
    build_final_rankings,
    build_leaderboard,
    score_answer,
)


def test_instant_correct_answer_gets_full_points() -> None:
    """A correct answer at full time remaining gets the full base score."""
    stats = PlayerGameStats(name="Alice")
    result = score_answer(
        stats,
        is_correct=True,
        time_remaining=10.0,
        time_limit=10.0,
        answer_elapsed=0.3,
    )

    assert result.score == 1000
    assert result.total == 1000
    assert result.streak == 1
    assert stats.fastest_answer == 0.3


def test_buzzer_answer_gets_floor_before_streak_bonus() -> None:
    """Minimum time still gets the 500 floor before the streak multiplier."""
    stats = PlayerGameStats(name="Alice", current_streak=1, longest_streak=1)
    result = score_answer(
        stats,
        is_correct=True,
        time_remaining=0.0,
        time_limit=10.0,
        answer_elapsed=10.0,
    )

    assert result.score == 550
    assert result.total == 550
    assert result.streak == 2


def test_wrong_answer_scores_zero_and_resets_streak() -> None:
    """Wrong answers score zero and reset the current streak."""
    stats = PlayerGameStats(
        name="Alice", total_score=800, current_streak=3, longest_streak=3
    )
    result = score_answer(
        stats,
        is_correct=False,
        time_remaining=5.0,
        time_limit=10.0,
        answer_elapsed=2.0,
    )

    assert result.score == 0
    assert result.total == 800
    assert result.streak == 0
    assert stats.current_streak == 0
    assert stats.longest_streak == 3


def test_timeout_scores_zero_and_resets_streak() -> None:
    """Timeouts behave like unanswered questions."""
    stats = PlayerGameStats(name="Alice", current_streak=2, longest_streak=2)
    result = score_answer(
        stats,
        is_correct=False,
        time_remaining=0.0,
        time_limit=10.0,
        answer_elapsed=None,
    )

    assert result.score == 0
    assert result.streak == 0
    assert stats.current_streak == 0


def test_fastest_correct_answer_uses_lowest_elapsed_seconds() -> None:
    """Fastest answer stores the lowest elapsed correct-answer time."""
    stats = PlayerGameStats(name="Alice")
    score_answer(
        stats,
        is_correct=True,
        time_remaining=8.0,
        time_limit=10.0,
        answer_elapsed=2.0,
    )
    score_answer(
        stats,
        is_correct=True,
        time_remaining=9.0,
        time_limit=10.0,
        answer_elapsed=1.0,
    )

    assert stats.fastest_answer == 1.0


def test_leaderboard_and_final_rankings_break_ties_by_name() -> None:
    """Ties are ordered by display name ascending for deterministic output."""
    alice = PlayerGameStats(name="Alice", total_score=1000)
    bob = PlayerGameStats(name="Bob", total_score=1000)
    stats_by_player = {"Bob": bob, "Alice": alice}

    leaderboard = build_leaderboard(stats_by_player)
    rankings = build_final_rankings(stats_by_player)

    assert [row["name"] for row in leaderboard] == ["Alice", "Bob"]
    assert [row["name"] for row in rankings] == ["Alice", "Bob"]
