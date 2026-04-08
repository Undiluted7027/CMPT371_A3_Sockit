"""Handles game loop.

- Lobby auto-start countdown
- Game start
- Question sequencing with timers
- Collecting answers and scoring
- Sending QUESTION, QUESTION_RESULT, and GAME_OVER messages
"""

import logging
import queue as _queue
import time
from typing import Any

from .protocol import MsgType
from .quiz import Quiz
from .scoring import (
    PlayerGameStats,
    build_final_rankings,
    build_leaderboard,
    score_answer,
)
from .server import GameServer

logger = logging.getLogger(__name__)


class HostObserver:
    """Local observer interface for the host GUI.

    All callbacks are optional. The GameLoop invokes them only when a
    ``host_observer`` is supplied, keeping existing tests and call sites
    unchanged.
    """

    def on_game_started(self, total_questions: int) -> None:
        """Call once immediately after GAME_START is broadcast."""

    def on_question(
        self,
        index: int,
        total: int,
        text: str,
        question_type: str,
        options: list[str],
        time_limit: float,
    ) -> None:
        """Call when a new question becomes active."""

    def on_timer_tick(
        self, question_index: int, remaining: float, answered: int, total: int
    ) -> None:
        """Call during the live question phase."""

    def on_question_result(
        self,
        correct_answer: int,
        leaderboard: list[dict[str, int | str]],
        pause_duration: int,
        show_correct_answer: bool,
        show_leaderboard: bool,
    ) -> None:
        """Call once after scores are calculated for a question."""

    def on_game_over(
        self, final_rankings: list[dict[str, int | float | str]], total_questions: int
    ) -> None:
        """Call when the game is complete."""


