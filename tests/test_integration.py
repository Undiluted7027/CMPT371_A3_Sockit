"""Integration tests for Task 16 — full session scenarios.

Covers the scenarios not addressed by test_game_loop.py:

  TestDisconnectMidGame  : player disconnects during a question; game continues
                           and remaining players receive PLAYER_DISCONNECTED +
                           GAME_OVER
  TestEdgeCases          : all players timeout; all wrong; below min_players;
                           streak multiplier across two questions
"""

import json
import socket
import threading
from collections.abc import Generator
from pathlib import Path

import pytest

from src.protocol import MsgType, recv_msg, send_msg
from src.quiz import Quiz, load_quiz
from src.server import GameServer
from src.serverGameLoop import GameLoop

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def server() -> Generator[GameServer, None, None]:
    """Create a GameServer."""
    s = GameServer(host="127.0.0.1", port=0)
    s.start()
    yield s
    s.stop()


def _join(port: int, code: str, name: str) -> socket.socket:
    """Connect, send JOIN, drain the initial lobby_update."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(("127.0.0.1", port))
    send_msg(sock, {"type": MsgType.JOIN, "session_code": code, "display_name": name})
    recv_msg(sock)  # initial lobby_update
    return sock


def _read_until(sock: socket.socket, msg_type: str, limit: int = 20) -> dict:
    sock.settimeout(5.0)
    for _ in range(limit):
        msg = recv_msg(sock)
        if msg["type"] == msg_type:
            return msg
    pytest.fail(f"Did not receive {msg_type!r} within {limit} messages")


def _make_quiz(tmp_path: Path, n_questions: int = 1, time_limit: float = 0.2) -> Quiz:
    questions = [
        {
            "text": f"Q{i + 1}",
            "type": "multiple_choice",
            "options": ["A", "B", "C", "D"],
            "answer": 0,
            "time_limit": time_limit,
        }
        for i in range(n_questions)
    ]
    data = {
        "title": "Integration Test Quiz",
        "settings": {"pause_between_questions": 0},
        "questions": questions,
    }
    path = tmp_path / "quiz.json"
    path.write_text(json.dumps(data))
    return load_quiz(str(path))


def _start_loop(server: GameServer, quiz: Quiz) -> threading.Thread:
    loop = GameLoop(server, quiz, min_players=2, lobby_countdown=0)
    t = threading.Thread(target=loop.start, daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# Mid-game disconnect
# ---------------------------------------------------------------------------


class TestDisconnectMidGame:
    """Player disconnects after game start; remaining player finishes the game."""

    def test_game_continues_after_mid_question_disconnect(
        self, server: GameServer, tmp_path: Path
    ) -> None:
        """Bob receives GAME_OVER even after Alice disconnects mid-question."""
        quiz = _make_quiz(tmp_path, time_limit=0.3)
        alice = _join(server.port, server.session_code, "Alice")
        bob = _join(server.port, server.session_code, "Bob")
        recv_msg(alice)  # drain Alice's lobby_update from Bob joining

        t = _start_loop(server, quiz)

        # Wait for both players to receive the question, then Alice drops.
        _read_until(alice, MsgType.QUESTION)
        _read_until(bob, MsgType.QUESTION)
        alice.close()

        # Bob should still receive QUESTION_RESULT and GAME_OVER.
        _read_until(bob, MsgType.QUESTION_RESULT)
        game_over = _read_until(bob, MsgType.GAME_OVER)

        assert game_over["total_questions"] == 1
        names = {r["name"] for r in game_over["final_rankings"]}
        assert "Alice" in names
        assert "Bob" in names

        t.join(timeout=5)
        bob.close()

    def test_player_disconnected_message_sent_to_remaining(
        self, server: GameServer, tmp_path: Path
    ) -> None:
        """Bob receives PLAYER_DISCONNECTED when Alice drops mid-question."""
        quiz = _make_quiz(tmp_path, time_limit=0.5)
        alice = _join(server.port, server.session_code, "Alice")
        bob = _join(server.port, server.session_code, "Bob")
        recv_msg(alice)

        _start_loop(server, quiz)

        _read_until(alice, MsgType.QUESTION)
        _read_until(bob, MsgType.QUESTION)
        alice.close()

        disconnected = _read_until(bob, MsgType.PLAYER_DISCONNECTED)
        assert disconnected["display_name"] == "Alice"

        bob.close()

    def test_disconnected_player_scores_zero_in_final_rankings(
        self, server: GameServer, tmp_path: Path
    ) -> None:
        """Alice, who disconnected without answering, scores 0 in GAME_OVER."""
        quiz = _make_quiz(tmp_path, time_limit=0.3)
        alice = _join(server.port, server.session_code, "Alice")
        bob = _join(server.port, server.session_code, "Bob")
        recv_msg(alice)

        _start_loop(server, quiz)

        _read_until(alice, MsgType.QUESTION)
        _read_until(bob, MsgType.QUESTION)
        alice.close()

        game_over = _read_until(bob, MsgType.GAME_OVER)
        alice_row = next(r for r in game_over["final_rankings"] if r["name"] == "Alice")
        assert alice_row["score"] == 0
        assert alice_row["correct"] == 0

        bob.close()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Boundary and negative-path scenarios."""

    def test_all_players_timeout_game_completes(
        self, server: GameServer, tmp_path: Path
    ) -> None:
        """Nobody answers — all score 0, GAME_OVER still arrives."""
        quiz = _make_quiz(tmp_path, time_limit=0.2)
        alice = _join(server.port, server.session_code, "Alice")
        bob = _join(server.port, server.session_code, "Bob")
        recv_msg(alice)

        t = _start_loop(server, quiz)

        game_over = _read_until(alice, MsgType.GAME_OVER)
        assert game_over["total_questions"] == 1
        for row in game_over["final_rankings"]:
            assert row["score"] == 0
            assert row["correct"] == 0

        t.join(timeout=5)
        alice.close()
        bob.close()

    def test_all_wrong_answers_score_zero(
        self, server: GameServer, tmp_path: Path
    ) -> None:
        """Both players submit wrong answers — both score 0."""
        quiz = _make_quiz(tmp_path, time_limit=0.3)  # answer is index 0
        alice = _join(server.port, server.session_code, "Alice")
        bob = _join(server.port, server.session_code, "Bob")
        recv_msg(alice)

        _start_loop(server, quiz)

        _read_until(alice, MsgType.QUESTION)
        _read_until(bob, MsgType.QUESTION)
        # Submit wrong answer (index 1; correct is 0)
        send_msg(alice, {"type": MsgType.ANSWER, "question_index": 0, "choice": 1})
        send_msg(bob, {"type": MsgType.ANSWER, "question_index": 0, "choice": 2})

        alice_result = _read_until(alice, MsgType.QUESTION_RESULT)
        bob_result = _read_until(bob, MsgType.QUESTION_RESULT)

        assert alice_result["your_score"] == 0
        assert alice_result["your_streak"] == 0
        assert bob_result["your_score"] == 0
        assert bob_result["your_streak"] == 0

        alice.close()
        bob.close()

    def test_not_enough_players_loop_exits_without_game_start(
        self, server: GameServer, tmp_path: Path
    ) -> None:
        """With only one player and lobby_countdown=0.

        The loop exits immediately
        without broadcasting GAME_START.
        """
        quiz = _make_quiz(tmp_path)
        alice = _join(server.port, server.session_code, "Alice")

        loop = GameLoop(server, quiz, min_players=2, lobby_countdown=0)
        t = threading.Thread(target=loop.start, daemon=True)
        t.start()
        t.join(timeout=3)

        assert not loop.running
        # Alice must not have received GAME_START.
        alice.settimeout(0.3)
        with pytest.raises((TimeoutError, OSError)):
            _read_until(alice, MsgType.GAME_START, limit=5)

        alice.close()

    def test_streak_multiplier_applied_on_consecutive_correct(
        self, server: GameServer, tmp_path: Path
    ) -> None:
        """Two consecutive correct answers give streak=2 and score > base on Q2."""
        quiz_data = {
            "title": "Streak Quiz",
            "settings": {"pause_between_questions": 0},
            "questions": [
                {
                    "text": "Q1",
                    "type": "multiple_choice",
                    "options": ["A", "B", "C", "D"],
                    "answer": 0,
                    "time_limit": 0.3,
                },
                {
                    "text": "Q2",
                    "type": "multiple_choice",
                    "options": ["A", "B", "C", "D"],
                    "answer": 0,
                    "time_limit": 0.3,
                },
            ],
        }
        path = tmp_path / "streak.json"
        path.write_text(json.dumps(quiz_data))
        quiz = load_quiz(str(path))

        alice = _join(server.port, server.session_code, "Alice")
        bob = _join(server.port, server.session_code, "Bob")
        recv_msg(alice)

        t = _start_loop(server, quiz)

        # Q1 — both answer correctly.
        _read_until(alice, MsgType.QUESTION)
        _read_until(bob, MsgType.QUESTION)
        send_msg(alice, {"type": MsgType.ANSWER, "question_index": 0, "choice": 0})
        send_msg(bob, {"type": MsgType.ANSWER, "question_index": 0, "choice": 0})
        r1 = _read_until(alice, MsgType.QUESTION_RESULT)
        _read_until(bob, MsgType.QUESTION_RESULT)
        assert r1["your_streak"] == 1

        # Q2 — Alice answers correctly again (Bob times out).
        _read_until(alice, MsgType.QUESTION)
        _read_until(bob, MsgType.QUESTION)
        send_msg(alice, {"type": MsgType.ANSWER, "question_index": 1, "choice": 0})
        r2 = _read_until(alice, MsgType.QUESTION_RESULT)
        _read_until(bob, MsgType.QUESTION_RESULT)

        assert r2["your_streak"] == 2
        # With 1.1x multiplier on Q2, score must exceed the base floor of 500.
        assert r2["your_score"] >= 550

        t.join(timeout=5)
        alice.close()
        bob.close()
