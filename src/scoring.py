"""Scoring engine and player-stat tracking for Sockit Trivia."""

import math
from dataclasses import asdict, dataclass

BASE_POINTS = 1000.0
MIN_POINTS = 500.0


@dataclass
class PlayerGameStats:
    """Mutable cumulative scoring state for one player."""

    name: str
    total_score: int = 0
    current_streak: int = 0
    longest_streak: int = 0
    correct_count: int = 0
    fastest_answer: float = 0.0  # 0.0 is the sentinel for "no correct answer yet"


@dataclass
class QuestionScore:
    """Per-question scoring result for one player."""

    score: int
    total: int
    streak: int
    was_correct: bool


@dataclass
class FinalRanking:
    """Final ranking row for the GAME_OVER payload."""

    rank: int
    name: str
    score: int
    correct: int
    streak: int
    fastest_answer: float


def score_answer(
    stats: PlayerGameStats,
    *,
    is_correct: bool,
    time_remaining: float,
    time_limit: float,
    answer_elapsed: float | None,
    rtt: float | None = None,
) -> QuestionScore:
    """Apply one question result to cumulative player state.

    Scoring formula (correct answers only)::

        score = floor(max(MIN_POINTS, BASE_POINTS * time_remaining / time_limit)
                      * streak_multiplier)

    Answering instantly (time_remaining == time_limit) yields BASE_POINTS (1000).
    Answering at the buzzer (time_remaining == 0) yields the MIN_POINTS floor (500).
    Wrong answers and timeouts (answer_elapsed is None) score 0 and reset the streak.

    RTT is accepted for future latency compensation support. It is currently
    unused when ``None`` or any other value is passed.
    """
    del rtt

    if not is_correct or answer_elapsed is None:
        stats.current_streak = 0
        return QuestionScore(
            score=0,
            total=stats.total_score,
            streak=stats.current_streak,
            was_correct=False,
        )

    stats.current_streak += 1
    stats.correct_count += 1
    stats.longest_streak = max(stats.longest_streak, stats.current_streak)

    bounded_remaining = max(0.0, min(time_remaining, time_limit))
    base_score = max(MIN_POINTS, BASE_POINTS * (bounded_remaining / time_limit))
    final_score = math.floor(base_score * _streak_multiplier(stats.current_streak))
    stats.total_score += final_score

    if stats.fastest_answer == 0.0 or answer_elapsed < stats.fastest_answer:
        stats.fastest_answer = answer_elapsed

    return QuestionScore(
        score=final_score,
        total=stats.total_score,
        streak=stats.current_streak,
        was_correct=True,
    )


def build_leaderboard(
    stats_by_player: dict[str, PlayerGameStats],
) -> list[dict[str, int | str]]:
    """Return cumulative leaderboard rows sorted deterministically."""
    ordered = _sorted_players(stats_by_player)
    return [{"name": stats.name, "score": stats.total_score} for stats in ordered]


def build_final_rankings(
    stats_by_player: dict[str, PlayerGameStats],
) -> list[dict[str, int | float | str]]:
    """Return final ranking rows for GAME_OVER.

    Constructs typed ``FinalRanking`` instances first so mypy enforces all
    required fields, then converts to plain dicts for JSON serialisation.
    """
    ordered = _sorted_players(stats_by_player)
    rankings = [
        FinalRanking(
            rank=rank,
            name=stats.name,
            score=stats.total_score,
            correct=stats.correct_count,
            streak=stats.longest_streak,
            fastest_answer=stats.fastest_answer,
        )
        for rank, stats in enumerate(ordered, start=1)
    ]
    return [asdict(r) for r in rankings]


def _sorted_players(
    stats_by_player: dict[str, PlayerGameStats],
) -> list[PlayerGameStats]:
    """Sort players by total descending, then name ascending."""
    return sorted(
        stats_by_player.values(),
        key=lambda stats: (-stats.total_score, stats.name),
    )


def _streak_multiplier(current_streak: int) -> float:
    """Return the streak multiplier for the current streak length.

    streak 1  → 1.0x (no bonus)
    streak 2  → 1.1x
    streak 3+ → 1.2x (capped)
    """
    if current_streak >= 3:
        return 1.2
    if current_streak == 2:
        return 1.1
    return 1.0
