"""Handles TCP networking for the player client."""

import contextlib
import logging
import socket
import threading
from collections.abc import Callable
from typing import Any

from .protocol import MsgType, recv_msg, send_msg

logger = logging.getLogger(__name__)


class ClientTCP:
    """TCP client for connecting to the BrainZap trivia server.

    Handles connecting, sending join/answer messages, and dispatching all
    incoming server messages to typed on_* handlers.

    Usage::

        client = ClientTCP("192.168.1.10", 5000)
        client.connect()
        client.join("ABCD", "Alice")
        # Lobby/game updates arrive via on_* handlers (override to wire to GUI)
        client.disconnect()

    All on_* methods are called from the background TCP receiver thread — use
    widget.after(0, fn) before touching any Tkinter widget.
    """

    def __init__(
        self,
        server_ip: str,
        server_port: int,
        on_message: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """Initialize the TCP client.

        Args:
            server_ip:   Server IP address or hostname.
            server_port: Server TCP port (default 5000).
            on_message:  Optional fallback callback invoked for any message
                         type not handled by a typed on_* method.  Kept for
                         backward compatibility and testing convenience.

        """
        self.server_ip = server_ip
        self.server_port = server_port
        self._on_message_fallback = on_message
        self.sock: socket.socket | None = None
        self.listener_thread: threading.Thread | None = None
        self.running = False

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Open a TCP connection and start the background receiver thread.

        Raises:
            ConnectionError: if the connection is refused or times out.

        """
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.sock.connect((self.server_ip, self.server_port))
        except Exception as e:
            raise ConnectionError(f"Failed to connect to server: {e}") from e

        self.running = True
        self.listener_thread = threading.Thread(
            target=self.listen, daemon=True, name="clientTCP-recv"
        )
        self.listener_thread.start()

    def disconnect(self) -> None:
        """Close the TCP connection and stop the receiver thread."""
        self.running = False
        sock, self.sock = self.sock, None  # snapshot and clear atomically
        if sock is not None:
            with contextlib.suppress(Exception):
                sock.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(Exception):
                sock.close()

    # ------------------------------------------------------------------
    # Outbound messages
    # ------------------------------------------------------------------

    def join(self, session_code: str, display_name: str) -> None:
        """Send a join request to the server.

        The server responds asynchronously — on_lobby_update fires on success,
        on_error fires on failure (bad code, duplicate name, full lobby).

        Args:
            session_code:  The 4-character room code shown on the host screen.
            display_name:  The player's chosen name (must be unique in lobby).

        """
        self.send_message(
            {
                "type": MsgType.JOIN,
                "session_code": session_code,
                "display_name": display_name,
            }
        )

    def send_answer(self, question_index: int, choice: int) -> None:
        """Submit the player's answer for the current question.

        No-op if not connected (safe to call without a guard).

        Args:
            question_index: 0-based index of the current question.
            choice:         0-based index into the question's options array.

        """
        if not self.running or self.sock is None:
            return
        self.send_message(
            {
                "type": MsgType.ANSWER,
                "question_index": question_index,
                "choice": choice,
            }
        )

    def send_message(self, message: dict[str, Any]) -> None:
        """Send any dict as a length-prefixed JSON message to the server.

        Args:
            message: Arbitrary dict; will be JSON-serialised and framed.

        Raises:
            ConnectionError: if the socket is not open or the send fails.

        """
        if self.sock is None:
            raise ConnectionError("Socket is not connected")
        try:
            send_msg(self.sock, message)
        except OSError as e:
            raise ConnectionError(f"Failed to send message: {e}") from e

    # ------------------------------------------------------------------
    # Receive loop (background thread)
    # ------------------------------------------------------------------

    def listen(self) -> None:
        """Read messages from the server and dispatch them until disconnected.

        Runs on the background TCP receiver thread. A ConnectionError means
        the server closed the connection; ValueError means a malformed frame
        (log and skip — one bad frame is not fatal).
        """
        assert self.sock is not None
        try:
            while self.running:
                try:
                    msg = recv_msg(self.sock)
                except ConnectionError:
                    self.on_disconnect()
                    return
                except OSError:
                    # Socket was closed by disconnect() from the main thread.
                    return
                except ValueError:
                    logger.warning("Received malformed message from server — skipping")
                    continue
                self._dispatch(msg)
        finally:
            self.disconnect()

    def _dispatch(self, msg: dict[str, Any]) -> None:
        """Route a received message to the correct on_* handler."""
        t = msg.get("type")

        if t == MsgType.LOBBY_UPDATE:
            self.on_lobby_update(
                players=msg["players"],
                host_started_countdown=msg["host_started_countdown"],
                countdown_remaining=msg["countdown_remaining"],
            )
        elif t == MsgType.GAME_START:
            self.on_game_start()
        elif t == MsgType.QUESTION:
            self.on_question(msg)
        elif t == MsgType.QUESTION_RESULT:
            self.on_question_result(msg)
        elif t == MsgType.GAME_OVER:
            self.on_game_over(msg)
        elif t == MsgType.PLAYER_DISCONNECTED:
            self.on_player_disconnected(display_name=msg["display_name"])
        elif t == MsgType.ERROR:
            self.on_error(message=msg["message"])
        elif self._on_message_fallback is not None:
            # Unknown/untyped message — call the generic fallback if provided.
            self._on_message_fallback(msg)
        else:
            logger.debug("Unknown message type: %r", t)

    # ------------------------------------------------------------------
    # Event handlers — override to connect to the GUI
    # All methods run on the TCP receiver thread.
    # Never update Tkinter widgets directly — schedule via widget.after(0, fn).
    # ------------------------------------------------------------------

    def on_lobby_update(
        self,
        players: list[str],
        host_started_countdown: bool,
        countdown_remaining: float | None,
    ) -> None:
        """Server sent an updated lobby state."""

    def on_game_start(self) -> None:
        """Server signalled that the game is starting."""

    def on_question(self, msg: dict[str, Any]) -> None:
        """Server delivered a new question."""

    def on_question_result(self, msg: dict[str, Any]) -> None:
        """Server sent post-question results."""

    def on_game_over(self, msg: dict[str, Any]) -> None:
        """Server sent the final game-over summary."""

    def on_player_disconnected(self, display_name: str) -> None:
        """Another player disconnected during an active game."""

    def on_error(self, message: str) -> None:
        """Server rejected the last request. Connection stays open for retry."""

    def on_disconnect(self) -> None:
        """Server closed the connection unexpectedly."""
