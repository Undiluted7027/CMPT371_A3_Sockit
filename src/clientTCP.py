"""Handles TCP networking for the player client."""

import json
import socket
import struct
import threading
from collections.abc import Callable
from typing import Any


class ClientTCP:
    """TCP client for connecting to the trivia server.

    Handles sending join requests, answers, and receiving server messages.
    """

    def __init__(
        self,
        server_ip: str,
        server_port: int,
        on_message: Callable[[dict[str, Any]], None],
    ) -> None:
        """Initialize the client.

        Args:
            server_ip: Server IP address.
            server_port: Server TCP port.
            on_message: Callback function called on each received message.

        """
        self.server_ip = server_ip
        self.server_port = server_port
        self.on_message = on_message
        self.sock: socket.socket | None = None
        self.listener_thread: threading.Thread | None = None
        self.running = False

    def connect(self) -> None:
        """Connect to the server and start the listener thread."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.sock.connect((self.server_ip, self.server_port))
        except Exception as e:
            raise ConnectionError(f"Failed to connect to server: {e}") from e

        self.running = True
        self.listener_thread = threading.Thread(target=self.listen, daemon=True)
        self.listener_thread.start()

    def send_message(self, message: dict[str, Any]) -> None:
        """Send a JSON message to the server with 4-byte length prefix."""
        if self.sock is None:
            raise ConnectionError("Socket is not connected")

        payload = json.dumps(message).encode("utf-8")
        length_prefix = struct.pack(">I", len(payload))
        try:
            self.sock.sendall(length_prefix + payload)
        except Exception as e:
            raise ConnectionError(f"Failed to send message: {e}") from e

    def listen(self) -> None:
        """Continuously listen for messages from the server and dispatch them."""
        if self.sock is None:
            return

        try:
            while self.running:
                # Read 4-byte length prefix
                header = self._recv_exact(4)
                if header is None:
                    break

                message_length = struct.unpack(">I", header)[0]
                # Read the JSON payload
                payload_bytes = self._recv_exact(message_length)
                if payload_bytes is None:
                    break

                message: dict[str, Any] = json.loads(payload_bytes.decode("utf-8"))
                self.on_message(message)
        except Exception as e:
            print(f"Listener stopped due to error: {e}")
        finally:
            self.disconnect()

    def _recv_exact(self, n: int) -> bytes | None:
        """Receive exactly n bytes from the socket, or None if disconnected."""
        assert self.sock is not None
        buf = b""
        while len(buf) < n:
            try:
                chunk = self.sock.recv(n - len(buf))
                if not chunk:
                    return None
                buf += chunk
            except Exception:
                return None
        return buf

    def disconnect(self) -> None:
        """Stop the listener thread and close the socket."""
        self.running = False
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            self.sock.close()
            self.sock = None
