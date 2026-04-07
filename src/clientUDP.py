"""Handles UDP networking for the player client."""

import json
import socket
import threading
from collections.abc import Callable
from typing import Any


class ClientUDP:
    """UDP client for sending/receiving messages to/from the trivia server."""

    def __init__(
        self,
        server_ip: str,
        server_port: int,
        on_message: Callable[[dict[str, Any]], None],
    ) -> None:
        """Initialize the UDP client.

        Args:
            server_ip: Server IP address
            server_port: Server UDP port
            on_message: Callback function for each received message

        """
        self.server_ip = server_ip
        self.server_port = server_port
        self.on_message = on_message
        self.sock: socket.socket | None = None
        self.listener_thread: threading.Thread | None = None
        self.running: bool = False
        self.seq_num: int = 0  # sequence number for outgoing messages

    def start(self) -> None:
        """Start the UDP client and listener thread."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.running = True
        self.listener_thread = threading.Thread(target=self.listen, daemon=True)
        self.listener_thread.start()

    def send_message(self, message: dict[str, Any]) -> None:
        """Send a JSON message to the server.

        Args:
            message: Dictionary to send

        """
        if self.sock is None:
            raise ConnectionError("UDP socket not initialized")

        # Add sequence number
        message_with_seq = message.copy()
        message_with_seq["seq"] = self.seq_num
        self.seq_num += 1

        payload = json.dumps(message).encode("utf-8")
        try:
            self.sock.sendto(payload, (self.server_ip, self.server_port))
        except Exception as e:
            raise ConnectionError(f"Failed to send UDP message: {e}") from e

    def listen(self) -> None:
        """Continuously listen for incoming UDP messages."""
        if self.sock is None:
            return

        try:
            while self.running:
                try:
                    data, _ = self.sock.recvfrom(4096)  # buffer size 4 KB
                    message: dict[str, Any] = json.loads(data.decode("utf-8"))
                    self.on_message(message)
                except Exception:
                    continue
        finally:
            self.stop()

    def stop(self) -> None:
        """Stop listener thread and close the UDP socket."""
        self.running = False
        if self.sock:
            self.sock.close()
            self.sock = None
