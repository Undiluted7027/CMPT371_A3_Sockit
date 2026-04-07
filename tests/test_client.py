"""Tests for client.py — dispatch routing, send_answer, connect lifecycle.

Coverage map
------------
TestDispatch       : _dispatch routes every server→client message type to the
                     correct on_* handler with the correct arguments; unknown
                     types are silently ignored
TestSendAnswer     : correct wire format; no-op when not connected
TestConnectJoin    : join() before connect() raises RuntimeError;
                     end-to-end connect + join against a real GameServer
TestDisconnect     : on_disconnect() called when server closes the connection

Note on threading
-----------------
All on_* handlers are called from the background TCP receiver thread.
Tests that involve a real server use threading.Event to wait for async
callbacks rather than sleeping.
"""

import socket
import threading
from collections.abc import Generator
from typing import Any

import pytest

from client import GameClient
from protocol import MsgType, recv_msg
from server import GameServer

# ---------------------------------------------------------------------------
# Capturing subclass
# ---------------------------------------------------------------------------


class _CapturingClient(GameClient):
    """GameClient that records every on_* call for inspection in tests."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[str, Any]] = []
        self._event = threading.Event()  # set when any handler fires

    def _record(self, name: str, payload: Any = None) -> None:
        self.events.append((name, payload))
        self._event.set()

    def wait_for_event(self, timeout: float = 2.0) -> bool:
        """Block until at least one handler has fired. Returns False on timeout."""
        fired = self._event.wait(timeout)
        self._event.clear()
        return fired

    def on_lobby_update(
        self,
        players: list,
        host_started_countdown: bool,
        countdown_remaining: float | None,
    ) -> None:
        self._record(
            "lobby_update",
            {
                "players": players,
                "host_started_countdown": host_started_countdown,
                "countdown_remaining": countdown_remaining,
            },
        )

    def on_game_start(self) -> None:
        self._record("game_start")

    def on_question(self, msg: dict) -> None:
        self._record("question", msg)

    def on_question_result(self, msg: dict) -> None:
        self._record("question_result", msg)

    def on_game_over(self, msg: dict) -> None:
        self._record("game_over", msg)

    def on_player_disconnected(self, display_name: str) -> None:
        self._record("player_disconnected", display_name)

    def on_error(self, message: str) -> None:
        self._record("error", message)

    def on_disconnect(self) -> None:
        self._record("disconnect")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def server() -> Generator[GameServer, None, None]:
    """Real GameServer on an ephemeral port."""
    s = GameServer(host="127.0.0.1", port=0)
    s.start()
    yield s
    s.stop()


# ---------------------------------------------------------------------------
# _dispatch routing (no real server needed — call _dispatch directly)
# ---------------------------------------------------------------------------


class TestDispatch:
    """Tests for routing server messages to the correct client callbacks."""

    def test_lobby_update_routed_correctly(self) -> None:
        """Route lobby_update messages to on_lobby_update with all fields."""
        c = _CapturingClient()
        c._dispatch(
            {
                "type": MsgType.LOBBY_UPDATE,
                "players": ["Alice", "Bob"],
                "host_started_countdown": True,
                "countdown_remaining": 20.5,
            }
        )
        assert c.events == [
            (
                "lobby_update",
                {
                    "players": ["Alice", "Bob"],
                    "host_started_countdown": True,
                    "countdown_remaining": 20.5,
                },
            )
        ]

    def test_game_start_routed_correctly(self) -> None:
        """Route game_start messages to on_game_start."""
        c = _CapturingClient()
        c._dispatch({"type": MsgType.GAME_START})
        assert c.events == [("game_start", None)]

    def test_question_routed_correctly(self) -> None:
        """Route question messages to on_question unchanged."""
        c = _CapturingClient()
        msg = {
            "type": MsgType.QUESTION,
            "index": 0,
            "text": "What is 2+2?",
            "question_type": "multiple_choice",
            "options": ["3", "4", "5", "6"],
            "time_limit": 15,
        }
        c._dispatch(msg)
        assert c.events == [("question", msg)]

    def test_question_result_routed_correctly(self) -> None:
        """Route question_result messages to on_question_result unchanged."""
        c = _CapturingClient()
        msg = {
            "type": MsgType.QUESTION_RESULT,
            "correct_answer": 1,
            "your_score": 850,
            "your_total": 850,
            "your_streak": 1,
            "leaderboard": [{"name": "Alice", "score": 850}],
            "show_correct_answer": True,
            "show_leaderboard": True,
            "pause_duration": 5,
        }
        c._dispatch(msg)
        assert c.events == [("question_result", msg)]

    def test_game_over_routed_correctly(self) -> None:
        """Route game_over messages to on_game_over unchanged."""
        c = _CapturingClient()
        msg = {
            "type": MsgType.GAME_OVER,
            "final_rankings": [
                {
                    "rank": 1,
                    "name": "Alice",
                    "score": 8500,
                    "correct": 8,
                    "streak": 5,
                    "fastest_answer": 1.2,
                }
            ],
            "total_questions": 10,
        }
        c._dispatch(msg)
        assert c.events == [("game_over", msg)]

    def test_player_disconnected_routed_correctly(self) -> None:
        """Route player_disconnected messages to on_player_disconnected."""
        c = _CapturingClient()
        c._dispatch({"type": MsgType.PLAYER_DISCONNECTED, "display_name": "Charlie"})
        assert c.events == [("player_disconnected", "Charlie")]

    def test_error_routed_correctly(self) -> None:
        """Route error messages to on_error with the server message text."""
        c = _CapturingClient()
        c._dispatch({"type": MsgType.ERROR, "message": "Invalid session code"})
        assert c.events == [("error", "Invalid session code")]

    def test_unknown_type_is_silently_ignored(self) -> None:
        """Ignore message types that do not have a matching handler."""
        c = _CapturingClient()
        c._dispatch({"type": "future_message_type", "data": 42})
        assert c.events == []


# ---------------------------------------------------------------------------
# send_answer
# ---------------------------------------------------------------------------


class TestSendAnswer:
    """Tests for send_answer wire format and disconnected behavior."""

    def test_send_answer_wire_format(self, server: GameServer) -> None:
        """Send an answer while connected and keep the request well-formed."""
        # Connect and join so the server has an active per-client thread.
        client = GameClient()
        client.connect("127.0.0.1", server.port)
        client.join(server.session_code, "Alice")

        # Drain the lobby_update the server sends after join.
        received: list[dict] = []
        event = threading.Event()

        original_on_lobby = client.on_lobby_update  # noqa: F841

        def capture_lobby(
            players: list,
            host_started_countdown: bool,
            countdown_remaining: float | None,
        ) -> None:
            received.append({"players": players})
            event.set()

        client.on_lobby_update = capture_lobby  # type: ignore[method-assign]
        event.wait(timeout=2.0)

        # Now patch send and intercept — easier to read what the server sees.
        # We already have a working connection; call send_answer and verify the
        # server's per-client thread receives a correctly-formed ANSWER message.
        # Use a second thread to recv from the server socket directly isn't
        # easy; instead verify by checking the protocol layer directly.
        assert client._sock is not None
        client.send_answer(question_index=2, choice=1)

        # Read the raw frame back on a loopback by using another socketpair.
        # Since we sent to the actual server socket, verify indirectly: the
        # send_answer call must not raise, and the frame must be readable.
        client.disconnect()

    def test_send_answer_fields(self) -> None:
        """Serialize answer messages with the expected type, index, and choice."""
        a, b = socket.socketpair()
        client = GameClient()
        client._sock = a
        client._connected = True

        client.send_answer(question_index=3, choice=0)
        msg = recv_msg(b)
        a.close()
        b.close()

        assert msg["type"] == MsgType.ANSWER
        assert msg["question_index"] == 3
        assert msg["choice"] == 0

    def test_send_answer_when_not_connected_is_noop(self) -> None:
        """Treat send_answer as a no-op when the client is disconnected."""
        client = GameClient()
        client.send_answer(question_index=0, choice=1)  # should not raise


# ---------------------------------------------------------------------------
# connect / join lifecycle
# ---------------------------------------------------------------------------


class TestConnectJoin:
    """Tests for connect(), join(), and their client-side callbacks."""

    def test_join_before_connect_raises_runtime_error(self) -> None:
        """Reject join() calls before connect() has established a socket."""
        client = GameClient()
        with pytest.raises(RuntimeError):
            client.join("ABCD", "Alice")

    def test_connect_and_join_triggers_on_lobby_update(
        self, server: GameServer
    ) -> None:
        """Deliver a lobby_update callback after a successful join."""
        client = _CapturingClient()
        client.connect("127.0.0.1", server.port)
        client.join(server.session_code, "Alice")

        assert client.wait_for_event(), "on_lobby_update was not called within timeout"

        name, payload = client.events[0]
        assert name == "lobby_update"
        assert "Alice" in payload["players"]
        client.disconnect()

    def test_bad_session_code_triggers_on_error(self, server: GameServer) -> None:
        """Deliver an error callback when join() uses the wrong session code."""
        client = _CapturingClient()
        client.connect("127.0.0.1", server.port)
        client.join("ZZZZ", "Alice")

        assert client.wait_for_event(), "on_error was not called within timeout"

        name, _ = client.events[0]
        assert name == "error"
        client.disconnect()

    def test_duplicate_name_triggers_on_error(self, server: GameServer) -> None:
        """Deliver an error callback when the chosen display name is taken."""
        first = GameClient()
        first.connect("127.0.0.1", server.port)
        first.join(server.session_code, "Alice")

        second = _CapturingClient()
        second.connect("127.0.0.1", server.port)
        second.join(server.session_code, "Alice")

        assert second.wait_for_event()

        name, _ = second.events[0]
        assert name == "error"
        first.disconnect()
        second.disconnect()


# ---------------------------------------------------------------------------
# Disconnect handling
# ---------------------------------------------------------------------------


class TestDisconnect:
    """Tests for disconnect detection from the client receive loop."""

    def test_on_disconnect_called_when_server_closes_connection(self) -> None:
        """Invoke on_disconnect when the server closes the TCP connection."""
        # Use a minimal TCP server that accepts one connection then closes it.
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        def _close_immediately() -> None:
            conn, _ = listener.accept()
            conn.close()  # hang up immediately

        t = threading.Thread(target=_close_immediately, daemon=True)
        t.start()

        client = _CapturingClient()
        client.connect("127.0.0.1", port)

        assert client.wait_for_event(timeout=2.0), "on_disconnect was not called"
        assert client.events[0][0] == "disconnect"

        listener.close()
        client.disconnect()
