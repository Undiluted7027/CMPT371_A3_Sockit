"""protocol.py — Shared message protocol for Sockit Trivia.

This module is the contract between server and client. Every other module imports
from here. Nothing in this file knows about game logic — it only handles message
serialization, framing, and type constants.

TCP framing:
    ┌─────────────────────┬──────────────────────────────────┐
    │  4 bytes (uint32)   │         N bytes (UTF-8 JSON)     │
    │  big-endian length  │         the actual message       │
    └─────────────────────┴──────────────────────────────────┘

UDP:
    Each datagram is a single self-contained JSON message. No framing needed.
"""

import json
import queue
import struct
import threading
from socket import socket
from typing import Any

# ---------------------------------------------------------------------------
# Message type constants
# ---------------------------------------------------------------------------


class MsgType:
    """All valid values for the "type" field in every message.

    Using constants avoids typo bugs — if you mistype MsgType.QUESTON the
    interpreter raises AttributeError immediately; a mistyped string literal
    "queston" silently fails at runtime.
    """

    # TCP: Client → Server
    JOIN = "join"
    ANSWER = "answer"

    # TCP: Server → Client
    LOBBY_UPDATE = "lobby_update"
    GAME_START = "game_start"
    QUESTION = "question"
    QUESTION_RESULT = "question_result"
    GAME_OVER = "game_over"
    PLAYER_DISCONNECTED = "player_disconnected"
    ERROR = "error"

    # UDP: Server → Client
    TIMER_TICK = "timer_tick"
    ANSWER_COUNT = "answer_count"

    # UDP: Client ↔ Server (latency measurement)
    PING = "ping"
    PONG = "pong"


# ---------------------------------------------------------------------------
# TCP helpers
# ---------------------------------------------------------------------------


def _recv_exactly(sock: socket, n: int) -> bytes:
    """Read exactly n bytes from sock, handling partial reads.

    TCP is a byte stream — a single recv() call may return fewer bytes than
    requested. This loop accumulates chunks until we have exactly n bytes.

    Args:
        sock: A connected TCP socket.
        n:    The exact number of bytes to read.

    Returns:
        A bytes object of length n.

    Raises:
        ConnectionError: If the remote end closes the connection before n bytes
                         have been delivered (recv returns empty bytes b"").

    """
    buf: bytes = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            # recv() returns b"" only when the connection is closed.
            raise ConnectionError("Connection closed by remote end")
        buf += chunk
    return buf


def send_msg(sock: socket, msg: dict, lock: threading.Lock | None = None) -> None:
    """Serialize msg to JSON, prepend a 4-byte big-endian length, send over TCP.

    The frame format is:
        [uint32 big-endian payload length] [UTF-8 JSON payload]

    Multiple threads may write to the same client socket concurrently (e.g. the
    server game loop broadcasts a question at the same time a disconnect handler
    sends an error). Concurrent writes corrupt the stream. Pass the per-client
    send lock so writes are serialized.

    Args:
        sock: A connected TCP socket.
        msg:  A dict that will be JSON-serialized and sent.
        lock: Optional threading.Lock protecting this socket's send path.
              Always pass one on the server side.

    Raises:
        OSError: If the socket is broken (BrokenPipeError, ConnectionResetError,
                 etc.). Let this propagate — callers handle disconnect cleanup.

    """
    payload = json.dumps(msg).encode("utf-8")
    # ">I" = big-endian unsigned 32-bit integer (4 bytes)
    frame = struct.pack(">I", len(payload)) + payload

    if lock is not None:
        with lock:
            sock.sendall(frame)
    else:
        sock.sendall(frame)


def recv_msg(sock: socket) -> dict:
    """Read one framed message from a TCP socket and return the parsed dict.

    Protocol:
        1. Read 4 bytes → decode as big-endian uint32 → message length N.
        2. Read N bytes → decode as UTF-8 → parse as JSON.

    This function blocks until a complete message arrives. It is intended to be
    called from a dedicated reader thread (one thread per socket), so blocking
    here does not stall other work.

    Args:
        sock: A connected TCP socket.

    Returns:
        A dict parsed from the JSON payload.

    Raises:
        ConnectionError: If the socket closes before the full message is read.
                         Callers should catch this to detect client disconnect.
        ValueError:      If the payload bytes are not valid JSON (protocol error).

    """
    # Step 1: read the 4-byte length prefix.
    raw_len = _recv_exactly(sock, 4)
    msg_len = struct.unpack(">I", raw_len)[0]

    # Step 2: read exactly msg_len bytes and parse.
    payload = _recv_exactly(sock, msg_len)
    data = json.loads(payload.decode("utf-8"))
    if not isinstance(data, dict):
        raise TypeError("The JSON was malformed or not sent.")
    return data


# ---------------------------------------------------------------------------
# UDP helpers
# ---------------------------------------------------------------------------


def send_udp(sock: socket, msg: dict, addr: tuple) -> None:
    """Serialize msg to JSON and send as a single UDP datagram.

    UDP is datagram-based — each sendto() is one atomic, self-contained
    message, so no length prefix is needed. Keep messages well under 1400
    bytes to avoid IP fragmentation (timer_tick and answer_count are < 100
    bytes, so this is not a concern in practice).

    Args:
        sock: A bound/connected UDP socket.
        msg:  A dict that will be JSON-serialized and sent.
        addr: The (host, port) destination address.

    """
    payload = json.dumps(msg).encode("utf-8")
    sock.sendto(payload, addr)


def recv_udp(sock: socket, buf_size: int = 4096) -> tuple:
    """Receive one UDP datagram and return the parsed message with its sender.

    Args:
        sock:     A bound UDP socket.
        buf_size: Maximum bytes to receive per datagram. 4096 is more than
                  enough for any message in this protocol.

    Returns:
        A (dict, (host, port)) tuple — the parsed message and sender address.

    Raises:
        ValueError: If the datagram payload is not valid JSON (protocol error).

    """
    data, addr = sock.recvfrom(buf_size)
    return json.loads(data.decode("utf-8")), addr


# ---------------------------------------------------------------------------
# UDP send queue
# ---------------------------------------------------------------------------


class UdpSendQueue:
    """Thread-safe queue for routing UDP datagrams through a single sender thread.

    The threading model requires that all UDP sends go through one thread (the
    UDP listener/sender thread) to avoid ordering issues between timer ticks,
    pong replies, and answer counts. Other threads (game loop, per-client TCP
    threads) enqueue (msg, addr) pairs here rather than calling send_udp
    directly.

    Usage (server side):
        udp_queue = UdpSendQueue()

        # In the game loop thread:
        udp_queue.put({"type": MsgType.TIMER_TICK, ...}, (client_ip, client_udp_port))

        # In the UDP thread:
        msg, addr = udp_queue.get()
        send_udp(udp_sock, msg, addr)
    """

    def __init__(self) -> None:
        """Instantiate a thread-safe queue."""
        self._q: queue.Queue[tuple[dict[str, Any], tuple[str, int]]] = queue.Queue()

    def put(self, msg: dict, addr: tuple) -> None:
        """Enqueue a (msg, addr) pair for sending. Non-blocking."""
        self._q.put((msg, addr))

    def get(self, timeout: float | None = None) -> tuple:
        """Dequeue the next (msg, addr) pair. Blocks until one is available.

        Args:
            timeout: Optional seconds to wait before raising queue.Empty.

        Returns:
            (msg dict, addr tuple)

        Raises:
            queue.Empty: If timeout is set and no item arrives in time.

        """
        return self._q.get(timeout=timeout)

    def empty(self) -> bool:
        """Return True if the queue currently has no items."""
        return self._q.empty()
