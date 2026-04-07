"""Handles:
- Lobby auto-start countdown
- Game start
- Question sequencing with timers
- Collecting answers and scoring
- Sending QUESTION, QUESTION_RESULT, and GAME_OVER messages
"""

import threading
import time
from typing import Any

from src.protocol import MsgType
from src.server import GameServer


class GameLoop:
    """Manages the game loop using an existing GameServer instance."""

    def __init__(
        self,
        server: GameServer,
        questions: list[dict[str, Any]],
        min_players: int = 2,
        lobby_countdown: int = 10,
        question_time: int = 15,
    ) -> None:
        self.server = server
        self.questions = questions
        self.min_players = min_players
        self.lobby_countdown = lobby_countdown
        self.question_time = question_time
        self.scores: dict[str, int] = {}
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
        """Wait until min_players join or countdown expires."""
        print("Waiting for players...")
        countdown_remaining = self.lobby_countdown
        while countdown_remaining > 0:
            players = self.server.get_players()
            if len(players) >= self.min_players:
                break
            self.server.broadcast(
                {
                    "type": MsgType.LOBBY_UPDATE,
                    "players": players,
                    "host_started_countdown": True,
                    "countdown_remaining": countdown_remaining,
                }
            )
            time.sleep(1)
            countdown_remaining -= 1

        if len(self.server.get_players()) < self.min_players:
            print("Not enough players — aborting game.")
            self.running = False

    def _broadcast_game_start(self) -> None:
        """Send GAME_START message to all players and init scores."""
        players = self.server.get_players()
        self.scores = {name: 0 for name in players}
        self.server.broadcast({"type": MsgType.GAME_START})
        print("Game started with players:", players)

    # ------------------------------------------------------------------
    # Question loop
    # ------------------------------------------------------------------

    def _run_questions(self) -> None:
        """Iterate through questions, send them, collect answers, update scores."""
        for q_index, question in enumerate(self.questions):
            print(f"Sending Q{q_index + 1}: {question['text']}")
            # Broadcast question
            self.server.broadcast(
                {
                    "type": MsgType.QUESTION,
                    "index": q_index,
                    "text": question["text"],
                    "question_type": question.get("question_type", "multiple_choice"),
                    "options": question["options"],
                    "time_limit": self.question_time,
                }
            )

            # Collect answers with timeout
            answers: dict[str, Any] = {}
            threads: list[threading.Thread] = []

            def collect_answer(player_name: str) -> None:
                """Wait for player's ANSWER message for this question."""
                pass  # placeholder - integrate with GameServer's _dispatch for ANSWER

            for player_name in self.server.get_players():
                t = threading.Thread(target=collect_answer, args=(player_name,))
                t.start()
                threads.append(t)

            for t in threads:
                t.join(timeout=self.question_time)

            # Score calculation (example: correct_answer field in question dict)
            for player, ans in answers.items():
                if ans == question.get("answer"):
                    self.scores[player] += 1

            # Send QUESTION_RESULT
            leaderboard = [
                {"name": name, "score": score}
                for name, score in sorted(self.scores.items(), key=lambda x: -x[1])
            ]
            self.server.broadcast(
                {
                    "type": MsgType.QUESTION_RESULT,
                    "correct_answer": question.get("answer"),
                    "your_score": None,  # could set per-player score if we tracked answers in collect_answer
                    "your_total": None,
                    "your_streak": None,
                    "leaderboard": leaderboard,
                    "show_correct_answer": True,
                    "show_leaderboard": True,
                    "pause_duration": 5,
                }
            )
            time.sleep(5)  # pause before next question

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
                "correct": score,
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
        print("Game over")
