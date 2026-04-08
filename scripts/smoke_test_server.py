"""Manual smoke test for server.py — TCP join handshake.

Run the server first:
    python scripts/run_server.py [PORT]

Then run this script in a second terminal:
    python scripts/smoke_test_server.py <SESSION_CODE> [PORT]

PORT defaults to 5000. Pass the port printed by the server if it differs.

Example:
    python scripts/smoke_test_server.py 4P6S 3162

What this tests
---------------
1. Valid join           → lobby_update with player in list
2. Duplicate name       → error, connection stays open, retry succeeds
3. Bad session code     → error
4. Second player joins  → both clients receive updated lobby_update
5. Player disconnect    → remaining client receives updated lobby_update

"""

import socket
import sys

sys.path.insert(0, "src")

from src.protocol import MsgType, recv_msg, send_msg  # noqa: E402

HOST = "127.0.0.1"

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"


def connect(port: int) -> socket.socket:
    """Return a new TCP socket connected to the server."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((HOST, port))
    return sock


def join(sock: socket.socket, code: str, name: str) -> dict:
    """Send a join message and return the server's first response."""
    send_msg(sock, {"type": MsgType.JOIN, "session_code": code, "display_name": name})
    return recv_msg(sock)


def check(label: str, condition: bool, detail: str = "") -> None:
    """Print a PASS/FAIL status line and exit immediately on failure."""
    status = PASS if condition else FAIL
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    if not condition:
        sys.exit(1)


def main() -> None:
    """Run the end-to-end manual smoke checks against a running server."""
    if len(sys.argv) < 2:
        print("Usage: python scripts/smoke_test_server.py <SESSION_CODE> [PORT]")
        sys.exit(1)

    code = sys.argv[1].upper()
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
    print(f"\nSmoke test — server at {HOST}:{port}  code={code}\n")

    # ------------------------------------------------------------------
    # 1. Valid join
    # ------------------------------------------------------------------
    print("1. Valid join")
    alice = connect(port)
    r = join(alice, code, "Alice")
    check("type is lobby_update", r["type"] == MsgType.LOBBY_UPDATE, r["type"])
    check("Alice in players", "Alice" in r["players"], str(r["players"]))
    check("countdown_remaining is None", r["countdown_remaining"] is None)
    check("host_started_countdown is False", r["host_started_countdown"] is False)

    # ------------------------------------------------------------------
    # 2. Duplicate name → error, retry on same connection succeeds
    # ------------------------------------------------------------------
    print("\n2. Duplicate name → error, then retry with new name")
    retry = connect(port)
    r = join(retry, code, "Alice")
    check(
        "duplicate name returns error", r["type"] == MsgType.ERROR, r.get("message", "")
    )
    check("error message mentions 'taken'", "taken" in r.get("message", "").lower())

    # Alice gets the lobby_update from retry's failed join attempt... actually
    # the server doesn't broadcast on failed join, but drain any messages.
    # Retry with a different name on the same socket.
    r2 = join(retry, code, "Bob")
    # Drain Alice's lobby_update triggered by Bob's successful join.
    alice_update = recv_msg(alice)
    check(
        "retry with new name succeeds", r2["type"] == MsgType.LOBBY_UPDATE, r2["type"]
    )
    check("Bob now in lobby", "Bob" in r2["players"], str(r2["players"]))
    check("Alice also receives updated lobby", "Bob" in alice_update["players"])

    # ------------------------------------------------------------------
    # 3. Bad session code
    # ------------------------------------------------------------------
    print("\n3. Bad session code")
    bad = connect(port)
    r = join(bad, "ZZZZ", "Charlie")
    check("bad code returns error", r["type"] == MsgType.ERROR, r.get("message", ""))
    bad.close()

    # ------------------------------------------------------------------
    # 4. Both players see each other in the lobby
    # ------------------------------------------------------------------
    print("\n4. Lobby state consistency")
    check(
        "Alice's view has both players",
        set(alice_update["players"]) == {"Alice", "Bob"},
    )
    check("Bob's view has both players", set(r2["players"]) == {"Alice", "Bob"})

    # ------------------------------------------------------------------
    # 5. Disconnect → remaining player receives updated lobby_update
    # ------------------------------------------------------------------
    print("\n5. Bob disconnects → Alice gets updated lobby_update")
    retry.close()  # Bob disconnects
    disconnect_update = recv_msg(alice)
    check("type is lobby_update", disconnect_update["type"] == MsgType.LOBBY_UPDATE)
    check("Bob removed from lobby", "Bob" not in disconnect_update["players"])
    check("Alice still in lobby", "Alice" in disconnect_update["players"])

    alice.close()

    print("\nAll checks passed.\n")


if __name__ == "__main__":
    main()
