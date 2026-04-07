"""Tests for server.py — session code, join handshake, lobby state, disconnect.

Coverage map
------------
TestSessionCode    : length, character set, uniqueness across instances
TestJoinHandshake  : valid join → lobby_update; bad code; empty name;
                     whitespace-only name; duplicate name; full lobby
TestLobbyBroadcast : new joiner triggers lobby_update to existing clients;
                     get_players() is consistent with broadcast contents
TestDisconnect     : joined client leaves → lobby_update with reduced list;
                     unjoined client drops → no broadcast
TestBroadcast      : broadcast() reaches all joined clients;
                     send_to() delivers to exactly one named player;
                     send_to() non-existent name is a silent no-op
"""

import socket
import string
from collections.abc import Generator
from unittest.mock import patch

import pytest

from src.protocol import MsgType, recv_msg, send_msg
from src.server import GameServer, _generate_session_code

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


def _connect(port: int) -> socket.socket:
    """Return a TCP socket connected to the server at the given port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(("127.0.0.1", port))
    return sock


def _join(sock: socket.socket, code: str, name: str) -> dict:
    """Send a join message and return the server's first response."""
    send_msg(sock, {"type": MsgType.JOIN, "session_code": code, "display_name": name})
    return recv_msg(sock)


# ---------------------------------------------------------------------------
# Session code generation
# ---------------------------------------------------------------------------


class TestSessionCode:
    """Tests for session code generation and server-exposed session metadata."""

    def test_length_is_four(self) -> None:
        """Confirm generated session codes are four characters long."""
        assert len(_generate_session_code()) == 4

    def test_characters_are_uppercase_alphanumeric(self) -> None:
        """Confirm generated session codes use uppercase letters and digits."""
        allowed = set(string.ascii_uppercase + string.digits)
        for _ in range(50):
            assert set(_generate_session_code()).issubset(allowed)

    def test_unique_across_instances(self) -> None:
        """Ensure repeated generation yields more than one distinct code."""
        codes = {_generate_session_code() for _ in range(200)}
        # With 36^4 = 1,679,616 possibilities, 200 draws should be unique.
        assert len(codes) > 1

    def test_server_exposes_session_code(self, server: GameServer) -> None:
        """Confirm a started server exposes a four-character session code."""
        assert len(server.session_code) == 4


# ---------------------------------------------------------------------------
# Join handshake
# ---------------------------------------------------------------------------


class TestJoinHandshake:
    """Tests for join validation, lobby admission, and error handling."""

    def test_valid_join_returns_lobby_update(self, server: GameServer) -> None:
        """Accept a valid join request and return a lobby update."""
        sock = _connect(server.port)
        response = _join(sock, server.session_code, "Alice")
        sock.close()

        assert response["type"] == MsgType.LOBBY_UPDATE
        assert "Alice" in response["players"]

    def test_invalid_session_code_returns_error(self, server: GameServer) -> None:
        """Reject a join request with an incorrect session code."""
        sock = _connect(server.port)
        response = _join(sock, "ZZZZ", "Alice")
        sock.close()

        assert response["type"] == MsgType.ERROR

    def test_empty_display_name_returns_error(self, server: GameServer) -> None:
        """Reject a join request with an empty display name."""
        sock = _connect(server.port)
        response = _join(sock, server.session_code, "")
        sock.close()

        assert response["type"] == MsgType.ERROR

    def test_whitespace_only_name_returns_error(self, server: GameServer) -> None:
        """Reject a join request whose display name is whitespace only."""
        sock = _connect(server.port)
        response = _join(sock, server.session_code, "   ")
        sock.close()

        assert response["type"] == MsgType.ERROR

    def test_duplicate_name_returns_error(self, server: GameServer) -> None:
        """Reject a second join attempt that reuses an existing display name."""
        alice = _connect(server.port)
        _join(alice, server.session_code, "Alice")

        bob = _connect(server.port)
        response = _join(bob, server.session_code, "Alice")

        alice.close()
        bob.close()

        assert response["type"] == MsgType.ERROR
        assert "taken" in response["message"].lower()

    def test_unique_names_both_accepted(self, server: GameServer) -> None:
        """Accept distinct display names and include both players in the lobby."""
        alice = _connect(server.port)
        r1 = _join(alice, server.session_code, "Alice")

        bob = _connect(server.port)
        # Bob's join triggers a lobby_update to Alice too; discard it.
        r2 = _join(bob, server.session_code, "Bob")

        alice.close()
        bob.close()

        assert r1["type"] == MsgType.LOBBY_UPDATE
        assert r2["type"] == MsgType.LOBBY_UPDATE
        assert set(r2["players"]) == {"Alice", "Bob"}

    def test_error_does_not_close_connection_allowing_retry(
        self, server: GameServer
    ) -> None:
        """Allow a client to retry on the same socket after an error response."""
        sock = _connect(server.port)
        r1 = _join(sock, server.session_code, "")  # fails

        assert r1["type"] == MsgType.ERROR

        # Retry with a valid name — same connection, should succeed.
        r2 = _join(sock, server.session_code, "Alice")
        sock.close()

        assert r2["type"] == MsgType.LOBBY_UPDATE

    def test_full_lobby_returns_error(self, server: GameServer) -> None:
        """Reject a join request once the lobby reaches MAX_PLAYERS."""
        with patch("server.MAX_PLAYERS", 2):
            sockets = []
            for i in range(2):
                s = _connect(server.port)
                # Drain the lobby_update broadcast that goes to earlier joiners.
                _join(s, server.session_code, f"Player{i}")
                sockets.append(s)
                # Earlier clients receive updated lobby_update; drain them.
                for earlier in sockets[:-1]:
                    recv_msg(earlier)

            overflow = _connect(server.port)
            response = _join(overflow, server.session_code, "Extra")
            overflow.close()
            for s in sockets:
                s.close()

        assert response["type"] == MsgType.ERROR
        assert "full" in response["message"].lower()


