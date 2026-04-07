"""Test for ClientTCP using a simulated (temp) TCP server."""

import contextlib
import socket
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from src.clientTCP import ClientTCP
from src.protocol import MsgType, recv_msg


# ------------------------
# Helper: Simulated TCP Server (temp for testing)
# ------------------------
class MockServer:
    """A simple TCP server that echoes back received messages."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        """Instantiate a TCP server that echoes received messages."""
        self.host = host
        self.port = port
        self.sock: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.listen(10)
        self.port = self.sock.getsockname()[1]  # Get assigned port if port = 0
        self.client_conn: socket.socket | None = None
        self.running: bool = False
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        """Start server thread."""
        self.running = True
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        """Accept connections and echo messages; one daemon thread per client."""
        while self.running:
            try:
                conn, _ = self.sock.accept()
            except Exception:
                break
            self.client_conn = conn
            t = threading.Thread(target=self._handle_client, args=(conn,), daemon=True)
            t.start()

    def _handle_client(self, conn: socket.socket) -> None:
        """Echo all framed messages back to one connected client."""
        while self.running:
            try:
                header = self._recv_exact_conn(conn, 4)
                if header is None:
                    break
                length = int.from_bytes(header, "big")
                data = self._recv_exact_conn(conn, length)
                if data is None:
                    break
                conn.sendall(header + data)
            except Exception:
                break
        with contextlib.suppress(Exception):
            conn.close()

    def _recv_exact_conn(self, conn: socket.socket, n: int) -> bytes | None:
        """Receive exactly n bytes from the given connection."""
        buf = b""
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    def _recv_exact(self, n: int) -> bytes | None:
        """Receive exactly n bytes from the last accepted client connection."""
        assert self.client_conn is not None
        return self._recv_exact_conn(self.client_conn, n)

    def stop(self) -> None:
        """Stop server and close sockets."""
        self.running = False
        if self.client_conn:
            with contextlib.suppress(Exception):
                self.client_conn.shutdown(socket.SHUT_RDWR)
            self.client_conn.close()
        self.sock.close()


# ------------------------
# Tests for ClientTCP
# ------------------------
def test_client_tcp_echo() -> None:
    """Test that ClientTCP can connect and echo messages with mock server."""
    messages_received: list[dict[str, Any]] = []

    def on_message(msg: dict[str, Any]) -> None:
        messages_received.append(msg)

    server = MockServer()
    server.start()
    time.sleep(0.1)  # Give server a moment to start

    client = ClientTCP("127.0.0.1", server.port, on_message)
    client.connect()
    time.sleep(0.1)

    # Send a message and wait for echo
    test_msg: dict[str, Any] = {"action": "ping"}
    client.send_message(test_msg)
    time.sleep(0.1)

    client.disconnect()
    server.stop()

    assert len(messages_received) == 1
    assert messages_received[0] == test_msg


def test_client_tcp_disconnect_no_server() -> None:
    """Test that ClientTCP handles disconnect gracefully if server closes."""
    messages_received: list[dict[str, Any]] = []

    def on_message(msg: dict[str, Any]) -> None:
        messages_received.append(msg)

    # Start and stop server immediately
    server = MockServer()
    server.start()
    server.stop()
    time.sleep(0.1)

    client = ClientTCP("127.0.0.1", server.port, on_message)
    with pytest.raises(ConnectionError):
        client.connect()


def test_multiple_clients_echo() -> None:
    """Test multiple ClientTCP instances sending messages concurrently."""
    num_clients = 3
    received_lists: list[list[dict[str, Any]]] = [[] for _ in range(num_clients)]

    def make_callback(idx: int) -> Callable[[dict[str, Any]], None]:
        def cb(msg: dict[str, Any]) -> None:
            received_lists[idx].append(msg)

        return cb

    callbacks: list[Callable[[dict[str, Any]], None]] = [
        make_callback(i) for i in range(num_clients)
    ]

    server = MockServer()
    server.start()
    time.sleep(0.1)

    clients: list[ClientTCP] = []
    for i in range(num_clients):
        client = ClientTCP("127.0.0.1", server.port, on_message=callbacks[i])
        client.connect()
        clients.append(client)
        time.sleep(0.05)  # stagger connects slightly

    # Send a message from each client
    for i, client in enumerate(clients):
        client.send_message({"msg": f"hello {i}"})

    time.sleep(1)  # wait for echo

    # Disconnect all
    for client in clients:
        client.disconnect()
    server.stop()

    # Assert each client received its own message back
    for i, rec in enumerate(received_lists):
        assert len(rec) == 1
        assert rec[0]["msg"] == f"hello {i}"


# ------------------------
# Tests for new join / send_answer / dispatch API
# ------------------------


def _make_loopback_pair() -> tuple[socket.socket, socket.socket]:
    """Return a connected (client_sock, server_sock) pair using socketpair."""
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    return a, b


def test_join_sends_correct_fields() -> None:
    """join() sends a JOIN message with session_code and display_name."""
    client_sock, server_sock = _make_loopback_pair()
    client = ClientTCP("127.0.0.1", 0)
    client.sock = client_sock
    client.running = True

    client.join("ABCD", "Alice")

    msg = recv_msg(server_sock)
    client_sock.close()
    server_sock.close()

    assert msg["type"] == MsgType.JOIN
    assert msg["session_code"] == "ABCD"
    assert msg["display_name"] == "Alice"


def test_send_answer_sends_correct_fields() -> None:
    """send_answer() sends an ANSWER message with question_index and choice."""
    client_sock, server_sock = _make_loopback_pair()
    client = ClientTCP("127.0.0.1", 0)
    client.sock = client_sock
    client.running = True

    client.send_answer(question_index=3, choice=1)

    msg = recv_msg(server_sock)
    client_sock.close()
    server_sock.close()

    assert msg["type"] == MsgType.ANSWER
    assert msg["question_index"] == 3
    assert msg["choice"] == 1


def test_send_answer_noop_when_disconnected() -> None:
    """send_answer() does nothing when the client is not connected."""
    client = ClientTCP("127.0.0.1", 0)
    # running=False, sock=None — should not raise
    client.send_answer(question_index=0, choice=0)


def test_dispatch_routes_lobby_update() -> None:
    """_dispatch() calls on_lobby_update for LOBBY_UPDATE messages."""
    received: list[tuple] = []

    class _Client(ClientTCP):
        def on_lobby_update(
            self,
            players: list[str],
            host_started_countdown: bool,
            countdown_remaining: float | None,
        ) -> None:  # type: ignore[override]
            received.append((players, host_started_countdown, countdown_remaining))

    client = _Client("127.0.0.1", 0)
    client._dispatch(
        {
            "type": MsgType.LOBBY_UPDATE,
            "players": ["Alice", "Bob"],
            "host_started_countdown": False,
            "countdown_remaining": None,
        }
    )

    assert len(received) == 1
    assert received[0] == (["Alice", "Bob"], False, None)


def test_dispatch_routes_error() -> None:
    """_dispatch() calls on_error for ERROR messages."""
    errors: list[str] = []

    class _Client(ClientTCP):
        def on_error(self, message: str) -> None:
            errors.append(message)

    client = _Client("127.0.0.1", 0)
    client._dispatch(
        {"type": MsgType.ERROR, "message": "Name 'Alice' is already taken"}
    )

    assert errors == ["Name 'Alice' is already taken"]


def test_dispatch_unknown_type_calls_fallback() -> None:
    """_dispatch() calls on_message fallback for unknown message types."""
    received: list[dict] = []
    client = ClientTCP("127.0.0.1", 0, on_message=received.append)
    client._dispatch({"action": "ping"})  # no "type" key

    assert len(received) == 1
    assert received[0] == {"action": "ping"}


def test_on_disconnect_called_on_server_close() -> None:
    """on_disconnect fires when the server closes the connection."""
    disconnected: list[bool] = []
    event = threading.Event()

    class _Client(ClientTCP):
        def on_disconnect(self) -> None:
            disconnected.append(True)
            event.set()

    # Minimal server: accept one connection then immediately close it.
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def _close_immediately() -> None:
        conn, _ = srv.accept()
        conn.close()
        srv.close()

    threading.Thread(target=_close_immediately, daemon=True).start()

    client = _Client("127.0.0.1", port)
    client.connect()
    assert event.wait(timeout=2.0), "on_disconnect was not called"
    assert disconnected == [True]
