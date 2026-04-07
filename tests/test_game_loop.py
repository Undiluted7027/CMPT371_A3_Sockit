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
from collections.abc import Generator
from pathlib import Path
from typing import cast

import pytest

from src.protocol import MsgType, recv_msg, recv_udp, send_msg, send_udp
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


def _register_udp(port: int, session_code: str, display_name: str) -> socket.socket:
    """Register one player's UDP address by sending a valid ping."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(2.0)
    send_udp(
        sock,
        {
            "type": MsgType.PING,
            "timestamp": 1.0,
            "session_code": session_code,
            "display_name": display_name,
        },
        ("127.0.0.1", port),
    )
    recv_udp(sock)  # pong
    return sock


def _read_until(sock: socket.socket, msg_type: str, limit: int = 15) -> dict:
    """Read and discard messages until one matching msg_type is found."""
    sock.settimeout(5.0)
    for _ in range(limit):
        msg = recv_msg(sock)
        if msg["type"] == msg_type:
            return msg
    pytest.fail(f"Did not receive {msg_type!r} within {limit} messages")


def _read_udp_until(sock: socket.socket, msg_type: str, limit: int = 30) -> dict:
    """Read and discard UDP datagrams until one matching msg_type is found."""
    for _ in range(limit):
        msg, _ = recv_udp(sock)
        if msg["type"] == msg_type:
            return cast("dict", msg)
    pytest.fail(f"Did not receive {msg_type!r} within {limit} datagrams")


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

    def test_game_loop_emits_timer_ticks_to_registered_udp_clients(
        self, server: GameServer, tmp_path: Path
    ) -> None:
        """Registered UDP clients receive timer ticks during active questions."""
        quiz = _make_quiz(tmp_path, time_limit=0.3)
        alice = _join_player(server.port, server.session_code, "Alice")
        bob = _join_player(server.port, server.session_code, "Bob")
        recv_msg(alice)
        alice_udp = _register_udp(server.udp_port, server.session_code, "Alice")

        _start_loop(server, quiz)

        msg = _read_udp_until(alice_udp, MsgType.TIMER_TICK)

        assert msg["question_index"] == 0
        assert isinstance(msg["remaining"], float)

        alice_udp.close()
        alice.close()
        bob.close()

    def test_game_loop_emits_answer_count_to_registered_udp_clients(
        self, server: GameServer, tmp_path: Path
    ) -> None:
        """Registered UDP clients receive answer-count updates as answers arrive."""
        quiz = _make_quiz(tmp_path, time_limit=0.3)
        alice = _join_player(server.port, server.session_code, "Alice")
        bob = _join_player(server.port, server.session_code, "Bob")
        recv_msg(alice)
        alice_udp = _register_udp(server.udp_port, server.session_code, "Alice")

        _start_loop(server, quiz)

        _read_until(alice, MsgType.QUESTION)
        _read_until(bob, MsgType.QUESTION)
        send_msg(alice, {"type": MsgType.ANSWER, "question_index": 0, "choice": 3})

        msg = _read_udp_until(alice_udp, MsgType.ANSWER_COUNT)

        assert msg["question_index"] == 0
        assert msg["answered"] == 1
        assert msg["total"] == 2

        alice_udp.close()
        alice.close()
        bob.close()
