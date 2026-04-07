"""Integration tests for serverGameLoop.py — GameLoop end-to-end behaviour.

Coverage map
------------
TestGameLoop : GAME_START broadcast; QUESTION broadcast fields;
               correct answer recorded in QUESTION_RESULT leaderboard;
               QUESTION_RESULT sent after timer expires;
               GAME_OVER sent with rankings after all questions
"""

import json
import socket
import threading
from pathlib import Path
from collections.abc import Generator

import pytest

from src.protocol import MsgType, recv_msg, send_msg
from src.quiz import load_quiz
from src.server import GameServer
from src.serverGameLoop import GameLoop


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def server() -> Generator[GameServer, None, None]:
    """Start a GameServer on an OS-assigned ephemeral port; stop after the test."""
    s = GameServer(host="127.0.0.1", port=0)
    s.start()
    yield s
    s.stop()


def _make_quiz(tmp_path: Path, time_limit: float = 0.2) -> object:
    """Write a single-question quiz JSON to tmp_path and return the loaded Quiz."""
    quiz_data = {
        "title": "Test Quiz",
        "settings": {"pause_between_questions": 0},
        "questions": [
            {
                "text": "What is 2+2?",
                "type": "multiple_choice",
                "options": ["1", "2", "3", "4"],
                "answer": 3,
                "time_limit": time_limit,
            }
        ],
    }
    f = tmp_path / "quiz.json"
    f.write_text(json.dumps(quiz_data))
    return load_quiz(str(f))


def _join_player(port: int, code: str, name: str) -> socket.socket:
    """Connect, send JOIN, and consume the initial lobby_update response."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(("127.0.0.1", port))
    send_msg(sock, {"type": MsgType.JOIN, "session_code": code, "display_name": name})
    recv_msg(sock)  # initial lobby_update
    return sock


def _read_until(sock: socket.socket, msg_type: str, limit: int = 15) -> dict:
    """Read and discard messages until one matching msg_type is found."""
    sock.settimeout(5.0)
    for _ in range(limit):
        msg = recv_msg(sock)
        if msg["type"] == msg_type:
            return msg
    pytest.fail(f"Did not receive {msg_type!r} within {limit} messages")


def _start_loop(
    server: GameServer, quiz: object, **kwargs: object
) -> tuple["GameLoop", threading.Thread]:
    """Instantiate a GameLoop and run it in a daemon thread."""
    loop = GameLoop(server, quiz, min_players=2, lobby_countdown=0, **kwargs)  # type: ignore[arg-type]
    t = threading.Thread(target=loop.start, daemon=True)
    t.start()
    return loop, t


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGameLoop:
    """Integration tests for GameLoop running against a live GameServer."""

    def test_game_loop_sends_game_start(
        self, server: GameServer, tmp_path: Path
    ) -> None:
        """GameLoop broadcasts GAME_START to all joined clients."""
        quiz = _make_quiz(tmp_path)
        alice = _join_player(server.port, server.session_code, "Alice")
        bob = _join_player(server.port, server.session_code, "Bob")
        recv_msg(alice)  # drain Alice's lobby_update from Bob's join

        _, t = _start_loop(server, quiz)

        assert _read_until(alice, MsgType.GAME_START)["type"] == MsgType.GAME_START
        assert _read_until(bob, MsgType.GAME_START)["type"] == MsgType.GAME_START

        t.join(timeout=5)
        alice.close()
        bob.close()

    def test_game_loop_sends_question(self, server: GameServer, tmp_path: Path) -> None:
        """GameLoop broadcasts QUESTION with correct text, options, and time_limit."""
        quiz = _make_quiz(tmp_path)
        alice = _join_player(server.port, server.session_code, "Alice")
        bob = _join_player(server.port, server.session_code, "Bob")
        recv_msg(alice)

        _start_loop(server, quiz)

        msg = _read_until(alice, MsgType.QUESTION)
        assert msg["index"] == 0
        assert msg["text"] == "What is 2+2?"
        assert msg["options"] == ["1", "2", "3", "4"]
        assert msg["time_limit"] == pytest.approx(0.2)
        assert msg["question_type"] == "multiple_choice"

        alice.close()
        bob.close()

    def test_game_loop_records_correct_answer(
        self, server: GameServer, tmp_path: Path
    ) -> None:
        """Correct answer from Alice increments her score in the QUESTION_RESULT leaderboard."""
        quiz = _make_quiz(tmp_path)
        alice = _join_player(server.port, server.session_code, "Alice")
        bob = _join_player(server.port, server.session_code, "Bob")
        recv_msg(alice)

        _start_loop(server, quiz)

        # Wait until the question is live, then answer.
        _read_until(alice, MsgType.QUESTION)
        _read_until(bob, MsgType.QUESTION)
        send_msg(alice, {"type": MsgType.ANSWER, "question_index": 0, "choice": 3})

        result = _read_until(alice, MsgType.QUESTION_RESULT)
        lb = {entry["name"]: entry["score"] for entry in result["leaderboard"]}
        assert lb["Alice"] == 1
        assert lb["Bob"] == 0

        alice.close()
        bob.close()

    def test_game_loop_sends_question_result(
        self, server: GameServer, tmp_path: Path
    ) -> None:
        """QUESTION_RESULT is broadcast after the question timer expires."""
        quiz = _make_quiz(tmp_path)
        alice = _join_player(server.port, server.session_code, "Alice")
        bob = _join_player(server.port, server.session_code, "Bob")
        recv_msg(alice)

        _start_loop(server, quiz)

        _read_until(alice, MsgType.QUESTION)  # wait for question to start
        result = _read_until(alice, MsgType.QUESTION_RESULT)

        assert result["correct_answer"] == 3
        assert "leaderboard" in result
        assert isinstance(result["leaderboard"], list)

        alice.close()
        bob.close()

    def test_game_loop_sends_game_over(
        self, server: GameServer, tmp_path: Path
    ) -> None:
        """GAME_OVER is broadcast with final rankings after all questions complete."""
        quiz = _make_quiz(tmp_path)
        alice = _join_player(server.port, server.session_code, "Alice")
        bob = _join_player(server.port, server.session_code, "Bob")
        recv_msg(alice)

        _, t = _start_loop(server, quiz)

        game_over = _read_until(alice, MsgType.GAME_OVER)
        assert game_over["total_questions"] == 1
        assert len(game_over["final_rankings"]) == 2
        names = {r["name"] for r in game_over["final_rankings"]}
        assert names == {"Alice", "Bob"}

        t.join(timeout=5)
        alice.close()
        bob.close()
