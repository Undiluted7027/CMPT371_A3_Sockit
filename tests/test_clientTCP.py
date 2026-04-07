"""Test for ClientTCP using a simulated (temp) TCP server."""

import contextlib
import socket
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from src.clientTCP import ClientTCP


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