# ---------------------------------------------------------------------------
# Lobby broadcast
# ---------------------------------------------------------------------------


class TestLobbyBroadcast:
    """Tests for lobby_update broadcasts and lobby state consistency."""

    def test_second_join_broadcasts_updated_list_to_first_client(
        self, server: GameServer
    ) -> None:
        """Broadcast the updated lobby list to existing clients after a join."""
        alice = _connect(server.port)
        _join(alice, server.session_code, "Alice")

        bob = _connect(server.port)
        _join(bob, server.session_code, "Bob")

        # Alice receives the lobby_update triggered by Bob's join.
        update_for_alice = recv_msg(alice)
        alice.close()
        bob.close()

        assert update_for_alice["type"] == MsgType.LOBBY_UPDATE
        assert set(update_for_alice["players"]) == {"Alice", "Bob"}

    def test_lobby_update_contains_host_started_countdown_false(
        self, server: GameServer
    ) -> None:
        """Include countdown metadata in lobby_update messages."""
        sock = _connect(server.port)
        response = _join(sock, server.session_code, "Alice")
        sock.close()

        assert response["host_started_countdown"] is False
        assert response["countdown_remaining"] is None

    def test_get_players_matches_lobby_update_players(self, server: GameServer) -> None:
        """Return the same joined players that appear in lobby_update payloads."""
        alice = _connect(server.port)
        _join(alice, server.session_code, "Alice")

        bob = _connect(server.port)
        _join(bob, server.session_code, "Bob")
        recv_msg(alice)  # drain Alice's lobby_update from Bob's join

        assert set(server.get_players()) == {"Alice", "Bob"}
        alice.close()
        bob.close()


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------


class TestDisconnect:
    """Tests for disconnect cleanup and the resulting lobby broadcasts."""

    def test_joined_client_disconnect_removed_from_lobby(
        self, server: GameServer
    ) -> None:
        """Remove joined clients from the lobby and broadcast the updated list.

        We verify by having a second client receive the lobby_update that the
        server broadcasts after the disconnect — this also proves the broadcast
        fires rather than relying on a sleep.
        """
        alice = _connect(server.port)
        _join(alice, server.session_code, "Alice")

        bob = _connect(server.port)
        _join(bob, server.session_code, "Bob")
        recv_msg(alice)  # drain Alice's lobby_update from Bob's join

        # Alice disconnects.
        alice.close()

        # Bob receives the lobby_update triggered by Alice leaving.
        update = recv_msg(bob)
        bob.close()

        assert update["type"] == MsgType.LOBBY_UPDATE
        assert "Alice" not in update["players"]
        assert "Bob" in update["players"]

    def test_unjoined_client_disconnect_sends_no_broadcast(
        self, server: GameServer
    ) -> None:
        """Ignore disconnects from clients that never successfully joined."""
        alice = _connect(server.port)
        _join(alice, server.session_code, "Alice")

        # Connect but never join.
        ghost = _connect(server.port)

        ghost.close()  # disconnect without joining

        # Give the server a moment to process the disconnect.
        # Alice must NOT receive any new message (no broadcast expected).
        alice.settimeout(0.2)
        with pytest.raises((TimeoutError, OSError)):
            recv_msg(alice)

        alice.close()


# ---------------------------------------------------------------------------
# broadcast() and send_to()
# ---------------------------------------------------------------------------


class TestBroadcast:
    """Tests for server-wide broadcast delivery and targeted sends."""

    def test_broadcast_reaches_all_joined_clients(self, server: GameServer) -> None:
        """Deliver a broadcast message to every joined client."""
        sockets = []
        for name in ("Alice", "Bob", "Charlie"):
            s = _connect(server.port)
            _join(s, server.session_code, name)
            sockets.append(s)
            # Drain the lobby_update that goes to all earlier clients.
            for earlier in sockets[:-1]:
                recv_msg(earlier)

        probe = {"type": MsgType.GAME_START}
        server.broadcast(probe)

        received = [recv_msg(s) for s in sockets]
        for s in sockets:
            s.close()

        assert all(r == probe for r in received)

    def test_send_to_delivers_to_named_player_only(self, server: GameServer) -> None:
        """Deliver a targeted message only to the named player."""
        alice = _connect(server.port)
        _join(alice, server.session_code, "Alice")

        bob = _connect(server.port)
        _join(bob, server.session_code, "Bob")
        recv_msg(alice)  # drain Alice's lobby_update from Bob's join

        server.send_to("Bob", {"type": MsgType.ERROR, "message": "for Bob only"})

        response = recv_msg(bob)

        # Check Alice gets nothing BEFORE closing Bob — closing Bob would trigger
        # a disconnect broadcast to Alice which would make the check spuriously pass.
        alice.settimeout(0.2)
        with pytest.raises((TimeoutError, OSError)):
            recv_msg(alice)

        alice.close()
        bob.close()

        assert response["message"] == "for Bob only"

    def test_send_to_nonexistent_player_is_noop(self, server: GameServer) -> None:
        """Treat send_to for an unknown player as a silent no-op."""
        server.send_to("Nobody", {"type": MsgType.ERROR, "message": "unreachable"})
