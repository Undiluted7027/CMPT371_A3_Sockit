"""server.py — TCP + UDP game server for BrainZap Trivia.

CMPT 371 A3: BrainZap - Trivia Game
Architecture: Client-Server, TCP + UDP hybrid

GameServer manages one BrainZap session end-to-end:
- Generates a session code on startup
- Accepts TCP connections and validates JOIN requests (session code,
  name uniqueness, player cap)
- Maintains lobby state (_joined dict) and broadcasts LOBBY_UPDATE on changes
- Dispatches ANSWER messages into an answer queue consumed by GameLoop
- Sends PLAYER_DISCONNECTED to remaining players when a client drops
- Runs a UDP socket on the same port for timer ticks, answer counts, ping/pong
- Exposes send_to() for personalised per-player messages and
  broadcast_timer_tick() / broadcast_answer_count() for game-loop use

Threading model
---------------
  Caller thread     : runs GameLoop (blocking) after start()
  Acceptor thread   : accept() loop; hands each socket to a client thread
  Per-client thread : reads one client's messages for its entire lifetime
  UDP recv thread   : handles incoming datagrams (pings, registration)
  UDP send thread   : drains the UdpSendQueue and calls sendto()

Lock discipline
---------------
  _joined_lock guards all reads/writes to _joined.
  Never hold _joined_lock while doing I/O — take a snapshot first, release the
  lock, then iterate and send. This prevents a slow or dead socket from stalling
  all other threads.
  Each _ClientConn has its own send_lock so concurrent writes to the same socket
  (game loop + disconnect handler) are serialised without a global bottleneck.
"""

import contextlib
import logging
import queue
import random
import socket
import string
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .protocol import MsgType, UdpSendQueue, recv_msg, recv_udp, send_msg, send_udp

if TYPE_CHECKING:
    import queue as _queue

logger = logging.getLogger(__name__)

MAX_PLAYERS = 10
DEFAULT_PORT = 5001


# ---------------------------------------------------------------------------
# Internal data structures
# ---------------------------------------------------------------------------


