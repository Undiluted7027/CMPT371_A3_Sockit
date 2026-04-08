"""Manual smoke test for serverGameLoop.py — full game flow end-to-end.

Self-contained: starts its own GameServer and GameLoop internally.

    python scripts/smoke_test_game_loop.py

What this tests
---------------
1. Lobby        → two players join; each receives lobby_update with both names
2. GAME_START   → game loop broadcasts game_start to all joined clients
3. QUESTION     → question broadcast has correct index, text, options,
                  question_type, and time_limit fields
4. ANSWER       → Alice submits correct answer; QUESTION_RESULT leaderboard
                  shows Alice=1, Bob=0
5. Multi-round  → second question broadcasts; scores persist across rounds
6. GAME_OVER    → final_rankings include both players; Alice ranks first;
                  'correct' and 'total_questions' fields are present
"""

import json
import socket
import sys
import tempfile
import threading

sys.path.insert(0, ".")

from src.protocol import MsgType, recv_msg, send_msg  # noqa: E402
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


def connect_and_join(port: int, code: str, name: str) -> socket.socket:
    """Open a TCP connection and send a JOIN message; return the socket."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((HOST, port))
    send_msg(sock, {"type": MsgType.JOIN, "session_code": code, "display_name": name})
    return sock


def read_until(sock: socket.socket, msg_type: str, timeout: float = 15.0) -> dict:
    """Read messages, discarding non-matching ones, until msg_type is found."""
    sock.settimeout(timeout)
    for _ in range(30):
        msg = recv_msg(sock)
        if msg["type"] == msg_type:
            return msg
    print(f"  [{FAIL}] Timed out waiting for {msg_type!r}")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Run end-to-end smoke checks for the game loop using a local server."""
    print("\nSmoke test — GameLoop end-to-end\n")

    # ------------------------------------------------------------------
    # Setup: write a two-question quiz, start server, start game loop
    # ------------------------------------------------------------------
    quiz_data = {
        "title": "Smoke Test Quiz",
        "settings": {"pause_between_questions": 1},
        "questions": [
            {
                "text": "What is 2 + 2?",
                "type": "multiple_choice",
                "options": ["1", "2", "3", "4"],
                "answer": 3,
                "time_limit": 5,
            },
            {
                "text": "Capital of France?",
                "type": "multiple_choice",
                "options": ["Berlin", "Madrid", "Paris", "Rome"],
                "answer": 2,
                "time_limit": 5,
            },
        ],
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(quiz_data, f)
        quiz_path = f.name

    quiz = load_quiz(quiz_path)
    server = GameServer(host=HOST, port=0)
    server.start()
    code = server.session_code
    port = server.port
    print(f"Server on {HOST}:{port}  session_code={code}\n")

    # ------------------------------------------------------------------
    # 1. Lobby — two players join
    # ------------------------------------------------------------------
    print("1. Lobby — two players join")

    alice = connect_and_join(port, code, "Alice")
    r = recv_msg(alice)
    check("Alice receives lobby_update", r["type"] == MsgType.LOBBY_UPDATE, r["type"])
    check("Alice in players list", "Alice" in r["players"])
    check("host_started_countdown is False", r["host_started_countdown"] is False)
    check("countdown_remaining is None", r["countdown_remaining"] is None)

    bob = connect_and_join(port, code, "Bob")
    r_bob = recv_msg(bob)
    r_alice_update = recv_msg(alice)  # lobby_update broadcast after Bob joins
    check(
        "Bob receives lobby_update with both players",
        set(r_bob["players"]) == {"Alice", "Bob"},
    )
    check("Alice receives updated lobby_update", "Bob" in r_alice_update["players"])

    # ------------------------------------------------------------------
    # 2. Game start
    # ------------------------------------------------------------------
    print("\n2. GAME_START broadcast")

    loop = GameLoop(server, quiz, min_players=2, lobby_countdown=0)
    loop_thread = threading.Thread(target=loop.start, daemon=True)
    loop_thread.start()

    check(
        "Alice receives GAME_START",
        read_until(alice, MsgType.GAME_START)["type"] == MsgType.GAME_START,
    )
    check(
        "Bob receives GAME_START",
        read_until(bob, MsgType.GAME_START)["type"] == MsgType.GAME_START,
    )

    # ------------------------------------------------------------------
    # 3. First question fields
    # ------------------------------------------------------------------
    print("\n3. First QUESTION broadcast")

    q1 = read_until(alice, MsgType.QUESTION)
    read_until(bob, MsgType.QUESTION)  # drain Bob's copy

    check("index is 0", q1["index"] == 0, str(q1["index"]))
    check("text matches quiz", q1["text"] == "What is 2 + 2?", repr(q1["text"]))
    check("options list has 4 items", len(q1["options"]) == 4, str(q1["options"]))
    check("question_type field present", "question_type" in q1, str(q1.keys()))
    check("question_type is multiple_choice", q1["question_type"] == "multiple_choice")
    check("time_limit field present", "time_limit" in q1)
    check("time_limit is 5", q1["time_limit"] == 5, str(q1["time_limit"]))

    # ------------------------------------------------------------------
    # 4. Alice answers correctly; Bob skips
    # ------------------------------------------------------------------
    print("\n4. ANSWER → QUESTION_RESULT scoring")

    send_msg(alice, {"type": MsgType.ANSWER, "question_index": 0, "choice": 3})

    r1 = read_until(alice, MsgType.QUESTION_RESULT)
    read_until(bob, MsgType.QUESTION_RESULT)  # drain Bob's copy

    check("correct_answer is 3", r1["correct_answer"] == 3, str(r1["correct_answer"]))
    lb = {e["name"]: e["score"] for e in r1["leaderboard"]}
    check(
        "Alice score > 0 (time-based, between 500 and 1000)",
        500 <= lb.get("Alice", 0) <= 1000,
        str(lb),
    )
    check("Bob score is 0", lb.get("Bob") == 0, str(lb))
    check("show_correct_answer field present", "show_correct_answer" in r1)
    check("pause_duration field present", "pause_duration" in r1)

    # ------------------------------------------------------------------
    # 5. Second question — scores persist
    # ------------------------------------------------------------------
    print("\n5. Second QUESTION — scores carry over")

    q2 = read_until(alice, MsgType.QUESTION)
    read_until(bob, MsgType.QUESTION)

    check("index is 1", q2["index"] == 1, str(q2["index"]))
    check("text matches quiz", q2["text"] == "Capital of France?", repr(q2["text"]))

    # Neither player answers — wait for result
    r2 = read_until(alice, MsgType.QUESTION_RESULT)
    read_until(bob, MsgType.QUESTION_RESULT)

    lb2 = {e["name"]: e["score"] for e in r2["leaderboard"]}
    check(
        "Alice score unchanged (still 500–1000 from Q1)",
        500 <= lb2.get("Alice", 0) <= 1000,
        str(lb2),
    )
    check("Bob score still 0", lb2.get("Bob") == 0, str(lb2))

    # ------------------------------------------------------------------
    # 6. Game over — final rankings
    # ------------------------------------------------------------------
    print("\n6. GAME_OVER final rankings")

    go = read_until(alice, MsgType.GAME_OVER)

    check(
        "total_questions is 2", go["total_questions"] == 2, str(go["total_questions"])
    )
    names = {r["name"] for r in go["final_rankings"]}
    check("Alice in final_rankings", "Alice" in names)
    check("Bob in final_rankings", "Bob" in names)

    ranks = {r["name"]: r for r in go["final_rankings"]}
    check("Alice is rank 1", ranks["Alice"]["rank"] == 1, str(ranks["Alice"]))
    check("Bob is rank 2", ranks["Bob"]["rank"] == 2, str(ranks["Bob"]))
    check("'correct' count present for Alice", "correct" in ranks["Alice"])
    check(
        "Alice correct count is 1",
        ranks["Alice"]["correct"] == 1,
        str(ranks["Alice"]["correct"]),
    )
    check(
        "Bob correct count is 0",
        ranks["Bob"]["correct"] == 0,
        str(ranks["Bob"]["correct"]),
    )

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------
    alice.close()
    bob.close()
    server.stop()
    loop_thread.join(timeout=5)

    print("\nAll checks passed.\n")


if __name__ == "__main__":
    main()
