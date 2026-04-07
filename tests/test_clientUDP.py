"""Test for ClientUDP using a simulated (temp) UDP server."""

import json
import socket
import threading
import time
from pathlib import Path
from typing import Any

from src.clientUDP import ClientUDP


class MockUDPServer:
    """Mock UDP server for testing."""

    def __init__(self, host: str, port: int, drop_every: int = 0) -> None:
        """Instantiate a UDP server that echoes received messages."""
        self.host = host
        self.port = port
        self.drop_every = drop_every  # drop every Nth message
        self.received: list[dict] = []
        self.running: bool = False
        self.sock: socket.socket | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the server."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.host, self.port))
        self.running = True
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self) -> None:
        """Run the server, optionally dropping every Nth received message."""
        assert self.sock is not None
        count = 0
        while self.running:
            try:
                data, _ = self.sock.recvfrom(4096)
                msg: dict = json.loads(data.decode("utf-8"))
                count += 1
                if self.drop_every > 0 and count % self.drop_every == 0:
                    continue  # simulate packet drop
                self.received.append(msg)
            except Exception:
                continue

    def stop(self) -> None:
        """Stop the server."""
        self.running = False
        if self.sock:
            self.sock.close()


def test_client_udp_sequence(tmp_path: Path) -> None:
    """Test a client with UDP sequence."""
    host: str = "127.0.0.1"
    port: int = 12345

    # Start mock server
    server: MockUDPServer = MockUDPServer(host, port)
    server.start()

    received_messages: list[dict[str, Any]] = []

    # Callback to collect messages (for completeness)
    def on_message(msg: dict[str, Any]) -> None:
        received_messages.append(msg)

    client: ClientUDP = ClientUDP(host, port, on_message)
    client.start()

    # Send multiple messages
    for i in range(5):
        client.send_message({"type": "tick", "value": i})
        time.sleep(0.05)  # slight delay to allow server to process

    time.sleep(0.2)  # wait for server to receive all messages

    client.stop()
    server.stop()

    # Check sequence numbers
    seqs: list[int] = [msg["seq"] for msg in server.received]
    assert seqs == list(range(5)), f"Sequence numbers mismatch: {seqs}"


def test_client_udp_dropped_packets(tmp_path: Path) -> None:
    """Test that sequence numbers detect dropped UDP packets."""
    host: str = "127.0.0.1"
    port: int = 12346

    # Start mock server that randomly drops packets
    server: MockUDPServer = MockUDPServer(
        host, port, drop_every=2
    )  # drops every 2nd message
    server.start()

    received_messages: list[dict[str, Any]] = []

    def on_message(msg: dict[str, Any]) -> None:
        received_messages.append(msg)

    client: ClientUDP = ClientUDP(host, port, on_message)
    client.start()

    # Send 5 messages
    for i in range(5):
        client.send_message({"type": "tick", "value": i})
        time.sleep(0.05)

    time.sleep(0.2)

    client.stop()
    server.stop()

    # Sequence numbers received by the server
    seqs: list[int] = [msg["seq"] for msg in server.received]

    # We expect dropped packets
    expected_seqs: list[int] = [0, 2, 4]  # 1 and 3 dropped
    assert seqs == expected_seqs, f"Dropped packets not detected: {seqs}"
