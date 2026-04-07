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

from .protocol import MsgType
from .quiz import Quiz
from .server import GameServer

logger = logging.getLogger(__name__)


class GameLoop:
    """Manages the game loop using an existing GameServer instance."""

    def __init__(
        self,
        server: GameServer,
        quiz: Quiz,
        min_players: int = 2,
        lobby_countdown: int = 10,
        question_time: int = 15,
    ) -> None:
        """Instantiate a game loop using an existing GameServer instance."""
        self.server = server
        self.quiz = quiz
        self.questions = quiz.questions
        self.settings = quiz.settings
        self.min_players = min_players
        self.lobby_countdown = lobby_countdown
        self.question_time = question_time
        self.scores: dict[str, int] = {}
        self.correct_counts: dict[str, int] = {}
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
        self.scores = {name: 0 for name in players}
        self.correct_counts = {name: 0 for name in players}
        self.server.broadcast({"type": MsgType.GAME_START})
        logger.info("Game started with players: %s", players)

    # ------------------------------------------------------------------
    # Question loop
    # ------------------------------------------------------------------

    def _collect_answers(
        self, q_index: int, time_limit: float, players: list[str]
    ) -> dict[str, int]:
        """Block until all players answer or time_limit expires.

        Returns a dict mapping display_name -> choice index for every answer
        that arrived within the window.
        """
        answer_q: _queue.Queue[tuple[str, int, int]] = _queue.Queue()
        self.server.set_answer_queue(answer_q)
        deadline = time.monotonic() + time_limit
        answers: dict[str, int] = {}
        expected = set(players)
        try:
            while time.monotonic() < deadline and len(answers) < len(expected):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    name, idx, choice = answer_q.get(timeout=min(remaining, 0.1))
                    if idx == q_index and name in expected:
                        answers[name] = choice
                except _queue.Empty:
                    pass
        finally:
            self.server.set_answer_queue(None)
        return answers

    def _run_questions(self) -> None:
        """Iterate through questions, collect answers, score, and broadcast results."""
        show_correct = bool(self.settings.get("show_correct_answer", True))
        show_lb = bool(self.settings.get("show_leaderboard", True))
        pause_duration = int(self.settings.get("pause_between_questions", 5))

        for q_index, question in enumerate(self.questions):
            players_at_start = self.server.get_players()
            logger.info("Sending Q%d: %s", q_index + 1, question.text)

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
                q_index, question.time_limit, players_at_start
            )

            # Placeholder scoring — replaced once Task 7 scoring engine is available.
            for player, choice in answers.items():
                if choice == question.answer:
                    self.scores[player] += 1
                    self.correct_counts[player] = self.correct_counts.get(player, 0) + 1

            leaderboard = [
                {"name": name, "score": score}
                for name, score in sorted(self.scores.items(), key=lambda x: -x[1])
            ]

            # your_score / your_total / your_streak are per-player fields; they
            # will be sent individually via send_to once the scoring engine (Task 7)
            # is integrated. For now broadcast 0 as a safe placeholder.
            self.server.broadcast(
                {
                    "type": MsgType.QUESTION_RESULT,
                    "correct_answer": question.answer,
                    "your_score": 0,
                    "your_total": 0,
                    "your_streak": 0,
                    "leaderboard": leaderboard,
                    "show_correct_answer": show_correct,
                    "show_leaderboard": show_lb,
                    "pause_duration": pause_duration,
                }
            )
            time.sleep(pause_duration)

    # ------------------------------------------------------------------
    # Game over
    # ------------------------------------------------------------------

    def _send_game_over(self) -> None:
        """Send final GAME_OVER message with rankings."""
        final_rankings = [
            {
                "rank": i + 1,
                "name": name,
                "score": score,
                "correct": self.correct_counts.get(name, 0),
                "streak": 0,
                "fastest_answer": 0.0,
            }
            for i, (name, score) in enumerate(
                sorted(self.scores.items(), key=lambda x: -x[1])
            )
        ]
        self.server.broadcast(
            {
                "type": MsgType.GAME_OVER,
                "final_rankings": final_rankings,
                "total_questions": len(self.questions),
            }
        )
        logger.info("Game over")
