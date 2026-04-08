"""Manual smoke test for server.py Task 5 — Server UDP networking.

Self-contained: starts its own GameServer and GameLoop internally.

    python scripts/smoke_test_udp.py

What this tests
---------------
1. Ping → Pong       : registered player sends PING; server replies with PONG
                       that echoes the original timestamp
2. Rejected ping     : bad session code → no PONG (server silently drops)
3. Unjoined player   : valid code but name not in lobby → no PONG
4. Address update    : sending a second PING from a new UDP port updates the
                       registration; PONG arrives at the new address
5. Timer ticks       : start a question; registered UDP client receives
                       TIMER_TICK datagrams with question_index and remaining
6. Answer count      : player answers via TCP; registered UDP client receives
                       ANSWER_COUNT with answered=1, total=2
"""

import json
import socket
import sys
import tempfile
import threading
import time
from typing import cast

sys.path.insert(0, ".")

from src.protocol import MsgType, recv_msg, recv_udp, send_msg, send_udp  # noqa: E402
from src.quiz import load_quiz  # noqa: E402
from src.server import GameServer  # noqa: E402
from src.serverGameLoop import GameLoop  # noqa: E402

HOST = "127.0.0.1"
PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def check(label: str, condition: bool, detail: str = "") -> None:
    """Print a PASS/FAIL line; exit immediately on first failure."""
    status = PASS if condition else FAIL
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    if not condition:
        sys.exit(1)


def tcp_join(port: int, code: str, name: str) -> socket.socket:
    """Open a TCP connection, send JOIN, consume the lobby_update, and return the socket."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((HOST, port))
    send_msg(sock, {"type": MsgType.JOIN, "session_code": code, "display_name": name})
    recv_msg(sock)  # initial lobby_update
    return sock


def udp_socket(timeout: float = 2.0) -> socket.socket:
    """Return a bound UDP socket with the given receive timeout."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((HOST, 0))
    sock.settimeout(timeout)
    return sock


def send_ping(
    sock: socket.socket,
    server_port: int,
    code: str,
    name: str,
    ts: float | None = None,
) -> float:
    """Send a PING and return the timestamp used."""
    t = ts if ts is not None else time.monotonic()
    send_udp(
        sock,
        {
            "type": MsgType.PING,
            "timestamp": t,
            "session_code": code,
            "display_name": name,
        },
        (HOST, server_port),
    )
    return t


def read_udp_until(sock: socket.socket, msg_type: str, limit: int = 30) -> dict:
    """Discard UDP datagrams until one matching msg_type is found."""
    for _ in range(limit):
        msg, _ = recv_udp(sock)
        if msg["type"] == msg_type:
            return cast("dict", msg)
    print(f"  [{FAIL}] Did not receive {msg_type!r} within {limit} datagrams")
    sys.exit(1)


