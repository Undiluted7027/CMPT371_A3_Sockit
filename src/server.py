"""server.py — TCP game server for Sockit Trivia.

CMPT 371 A3: Sockit - Trivia Game
Architecture: Client-Server, TCP + UDP hybrid
Reference:    reference/protocol-foundation.md
Ownership:    Sanchit (Phase 2: Task 3 TCP skeleton; Phase 4: Task 5 Server UDP + Task 14 Host GUI)
              Praneet (Phase 4: Task 8 game loop — will extend this file)

Phase 2 scope
-------------
- Generate session code on startup
- Accept TCP connections (acceptor thread)
- Spawn a per-client reader thread for each connection
- Validate join requests (session code, display name uniqueness, capacity)
- Maintain lobby state (_joined dict)
- Broadcast lobby_update to all joined clients on any change
- Clean up on disconnect (lobby phase)

Phase 4 will add
----------------
- Game loop (question sequencing, timers, scoring)
- Handling ANSWER messages
- Sending QUESTION, QUESTION_RESULT, GAME_OVER via broadcast()
- Host-triggered game start / auto-start countdown
- game_start message delivery

Threading model
---------------
  Main thread      : starts server; will run game loop in Phase 4
  Acceptor thread  : accept() loop; hands each socket to a client thread (daemon)
  Per-client thread: reads one client's messages for its entire lifetime (daemon)

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
import random
import socket
import string
import threading
from dataclasses import dataclass, field

from src.protocol import MsgType, recv_msg, send_msg

logger = logging.getLogger(__name__)

MAX_PLAYERS = 10
DEFAULT_PORT = 5000


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
    """TCP server managing one Sockit game session.

    Typical lifecycle::

        server = GameServer()
        server.start()
        print(f"Session code: {server.session_code}")
        # ... main thread runs game loop in Phase 4 ...
        server.stop()
    """

    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_PORT) -> None:
        """Instantiate a GameServer object to control TCP server managing on Sockit game session."""
        self.host = host
        self.port = port
        self.session_code: str = _generate_session_code()

        self._tcp_sock: socket.socket | None = None
        self._running = False

        # All successfully joined clients, keyed by display name.
        self._joined: dict[str, _ClientConn] = {}
        self._joined_lock = threading.Lock()

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
        self._tcp_sock.listen(MAX_PLAYERS)
        self._running = True

        logger.info(
            "Server listening on %s:%d  session_code=%s",
            self.host,
            self.port,
            self.session_code,
        )

        t = threading.Thread(target=self._accept_loop, daemon=True, name="tcp-acceptor")
        t.start()

    def stop(self) -> None:
        """Signal the server to stop and close the listening socket.

        In-flight client threads will finish naturally when their sockets close.
        """
        self._running = False
        if self._tcp_sock is not None:
            self._tcp_sock.close()
            self._tcp_sock = None
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
        else:
            # ANSWER and other game-phase messages handled in Phase 4.
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
        self._broadcast_lobby_update()

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

        # Phase 4 will set host_started_countdown / countdown_remaining when the
        # host triggers the auto-start countdown.
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
    # Accessors for the game loop (Phase 4)
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