@dataclass
class _ClientConn:
    """Per-connection state held for one TCP client."""

    sock: socket.socket
    addr: tuple
    send_lock: threading.Lock = field(default_factory=threading.Lock)
    display_name: str | None = None  # None until a valid join is received


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class GameServer:
    """TCP server managing one BrainZap game session.

    Typical lifecycle::

        server = GameServer()
        server.start()
        print(f"Session code: {server.session_code}")
        # ... caller runs GameLoop here ...
        server.stop()
    """

    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_PORT) -> None:
        """Instantiate a GameServer object to control TCP server managing on BrainZap game session."""
        self.host = host
        self.port = port
        self.session_code: str = _generate_session_code()

        self._tcp_sock: socket.socket | None = None
        self._running = False
        self._udp_sock: socket.socket | None = None
        self._udp_running = False
        self.udp_port = port

        # All successfully joined clients, keyed by display name.
        self._joined: dict[str, _ClientConn] = {}
        self._joined_lock = threading.Lock()

        # Receives (display_name, question_index, choice, receive_time) tuples
        # while a question is active; None between questions.
        self._answer_queue: _queue.Queue[tuple[str, int, int, float]] | None = None

        # UDP state
        self._udp_send_queue = UdpSendQueue()
        self._udp_addrs: dict[str, tuple[str, int]] = {}
        # Maps display_name -> (ping_timestamp, server_receive_time) from the
        # most recent valid ping; used by a future latency-compensation task.
        self._udp_ping_meta: dict[str, tuple[float, float]] = {}
        self._udp_state_lock = threading.Lock()
        self._udp_recv_thread: threading.Thread | None = None
        self._udp_send_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Bind the TCP socket and start the acceptor thread.

        Returns immediately — the acceptor runs as a daemon thread so it does
        not prevent the process from exiting when the main thread finishes.
        """
        self._tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._tcp_sock.bind((self.host, self.port))
        # Update self.port to the actual bound port — important when port=0 is
        # passed (OS assigns an ephemeral port).
        self.port = self._tcp_sock.getsockname()[1]
        self.udp_port = self.port
        self._tcp_sock.listen(MAX_PLAYERS)
        self._running = True

        self._udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._udp_sock.bind((self.host, self.port))
        self._udp_sock.settimeout(0.2)
        self._udp_running = True

        logger.info(
            "Server listening on %s:%d  session_code=%s",
            self.host,
            self.port,
            self.session_code,
        )

        t = threading.Thread(target=self._accept_loop, daemon=True, name="tcp-acceptor")
        t.start()
        self._udp_recv_thread = threading.Thread(
            target=self._udp_recv_loop, daemon=True, name="udp-recv"
        )
        self._udp_send_thread = threading.Thread(
            target=self._udp_send_loop, daemon=True, name="udp-send"
        )
        self._udp_recv_thread.start()
        self._udp_send_thread.start()

    def stop(self) -> None:
        """Signal the server to stop and close the listening socket.

        In-flight client threads will finish naturally when their sockets close.
        """
        self._running = False
        self._udp_running = False
        if self._tcp_sock is not None:
            self._tcp_sock.close()
            self._tcp_sock = None
        if self._udp_sock is not None:
            self._udp_sock.close()
            self._udp_sock = None
        if self._udp_recv_thread is not None:
            self._udp_recv_thread.join(timeout=1.0)
        if self._udp_send_thread is not None:
            self._udp_send_thread.join(timeout=1.0)
        logger.info("Server stopped")

    # ------------------------------------------------------------------
    # Acceptor thread
    # ------------------------------------------------------------------

    def _accept_loop(self) -> None:
        """Accept incoming TCP connections and spawn a per-client thread for each."""
        assert self._tcp_sock is not None
        while self._running:
            try:
                conn, addr = self._tcp_sock.accept()
            except OSError:
                # Raised when stop() closes _tcp_sock — normal shutdown path.
                break
            logger.debug("New TCP connection from %s", addr)
            client = _ClientConn(sock=conn, addr=addr)
            t = threading.Thread(
                target=self._client_thread,
                args=(client,),
                daemon=True,
                name=f"client-{addr}",
            )
            t.start()

    # ------------------------------------------------------------------
    # Per-client thread
    # ------------------------------------------------------------------

    def _client_thread(self, client: _ClientConn) -> None:
        """Read and dispatch messages from one client for its entire lifetime.

        The finally block guarantees cleanup even if an exception slips through.
        """
        try:
            while True:
                try:
                    msg = recv_msg(client.sock)
                except ConnectionError:
                    logger.debug("Client %s disconnected", client.addr)
                    break
                except ValueError:
                    logger.warning(
                        "Malformed message from %s — closing connection", client.addr
                    )
                    break
                self._dispatch(client, msg)
        finally:
            self._remove_client(client)

    def _dispatch(self, client: _ClientConn, msg: dict) -> None:
        """Route an incoming message to the correct handler."""
        t = msg.get("type")
        if t == MsgType.JOIN:
            self._handle_join(client, msg)
        elif t == MsgType.ANSWER:
            self._handle_answer(client, msg)
        else:
            logger.debug("Unhandled message type %r from %s", t, client.addr)

    # ------------------------------------------------------------------
    # Join / leave
    # ------------------------------------------------------------------

    def _handle_join(self, client: _ClientConn, msg: dict) -> None:
        """Validate a join request and, if accepted, register the client.

        Validation (in order):
          1. session_code must match
          2. display_name must be non-empty after stripping whitespace
          3. lobby must not be full (MAX_PLAYERS)
          4. display_name must be unique among joined players

        On failure: send error, leave connection open so client can retry.
        On success: register client, broadcast lobby_update to everyone.
        """
        code: str = msg.get("session_code", "")
        name: str = str(msg.get("display_name", "")).strip()

        if code != self.session_code:
            self._send_error(client, "Invalid session code")
            return

        if not name:
            self._send_error(client, "Display name cannot be empty")
            return

        # Check capacity and uniqueness atomically under the lock so two
        # simultaneous joins with the same name cannot both pass.
        with self._joined_lock:
            if len(self._joined) >= MAX_PLAYERS:
                self._send_error(client, "Game is full")
                return

            if name in self._joined:
                self._send_error(client, f"Name '{name}' is already taken")
                return

            client.display_name = name
            self._joined[name] = client

        logger.info(
            "Player %r joined from %s  (%d/%d in lobby)",
            name,
            client.addr,
            len(self._joined),
            MAX_PLAYERS,
        )
        self._broadcast_lobby_update()

    def _remove_client(self, client: _ClientConn) -> None:
        """Remove client from lobby and close socket.

        Called from the per-client thread's finally block, so it always runs
        regardless of how the thread exits. If the client never joined (failed
        validation and then disconnected) we just close the socket quietly.
        """
        with contextlib.suppress(OSError):
            client.sock.close()

        if client.display_name is None:
            # Never made it into the lobby — nothing to broadcast.
            return

        with self._joined_lock:
            self._joined.pop(client.display_name, None)

        logger.info(
            "Player %r left  (%d remaining)", client.display_name, len(self._joined)
        )
        self._clear_udp_registration(client.display_name)
        self._broadcast_lobby_update()
        self.broadcast(
            {"type": MsgType.PLAYER_DISCONNECTED, "display_name": client.display_name}
        )

    def _handle_answer(self, client: _ClientConn, msg: dict) -> None:
        """Enqueue a player's answer with a server-stamped receive time.

        The receive time is recorded immediately via ``time.monotonic()`` so the
        game loop can compute answer_elapsed = receive_time - question_start_time
        without client-clock trust. Answers are silently dropped when no question
        is active (``_answer_queue`` is None) or the client has not joined.
        """
        if self._answer_queue is not None and client.display_name is not None:
            self._answer_queue.put(
                (
                    client.display_name,
                    int(msg.get("question_index", -1)),
                    int(msg.get("choice", -1)),
                    time.monotonic(),
                )
            )

    # ------------------------------------------------------------------
    # UDP
    # ------------------------------------------------------------------

    def _udp_recv_loop(self) -> None:
        """Receive UDP datagrams and update registration state from pings."""
        assert self._udp_sock is not None
        while self._udp_running:
            try:
                msg, addr = recv_udp(self._udp_sock)
            except TimeoutError:
                continue
            except OSError:
                if self._udp_running:
                    logger.debug("UDP receive loop exiting after socket error")
                break
            except ValueError:
                logger.debug("Ignoring malformed UDP datagram")
                continue

            if msg.get("type") == MsgType.PING:
                self._handle_ping(msg, addr)
            else:
                logger.debug("Ignoring unknown UDP message type %r", msg.get("type"))

    def _udp_send_loop(self) -> None:
        """Send queued UDP datagrams until the server stops."""
        while self._udp_running:
            try:
                msg, addr = self._udp_send_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            sock = self._udp_sock
            if sock is None:
                break
            with contextlib.suppress(OSError):
                send_udp(sock, msg, addr)

    def _handle_ping(self, msg: dict, addr: tuple[str, int]) -> None:
        """Validate a ping, record UDP registration, and enqueue a pong."""
        code = msg.get("session_code")
        name = msg.get("display_name")
        timestamp = msg.get("timestamp")

        if code != self.session_code or not isinstance(name, str):
            return
        if not isinstance(timestamp, (int, float)):
            return

        server_receive_time = time.monotonic()
        with self._joined_lock:
            if name not in self._joined:
                return
            with self._udp_state_lock:
                self._udp_addrs[name] = (str(addr[0]), int(addr[1]))
                self._udp_ping_meta[name] = (float(timestamp), server_receive_time)

        self._udp_send_queue.put(
            {
                "type": MsgType.PONG,
                "timestamp": float(timestamp),
                "server_time": server_receive_time,
            },
            addr,
        )

    def _clear_udp_registration(self, display_name: str) -> None:
        """Remove all UDP state associated with one player."""
        with self._udp_state_lock:
            self._udp_addrs.pop(display_name, None)
            self._udp_ping_meta.pop(display_name, None)

    # ------------------------------------------------------------------
    # Broadcast helpers
    # ------------------------------------------------------------------

    def broadcast(self, msg: dict) -> None:
        """Send msg to every joined client.

        Takes a snapshot of _joined before releasing the lock so I/O happens
        outside the critical section. Dead sockets raise OSError which is
        silently dropped here — the per-client thread will call _remove_client
        when it detects the disconnect on its next recv.
        """
        with self._joined_lock:
            snapshot = list(self._joined.values())

        for client in snapshot:
            with contextlib.suppress(OSError):
                send_msg(client.sock, msg, lock=client.send_lock)

    def _broadcast_lobby_update(self) -> None:
        """Build and broadcast the current lobby state to all joined clients."""
        with self._joined_lock:
            players = list(self._joined.keys())

        # host_started_countdown and countdown_remaining are always False/None:
        # the auto-start countdown is driven by GameLoop, not the server directly.
        self.broadcast(
            {
                "type": MsgType.LOBBY_UPDATE,
                "players": players,
                "host_started_countdown": False,
                "countdown_remaining": None,
            }
        )

    def _send_error(self, client: _ClientConn, message: str) -> None:
        """Send an error message to one client. Swallows OSError on broken socket."""
        with contextlib.suppress(OSError):
            send_msg(
                client.sock,
                {"type": MsgType.ERROR, "message": message},
                lock=client.send_lock,
            )

    # ------------------------------------------------------------------
    # Accessors for the game loop
    # ------------------------------------------------------------------

    def get_players(self) -> list[str]:
        """Return a snapshot of currently joined display names."""
        with self._joined_lock:
            return list(self._joined.keys())

    def send_to(self, display_name: str, msg: dict) -> None:
        """Send a message to one specific player by display name.

        No-ops silently if the player is no longer in the lobby.
        Used by the game loop to send personalised question_result messages.
        """
        with self._joined_lock:
            client = self._joined.get(display_name)
        if client is not None:
            with contextlib.suppress(OSError):
                send_msg(client.sock, msg, lock=client.send_lock)

    def set_answer_queue(
        self, q: "_queue.Queue[tuple[str, int, int, float]] | None"
    ) -> None:
        """Activate or deactivate answer collection for the current question.

        Called by GameLoop at question start (pass a fresh Queue) and after the
        collection window closes (pass None). While None, incoming ANSWER messages
        are silently dropped.
        """
        self._answer_queue = q

    def broadcast_timer_tick(self, question_index: int, remaining: float) -> None:
        """Queue a timer tick datagram for every registered UDP client."""
        with self._udp_state_lock:
            addrs = list(self._udp_addrs.values())
        for addr in addrs:
            self._udp_send_queue.put(
                {
                    "type": MsgType.TIMER_TICK,
                    "question_index": question_index,
                    "remaining": remaining,
                },
                addr,
            )

    def broadcast_answer_count(
        self, question_index: int, answered: int, total: int
    ) -> None:
        """Queue an answer count datagram for every registered UDP client."""
        with self._udp_state_lock:
            addrs = list(self._udp_addrs.values())
        for addr in addrs:
            self._udp_send_queue.put(
                {
                    "type": MsgType.ANSWER_COUNT,
                    "question_index": question_index,
                    "answered": answered,
                    "total": total,
                },
                addr,
            )

    def get_udp_ping_meta(self, display_name: str) -> tuple[float, float] | None:
        """Return the most recent raw ping metadata for one player."""
        with self._udp_state_lock:
            return self._udp_ping_meta.get(display_name)

    def get_client_rtt(self, display_name: str) -> float | None:
        """Return the latest client RTT estimate, if available.

        Raw ping metadata is stored in _udp_ping_meta but RTT calculation and
        latency-compensated scoring are not implemented; this always returns None.
        """
        del display_name
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_session_code(length: int = 4) -> str:
    """Return a random uppercase alphanumeric session code (e.g. 'A3BX')."""
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choices(alphabet, k=length))


# ---------------------------------------------------------------------------
# Quick-start entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )

    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    server = GameServer(port=port)
    server.start()

    print(f"\n  Session code : {server.session_code}")
    print(f"  Listening on : 0.0.0.0:{port}")
    print("  Press Ctrl+C to stop\n")

    try:
        threading.Event().wait()  # block main thread until Ctrl+C
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.stop()
