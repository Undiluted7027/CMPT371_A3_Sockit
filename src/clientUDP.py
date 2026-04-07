"""Handles UDP networking for the player client."""

import logging
import socket
import threading
import time
from collections.abc import Callable
from typing import Any

from .protocol import MsgType, recv_udp, send_udp

logger = logging.getLogger(__name__)


class ClientUDP:
    """UDP client for receiving timer ticks/answer counts and sending pings.

    Usage::

        client = ClientUDP("192.168.1.10", 5001)
        client.start()
        # Timer ticks arrive via on_timer_tick(), answer counts via on_answer_count()
        client.stop()

    Override the on_* methods to wire events to the GUI.  All on_* methods are
    called from the background UDP listener thread — use widget.after(0, fn)
    before touching any Tkinter widget.
    """

    def __init__(
        self,
        server_ip: str,
        server_port: int,
        on_message: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """Initialize the UDP client.

        Args:
            server_ip:   Server IP address.
            server_port: Server UDP port.
            on_message:  Optional fallback callback for unhandled message types.

        """
        self.server_ip = server_ip
        self.server_port = server_port
        self._on_message_fallback = on_message
        self.sock: socket.socket | None = None
        self.listener_thread: threading.Thread | None = None
        self.running: bool = False
        self.seq_num: int = 0  # sequence number for outgoing messages

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Bind the UDP socket and start the background listener thread."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.running = True
        self.listener_thread = threading.Thread(
            target=self.listen, daemon=True, name="clientUDP-recv"
        )
        self.listener_thread.start()

    def stop(self) -> None:
        """Stop the listener thread and close the UDP socket."""
        self.running = False
        if self.sock:
            self.sock.close()
            self.sock = None

    # ------------------------------------------------------------------
    # Outbound messages
    # ------------------------------------------------------------------

    def send_message(self, message: dict[str, Any]) -> None:
        """Stamp a sequence number onto message and send it as a UDP datagram.

        Args:
            message: Dict to send; a ``"seq"`` field is added automatically.

        Raises:
            ConnectionError: if the socket is not open or the send fails.

        """
        if self.sock is None:
            raise ConnectionError("UDP socket not initialized")

        message_with_seq = message.copy()
        message_with_seq["seq"] = self.seq_num
        self.seq_num += 1

        try:
            send_udp(self.sock, message_with_seq, (self.server_ip, self.server_port))
        except OSError as e:
            raise ConnectionError(f"Failed to send UDP message: {e}") from e

    def send_ping(self) -> None:
        """Send a PING datagram to the server for RTT / latency measurement."""
        self.send_message({"type": MsgType.PING, "timestamp": time.monotonic()})

    # ------------------------------------------------------------------
    # Receive loop (background thread)
    # ------------------------------------------------------------------

    def listen(self) -> None:
        """Receive UDP datagrams and dispatch them until stopped."""
        if self.sock is None:
            return

        try:
            while self.running:
                try:
                    message, _ = recv_udp(self.sock)
                    self._dispatch(message)
                except OSError:
                    break  # socket closed — exit cleanly
                except Exception:
                    continue  # transient decode error — skip and keep going
        finally:
            self.stop()

    def _dispatch(self, msg: dict[str, Any]) -> None:
        """Route a received datagram to the correct on_* handler."""
        t = msg.get("type")

        if t == MsgType.TIMER_TICK:
            self.on_timer_tick(msg)
        elif t == MsgType.ANSWER_COUNT:
            self.on_answer_count(msg)
        elif t == MsgType.PONG:
            self.on_pong(msg)
        elif self._on_message_fallback is not None:
            self._on_message_fallback(msg)
        else:
            logger.debug("Unknown UDP message type: %r", t)

    # ------------------------------------------------------------------
    # Event handlers — override to connect to the GUI
    # All methods run on the UDP listener thread.
    # Never update Tkinter widgets directly — schedule via widget.after(0, fn).
    # ------------------------------------------------------------------

    def on_timer_tick(self, msg: dict[str, Any]) -> None:
        """Server broadcast a timer tick for the current question.

        msg keys: ``"question_index"`` (int), ``"remaining"`` (float seconds).
        """

    def on_answer_count(self, msg: dict[str, Any]) -> None:
        """Server broadcast a live answer-count update.

        msg keys: ``"question_index"`` (int), ``"answered"`` (int), ``"total"`` (int).
        """

    def on_pong(self, msg: dict[str, Any]) -> None:
        """Server replied to a ping — use for RTT calculation.

        msg keys: ``"timestamp"`` (echo of our ping timestamp),
        ``"server_time"`` (float, server's monotonic time at receipt).
        """