class GameLoop:
    """Manages the game loop using an existing GameServer instance."""

    def __init__(
        self,
        server: GameServer,
        quiz: Quiz,
        min_players: int = 2,
        lobby_countdown: int = 10,
        question_time: int = 15,
        host_observer: HostObserver | None = None,
    ) -> None:
        """Instantiate a game loop using an existing GameServer instance."""
        self.server = server
        self.quiz = quiz
        self.questions = quiz.questions
        self.settings = quiz.settings
        self.min_players = min_players
        self.lobby_countdown = lobby_countdown
        self.question_time = question_time
        self.player_stats: dict[str, PlayerGameStats] = {}
        self.host_observer = host_observer
        self.running = False

    def start(self) -> None:
        """Start the game loop — lobby, auto-start, questions, final scores."""
        self.running = True
        self._wait_for_players()
        if not self.running:
            return

        self._broadcast_game_start()
        self._run_questions()
        self._send_game_over()

    # ------------------------------------------------------------------
    # Lobby / auto-start
    # ------------------------------------------------------------------

    def _wait_for_players(self) -> None:
        """Broadcast lobby state every second until min_players join or time runs out."""
        logger.info("Waiting for players...")
        countdown_remaining = self.lobby_countdown
        while countdown_remaining > 0:
            players = self.server.get_players()
            if len(players) >= self.min_players:
                break
            self.server.broadcast(
                {
                    "type": MsgType.LOBBY_UPDATE,
                    "players": players,
                    "host_started_countdown": False,
                    "countdown_remaining": None,
                }
            )
            time.sleep(1)
            countdown_remaining -= 1

        if len(self.server.get_players()) < self.min_players:
            logger.info("Not enough players — aborting game.")
            self.running = False

    def _broadcast_game_start(self) -> None:
        """Send GAME_START message to all players and initialise score tables."""
        players = self.server.get_players()
        self.player_stats = {name: PlayerGameStats(name=name) for name in players}
        self.server.broadcast({"type": MsgType.GAME_START})
        self._notify_observer("on_game_started", len(self.questions))
        logger.info("Game started with players: %s", players)

    # ------------------------------------------------------------------
    # Question loop
    # ------------------------------------------------------------------

    def _collect_answers(
        self,
        q_index: int,
        question_start_time: float,
        time_limit: float,
        players: list[str],
    ) -> dict[str, tuple[int, float]]:
        """Block until all players answer or the question window closes.

        ``question_start_time`` is the ``time.monotonic()`` value recorded
        *before* the QUESTION broadcast, so ``deadline = question_start_time +
        time_limit`` is the same authoritative window used for scoring. Calling
        ``time.monotonic()`` here instead would introduce a small drift equal to
        the broadcast latency.

        Timer ticks are emitted approximately every 100 ms via UDP while the
        window is open; a final tick at 0.0 is sent on exit.

        Returns a dict mapping display_name -> (choice, receive_time) for every
        answer that arrived within the window.
        """
        answer_q: _queue.Queue[tuple[str, int, int, float]] = _queue.Queue()
        self.server.set_answer_queue(answer_q)
        deadline = question_start_time + time_limit
        next_tick = question_start_time
        answers: dict[str, tuple[int, float]] = {}
        expected = set(players)
        current_answered = 0
        try:
            while time.monotonic() < deadline and len(answers) < len(expected):
                now = time.monotonic()
                if now >= next_tick:
                    remaining_now = max(0.0, deadline - now)
                    self.server.broadcast_timer_tick(q_index, remaining_now)
                    self._notify_observer(
                        "on_timer_tick",
                        q_index,
                        remaining_now,
                        current_answered,
                        len(expected),
                    )
                    next_tick = now + 0.1

                remaining = deadline - now
                if remaining <= 0:
                    break
                try:
                    wait_for = min(remaining, max(0.0, next_tick - time.monotonic()))
                    name, idx, choice, receive_time = answer_q.get(
                        timeout=max(0.01, wait_for)
                    )
                    if idx == q_index and name in expected and name not in answers:
                        answers[name] = (choice, receive_time)
                        current_answered = len(answers)
                        self.server.broadcast_answer_count(
                            q_index, answered=current_answered, total=len(expected)
                        )
                        self._notify_observer(
                            "on_timer_tick",
                            q_index,
                            max(0.0, deadline - receive_time),
                            current_answered,
                            len(expected),
                        )
                except _queue.Empty:
                    pass
        finally:
            self.server.set_answer_queue(None)

        self.server.broadcast_timer_tick(q_index, 0.0)
        self._notify_observer(
            "on_timer_tick", q_index, 0.0, current_answered, len(expected)
        )
        return answers

    def _run_questions(self) -> None:
        """Iterate through questions, collect answers, score, and broadcast results.

        For each question, ``question_start_time`` is recorded before the QUESTION
        broadcast and passed to ``_collect_answers`` as the authoritative deadline
        baseline. After collection, *all* players present at question start are
        scored — those who did not answer receive ``answer_elapsed=None``, which
        scores 0 and resets their streak. QUESTION_RESULT is sent individually to
        each player via ``send_to`` so ``your_score``/``your_total``/``your_streak``
        can be personalised.
        """
        show_correct = bool(self.settings.get("show_correct_answer", True))
        show_lb = bool(self.settings.get("show_leaderboard", True))
        pause_duration = int(self.settings.get("pause_between_questions", 5))

        for q_index, question in enumerate(self.questions):
            players_at_start = self.server.get_players()
            logger.info("Sending Q%d: %s", q_index + 1, question.text)
            question_start_time = time.monotonic()
            self._notify_observer(
                "on_question",
                q_index,
                len(self.questions),
                question.text,
                question.question_type,
                question.options,
                question.time_limit,
            )

            self.server.broadcast(
                {
                    "type": MsgType.QUESTION,
                    "index": q_index,
                    "text": question.text,
                    "question_type": question.question_type,
                    "options": question.options,
                    "time_limit": question.time_limit,
                }
            )

            answers = self._collect_answers(
                q_index, question_start_time, question.time_limit, players_at_start
            )

            question_results: dict[str, tuple[int, int]] = {}
            deadline = question_start_time + question.time_limit
            for player in players_at_start:
                if player not in self.player_stats:
                    continue  # joined after game start - ignore
                stats = self.player_stats[player]
                rtt = self.server.get_client_rtt(player)
                answer = answers.get(player)
                if answer is None:
                    result = score_answer(
                        stats,
                        is_correct=False,
                        time_remaining=0.0,
                        time_limit=question.time_limit,
                        answer_elapsed=None,
                        rtt=rtt,
                    )
                else:
                    choice, receive_time = answer
                    answer_elapsed = receive_time - question_start_time
                    time_remaining = max(0.0, deadline - receive_time)
                    result = score_answer(
                        stats,
                        is_correct=choice == question.answer,
                        time_remaining=time_remaining,
                        time_limit=question.time_limit,
                        answer_elapsed=answer_elapsed,
                        rtt=rtt,
                    )
                question_results[player] = (result.score, result.streak)

            leaderboard = build_leaderboard(self.player_stats)
            self._notify_observer(
                "on_question_result",
                question.answer,
                leaderboard,
                pause_duration,
                show_correct,
                show_lb,
            )

            for player in players_at_start:
                if player not in self.player_stats:
                    continue  # joined after game start - no stats, no result to send
                your_score, your_streak = question_results[player]
                self.server.send_to(
                    player,
                    {
                        "type": MsgType.QUESTION_RESULT,
                        "correct_answer": question.answer,
                        "your_score": your_score,
                        "your_total": self.player_stats[player].total_score,
                        "your_streak": your_streak,
                        "leaderboard": leaderboard,
                        "show_correct_answer": show_correct,
                        "show_leaderboard": show_lb,
                        "pause_duration": pause_duration,
                    },
                )
            time.sleep(pause_duration)

    # ------------------------------------------------------------------
    # Game over
    # ------------------------------------------------------------------

    def _send_game_over(self) -> None:
        """Send final GAME_OVER message with rankings."""
        final_rankings = build_final_rankings(self.player_stats)
        self.server.broadcast(
            {
                "type": MsgType.GAME_OVER,
                "final_rankings": final_rankings,
                "total_questions": len(self.questions),
            }
        )
        self._notify_observer("on_game_over", final_rankings, len(self.questions))
        logger.info("Game over")

    def _notify_observer(self, method: str, *args: Any) -> None:
        """Call one observer method if a host observer is attached."""
        if self.host_observer is None:
            return
        callback = getattr(self.host_observer, method, None)
        if callback is not None:
            callback(*args)