def make_quiz(time_limit: float = 3.0) -> str:
    """Write a single-question quiz to a temp file and return its path."""
    data = {
        "title": "UDP Smoke Quiz",
        "settings": {"pause_between_questions": 0},
        "questions": [
            {
                "text": "What is 1 + 1?",
                "type": "multiple_choice",
                "options": ["1", "2", "3", "4"],
                "answer": 1,
                "time_limit": time_limit,
            }
        ],
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        return f.name


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the end-to-end UDP smoke checks against a self-hosted game server."""
    print("\nSmoke test — Server UDP networking (Task 5)\n")

    server = GameServer(host=HOST, port=0)
    server.start()
    code = server.session_code
    port = server.port
    print(f"Server on {HOST}:{port}  session_code={code}\n")

    # ------------------------------------------------------------------
    # 1. Ping → Pong (registered player)
    # ------------------------------------------------------------------
    print("1. Ping → Pong")

    alice_tcp = tcp_join(port, code, "Alice")
    alice_udp = udp_socket()

    ts = send_ping(alice_udp, port, code, "Alice")
    pong, _ = recv_udp(alice_udp)

    check("type is pong", pong["type"] == MsgType.PONG, pong["type"])
    check(
        "timestamp echoed",
        abs(pong["timestamp"] - ts) < 1e-6,
        f"sent={ts:.6f} got={pong['timestamp']:.6f}",
    )
    check("server_time present", "server_time" in pong)
    check("server_time is a float", isinstance(pong["server_time"], float))

    # ------------------------------------------------------------------
    # 2. Rejected ping — bad session code
    # ------------------------------------------------------------------
    print("\n2. Rejected ping — bad session code → no PONG")

    bad_udp = udp_socket(timeout=0.4)
    send_udp(
        bad_udp,
        {
            "type": MsgType.PING,
            "timestamp": time.monotonic(),
            "session_code": "ZZZZ",
            "display_name": "Alice",
        },
        (HOST, port),
    )
    try:
        recv_udp(bad_udp)
        check("no PONG for bad session code", False, "unexpectedly received a response")
    except (TimeoutError, OSError):
        check("no PONG for bad session code", True)
    bad_udp.close()

    # ------------------------------------------------------------------
    # 3. Rejected ping — player not in lobby
    # ------------------------------------------------------------------
    print("\n3. Rejected ping — unjoined player → no PONG")

    ghost_udp = udp_socket(timeout=0.4)
    send_ping(ghost_udp, port, code, "Ghost")
    try:
        recv_udp(ghost_udp)
        check("no PONG for unjoined player", False, "unexpectedly received a response")
    except (TimeoutError, OSError):
        check("no PONG for unjoined player", True)
    ghost_udp.close()

    # ------------------------------------------------------------------
    # 4. Address update — second ping from new port updates registration
    # ------------------------------------------------------------------
    print("\n4. Address update — second ping from new port")

    new_udp = udp_socket()
    _, new_local_port = new_udp.getsockname()
    send_ping(new_udp, port, code, "Alice")
    pong2, pong2_addr = recv_udp(new_udp)

    check(
        "PONG arrives at new UDP address", pong2["type"] == MsgType.PONG, pong2["type"]
    )
    # The pong came from the server — confirm the socket that received it is new_udp
    check("response received on new socket", pong2_addr[0] == HOST)
    new_udp.close()

    # Restore registration on original socket for subsequent checks
    send_ping(alice_udp, port, code, "Alice")
    recv_udp(alice_udp)  # drain pong

    # ------------------------------------------------------------------
    # 5. Timer ticks during a question
    # ------------------------------------------------------------------
    print("\n5. Timer ticks received via UDP during an active question")

    bob_tcp = tcp_join(port, code, "Bob")
    recv_msg(alice_tcp)  # drain Alice's lobby_update for Bob's join

    quiz = load_quiz(make_quiz(time_limit=3.0))
    loop = GameLoop(server, quiz, min_players=2, lobby_countdown=0)
    threading.Thread(target=loop.start, daemon=True).start()

    tick = read_udp_until(alice_udp, MsgType.TIMER_TICK)

    check(
        "question_index is 0", tick["question_index"] == 0, str(tick["question_index"])
    )
    check("remaining is a float", isinstance(tick["remaining"], float))
    check("remaining is non-negative", tick["remaining"] >= 0.0, str(tick["remaining"]))
    check("remaining <= time_limit", tick["remaining"] <= 3.0, str(tick["remaining"]))

    # ------------------------------------------------------------------
    # 6. Answer count when a player submits an answer
    # ------------------------------------------------------------------
    print("\n6. ANSWER_COUNT broadcast via UDP on answer submission")

    # Ensure Alice's QUESTION message has been received before answering
    alice_tcp.settimeout(5.0)
    bob_tcp.settimeout(5.0)
    for _ in range(10):
        try:
            msg = recv_msg(alice_tcp)
            if msg["type"] == MsgType.QUESTION:
                break
        except TimeoutError:
            break

    send_msg(alice_tcp, {"type": MsgType.ANSWER, "question_index": 0, "choice": 1})

    ac = read_udp_until(alice_udp, MsgType.ANSWER_COUNT)

    check("question_index is 0", ac["question_index"] == 0, str(ac["question_index"]))
    check("answered is 1", ac["answered"] == 1, str(ac["answered"]))
    check("total is 2", ac["total"] == 2, str(ac["total"]))

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------
    alice_udp.close()
    alice_tcp.close()
    bob_tcp.close()
    server.stop()

    print("\nAll checks passed.\n")


if __name__ == "__main__":
    main()
