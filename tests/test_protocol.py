"""Tests for protocol.py — framing, serialization, constants, and queue.

Coverage map
------------
TestMsgType            : all 13 constants exist and carry correct string values
TestRecvExactly        : exact-read loop; ConnectionError on remote close
TestSendRecvMsg        : single-message roundtrip; 3-message back-to-back (echo
                         test from checklist §7); large payload; nested types;
                         ConnectionError on closed socket; ValueError on bad JSON;
                         send with / without lock; concurrent sends with lock
TestUdpSendRecv        : roundtrip; sender address returned; ValueError on bad JSON
TestUdpSendQueue       : put/get roundtrip; FIFO order; empty(); get timeout
"""

import json
import queue
import socket
import struct
import threading
from collections.abc import Generator
from typing import Any

import pytest

from protocol import (
    MsgType,
    UdpSendQueue,
    _recv_exactly,
    recv_msg,
    recv_udp,
    send_msg,
    send_udp,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tcp_pair() -> Generator[tuple[socket.socket, socket.socket], None, None]:
    """Create a connected AF_UNIX SOCK_STREAM socket pair without network I/O."""
    a, b = socket.socketpair()
    yield a, b
    a.close()
    b.close()


@pytest.fixture
def udp_pair() -> Generator[tuple[socket.socket, socket.socket, tuple], None, None]:
    """Create a UDP sender socket and a bound receiver socket on loopback.

    Yields (sender, receiver, recv_addr).
    """
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    addr = receiver.getsockname()
    yield sender, receiver, addr
    sender.close()
    receiver.close()


# ---------------------------------------------------------------------------
# MsgType constants
# ---------------------------------------------------------------------------


class TestMsgType:
    """All 13 message-type constants must be present with the correct string value."""

    EXPECTED: dict[str, str] = {
        # TCP Client → Server
        "JOIN": "join",
        "ANSWER": "answer",
        # TCP Server → Client
        "LOBBY_UPDATE": "lobby_update",
        "GAME_START": "game_start",
        "QUESTION": "question",
        "QUESTION_RESULT": "question_result",
        "GAME_OVER": "game_over",
        "PLAYER_DISCONNECTED": "player_disconnected",
        "ERROR": "error",
        # UDP Server → Client
        "TIMER_TICK": "timer_tick",
        "ANSWER_COUNT": "answer_count",
        # UDP Client ↔ Server
        "PING": "ping",
        "PONG": "pong",
    }

    def test_all_thirteen_constants_present(self) -> None:
        """Ensure every expected message-type constant exists on MsgType."""
        for attr in self.EXPECTED:
            assert hasattr(MsgType, attr), f"MsgType.{attr} is missing"

    def test_string_values(self) -> None:
        """Verify each message-type constant maps to the correct wire value."""
        for attr, expected_value in self.EXPECTED.items():
            assert getattr(MsgType, attr) == expected_value, (
                f"MsgType.{attr} = {getattr(MsgType, attr)!r}, expected {expected_value!r}"
            )

    def test_count(self) -> None:
        """Confirm MsgType exposes exactly the 13 public protocol constants."""
        public_attrs = [a for a in vars(MsgType) if not a.startswith("_")]
        assert len(public_attrs) == 13, (
            f"Expected 13 MsgType constants, found {len(public_attrs)}: {public_attrs}"
        )


# ---------------------------------------------------------------------------
# _recv_exactly
# ---------------------------------------------------------------------------


class TestRecvExactly:
    """Tests for exact-byte TCP reads and disconnect behavior in _recv_exactly."""

    def test_reads_exact_byte_count(self, tcp_pair: tuple) -> None:
        """Read a known payload and confirm bytes are returned unchanged."""
        a, b = tcp_pair
        data = b"hello world"
        a.sendall(data)
        result = _recv_exactly(b, len(data))
        assert result == data

    def test_returns_bytes_of_correct_length(self, tcp_pair: tuple) -> None:
        """Ensure the helper returns exactly the requested byte length."""
        a, b = tcp_pair
        a.sendall(b"\x00" * 100)
        result = _recv_exactly(b, 100)
        assert len(result) == 100

    def test_raises_connection_error_when_socket_closes_before_n_bytes(
        self, tcp_pair: tuple
    ) -> None:
        """Ensure a connection error is raised when socket closes before n bytes."""
        a, b = tcp_pair
        a.sendall(b"\xab\xcd")  # only 2 bytes, but we will ask for 4
        a.close()
        with pytest.raises(ConnectionError):
            _recv_exactly(b, 4)

    def test_raises_connection_error_on_immediately_closed_socket(
        self, tcp_pair: tuple
    ) -> None:
        """Ensure connection error is raised on immediately closed socket."""
        a, b = tcp_pair
        a.close()
        with pytest.raises(ConnectionError):
            _recv_exactly(b, 1)


# ---------------------------------------------------------------------------
# send_msg / recv_msg (TCP framing)
# ---------------------------------------------------------------------------


class TestSendRecvMsg:
    """Tests for TCP framing, JSON serialization, and locking semantics."""

    def test_single_message_roundtrip(self, tcp_pair: tuple) -> None:
        """Send one framed message and verify recv_msg returns the same dict."""
        a, b = tcp_pair
        msg = {"type": MsgType.GAME_START}
        send_msg(a, msg)
        assert recv_msg(b) == msg

    def test_three_messages_back_to_back_echo(self, tcp_pair: tuple) -> None:
        """Checklist item §7 — send 3 messages back-to-back.

        verify all 3 arrive with correct boundaries (no frame bleed-through).
        """
        a, b = tcp_pair
        messages: list[dict[str, Any]] = [
            {"type": MsgType.JOIN, "session_code": "ABCD", "display_name": "Alice"},
            {"type": MsgType.ANSWER, "question_index": 0, "choice": 2},
            {"type": MsgType.GAME_OVER, "final_rankings": [], "total_questions": 5},
        ]
        for msg in messages:
            send_msg(a, msg)

        received = [recv_msg(b) for _ in messages]
        assert received == messages

    def test_large_payload(self, tcp_pair: tuple) -> None:
        """A payload larger than a single socket buffer still arrives intact.

        Send from a background thread so sendall and recv_msg can run
        concurrently — otherwise the sender blocks waiting for the buffer to
        drain while the receiver never gets scheduled (deadlock).
        """
        a, b = tcp_pair
        msg: dict = {"type": MsgType.QUESTION, "text": "x" * 50_000}
        t = threading.Thread(target=send_msg, args=(a, msg))
        t.start()
        result = recv_msg(b)
        t.join()
        assert result == msg

    def test_nested_and_typed_values(self, tcp_pair: tuple) -> None:
        """Preserve nested structures and mixed JSON-compatible value types."""
        a, b = tcp_pair
        msg = {
            "type": MsgType.QUESTION_RESULT,
            "correct_answer": 1,
            "your_score": 850,
            "your_total": 850,
            "your_streak": 3,
            "leaderboard": [
                {"name": "Alice", "score": 850},
                {"name": "Bob", "score": 0},
            ],
            "show_correct_answer": True,
            "show_leaderboard": False,
            "pause_duration": 5,
            "extra_null": None,
        }
        send_msg(a, msg)
        assert recv_msg(b) == msg

    def test_recv_raises_connection_error_on_closed_socket(
        self, tcp_pair: tuple
    ) -> None:
        """Raise ConnectionError when reading from an already closed peer."""
        a, b = tcp_pair
        a.close()
        with pytest.raises(ConnectionError):
            recv_msg(b)

    def test_recv_raises_connection_error_mid_message(self, tcp_pair: tuple) -> None:
        """Socket closes after the length prefix but before the full payload."""
        a, b = tcp_pair
        payload = b'{"type": "game_start"}'
        # Send length prefix claiming 100 bytes, but only send 10 bytes then close.
        a.sendall(struct.pack(">I", 100) + payload[:10])
        a.close()
        with pytest.raises(ConnectionError):
            recv_msg(b)

    def test_recv_raises_value_error_on_invalid_json(self, tcp_pair: tuple) -> None:
        """Raise ValueError when payload bytes are not valid JSON text."""
        a, b = tcp_pair
        bad_payload = b"this is not json {"
        a.sendall(struct.pack(">I", len(bad_payload)) + bad_payload)
        with pytest.raises(ValueError):
            recv_msg(b)

    def test_send_without_lock(self, tcp_pair: tuple) -> None:
        """Allow sending without a lock for single-threaded socket writers."""
        a, b = tcp_pair
        msg = {"type": MsgType.LOBBY_UPDATE, "players": ["Alice"]}
        send_msg(a, msg, lock=None)
        assert recv_msg(b) == msg

    def test_send_with_lock(self, tcp_pair: tuple) -> None:
        """Support sending while holding a provided lock object."""
        a, b = tcp_pair
        lock = threading.Lock()
        msg = {"type": MsgType.LOBBY_UPDATE, "players": ["Bob"]}
        send_msg(a, msg, lock=lock)
        assert recv_msg(b) == msg

    def test_concurrent_sends_with_lock_all_messages_arrive(
        self, tcp_pair: tuple
    ) -> None:
        """Multiple threads sending simultaneously with a shared lock produce non-interleaved frames.

        must produce N intact, non-interleaved frames on the receiver side.
        """
        a, b = tcp_pair
        lock = threading.Lock()
        n = 20
        messages: list[dict[str, int | str]] = [
            {"type": MsgType.ANSWER, "question_index": i, "choice": i % 4}
            for i in range(n)
        ]

        threads = [
            threading.Thread(target=send_msg, args=(a, msg, lock)) for msg in messages
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        received = [recv_msg(b) for _ in range(n)]
        # Order is non-deterministic; verify all arrived exactly once.
        assert sorted(received, key=lambda m: m["question_index"]) == sorted(
            messages, key=lambda m: m["question_index"]
        )

    def test_frame_format_is_big_endian_length_prefix(self, tcp_pair: tuple) -> None:
        """Verify the wire format directly.

        first 4 bytes must be the payload length
        as a big-endian uint32.
        """
        a, b = tcp_pair
        msg = {"type": "ping"}
        send_msg(a, msg)

        raw = b""
        while len(raw) < 4:
            raw += b.recv(4 - len(raw))

        claimed_len = struct.unpack(">I", raw)[0]
        rest = b""
        while len(rest) < claimed_len:
            rest += b.recv(claimed_len - len(rest))

        assert json.loads(rest.decode("utf-8")) == msg


# ---------------------------------------------------------------------------
# send_udp / recv_udp
# ---------------------------------------------------------------------------


class TestUdpSendRecv:
    """Tests for UDP JSON send/receive roundtrips and error handling."""

    def test_roundtrip(self, udp_pair: tuple) -> None:
        """Send one UDP JSON datagram and confirm parsed message integrity."""
        sender, receiver, addr = udp_pair
        msg = {"type": MsgType.TIMER_TICK, "question_index": 0, "remaining": 12.3}
        send_udp(sender, msg, addr)
        received_msg, _ = recv_udp(receiver)
        assert received_msg == msg

    def test_returns_sender_address(self, udp_pair: tuple) -> None:
        """Return the source UDP address alongside the decoded payload."""
        sender, receiver, addr = udp_pair
        sender.bind(("127.0.0.1", 0))
        expected_src = sender.getsockname()
        send_udp(sender, {"type": MsgType.PING, "timestamp": 1.0}, addr)
        _, src_addr = recv_udp(receiver)
        assert src_addr == expected_src

    def test_multiple_distinct_datagrams(self, udp_pair: tuple) -> None:
        """Preserve datagram boundaries across multiple sequential sends."""
        sender, receiver, addr = udp_pair
        msgs = [
            {"type": MsgType.TIMER_TICK, "question_index": 0, "remaining": 10.0},
            {
                "type": MsgType.ANSWER_COUNT,
                "question_index": 0,
                "answered": 3,
                "total": 5,
            },
            {"type": MsgType.PING, "timestamp": 9999.9},
        ]
        for msg in msgs:
            send_udp(sender, msg, addr)

        received = [recv_udp(receiver)[0] for _ in msgs]
        assert received == msgs

    def test_raises_value_error_on_invalid_json(self, udp_pair: tuple) -> None:
        """Raise ValueError when a UDP payload cannot be parsed as JSON."""
        sender, receiver, addr = udp_pair
        sender.sendto(b"not valid json", addr)
        with pytest.raises(ValueError):
            recv_udp(receiver)


# ---------------------------------------------------------------------------
# UdpSendQueue
# ---------------------------------------------------------------------------


class TestUdpSendQueue:
    """Tests for queue semantics used by the UDP sender thread."""

    def test_put_get_roundtrip(self) -> None:
        """Roundtrip one queued (message, address) tuple through the queue."""
        q = UdpSendQueue()
        msg = {"type": MsgType.TIMER_TICK, "remaining": 5.0}
        addr = ("127.0.0.1", 5000)
        q.put(msg, addr)
        got_msg, got_addr = q.get()
        assert got_msg == msg
        assert got_addr == addr

    def test_fifo_order_preserved(self) -> None:
        """Dequeue items in the same order they were enqueued (FIFO)."""
        q = UdpSendQueue()
        items = [
            ({"type": MsgType.TIMER_TICK, "n": i}, ("127.0.0.1", i)) for i in range(5)
        ]
        for msg, addr in items:
            q.put(msg, addr)
        received = [q.get() for _ in items]
        assert received == items

    def test_empty_true_when_no_items(self) -> None:
        """Report empty() as True for a newly created queue."""
        q = UdpSendQueue()
        assert q.empty() is True

    def test_empty_false_after_put(self) -> None:
        """Report empty() as False after an item is enqueued."""
        q = UdpSendQueue()
        q.put({"type": MsgType.PONG}, ("127.0.0.1", 1))
        assert q.empty() is False

    def test_empty_true_after_get(self) -> None:
        """Return to empty() == True after enqueueing then dequeueing one item."""
        q = UdpSendQueue()
        q.put({"type": MsgType.PONG}, ("127.0.0.1", 1))
        q.get()
        assert q.empty() is True

    def test_get_with_timeout_raises_queue_empty(self) -> None:
        """Raise queue.Empty when get() times out with no available items."""
        q = UdpSendQueue()
        with pytest.raises(queue.Empty):
            q.get(timeout=0.05)
