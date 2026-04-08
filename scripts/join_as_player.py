"""Interactive player client for manual end-to-end testing.

Run the host GUI first, click "Launch Server", note the session code and port.
Then open two terminals and run this script in each:

    python scripts/join_as_player.py <SESSION_CODE> <DISPLAY_NAME> [PORT]

PORT defaults to 5001.

Example:
    python scripts/join_as_player.py ABC1 Alice
    python scripts/join_as_player.py ABC1 Bob

What this does
--------------
- Connects to the server and joins as a named player.
- Prints every message received from the server.
- When a QUESTION arrives, prompts you to pick an answer (1-indexed).
  Press Enter without a number to skip (simulate no answer).
- Stays alive through QUESTION_RESULT, GAME_OVER, and disconnect.

"""

from __future__ import annotations

import sys
import threading

sys.path.insert(0, ".")

import socket  # noqa: E402

from src.protocol import MsgType, recv_msg, send_msg  # noqa: E402

HOST = "127.0.0.1"


def _fmt_msg(msg: dict) -> str:
    t = msg.get("type", "?")
    if t == MsgType.LOBBY_UPDATE:
        players = msg.get("players", [])
        return f"[LOBBY_UPDATE] Players in lobby: {players}"
    if t == MsgType.GAME_START:
        return "[GAME_START] Game is starting!"
    if t == MsgType.QUESTION:
        idx = msg.get("index", "?")
        text = msg.get("text", "")
        opts = msg.get("options", [])
        limit = msg.get("time_limit", "?")
        lines = [f"[QUESTION {idx}] ({limit}s) {text}"]
        for i, opt in enumerate(opts):
            lines.append(f"  {i + 1}. {opt}")
        return "\n".join(lines)
    if t == MsgType.QUESTION_RESULT:
        correct = msg.get("correct_answer", "?")
        your_score = msg.get("your_score", 0)
        your_total = msg.get("your_total", 0)
        streak = msg.get("your_streak", 0)
        lb = msg.get("leaderboard", [])
        lines = [
            f"[QUESTION_RESULT] correct={correct}  "
            f"score={your_score}  total={your_total}  streak={streak}"
        ]
        if lb:
            lines.append("  Leaderboard:")
            for entry in lb:
                lines.append(f"    {entry.get('name')}: {entry.get('total_score')}")
        return "\n".join(lines)
    if t == MsgType.GAME_OVER:
        rankings = msg.get("final_rankings", [])
        lines = ["[GAME_OVER] Final rankings:"]
        for r in rankings:
            lines.append(
                f"  #{r.get('rank')} {r.get('name')}  "
                f"score={r.get('total_score')}  correct={r.get('correct_answers')}"
            )
        return "\n".join(lines)
    if t == MsgType.ERROR:
        return f"[ERROR] {msg.get('message', msg)}"
    if t == MsgType.PLAYER_DISCONNECTED:
        return f"[PLAYER_DISCONNECTED] {msg.get('display_name')} left"
    return f"[{t}] {msg}"


def main() -> None:
    """Run an interactive terminal client that joins and plays as one user."""
    if len(sys.argv) < 3:
        print("Usage: python scripts/join_as_player.py <SESSION_CODE> <NAME> [PORT]")
        sys.exit(1)

    code = sys.argv[1].upper()
    name = sys.argv[2]
    port = int(sys.argv[3]) if len(sys.argv) > 3 else 5001

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((HOST, port))
    except ConnectionRefusedError:
        print(f"Could not connect to {HOST}:{port} — is the server running?")
        sys.exit(1)

    print(f"Connected to {HOST}:{port}. Joining as '{name}' with code {code}...")
    send_msg(sock, {"type": MsgType.JOIN, "session_code": code, "display_name": name})

    # Shared state: current question index and option count for input validation.
    state: dict = {"q_index": None, "n_options": 0, "game_over": False}
    state_lock = threading.Lock()

    def listen() -> None:
        while True:
            try:
                msg = recv_msg(sock)
            except (ConnectionError, OSError):
                print("\n[DISCONNECTED] Server closed the connection.")
                with state_lock:
                    state["game_over"] = True
                return

            print("\n" + _fmt_msg(msg))

            t = msg.get("type")
            if t == MsgType.QUESTION:
                with state_lock:
                    state["q_index"] = msg.get("index")
                    state["n_options"] = len(msg.get("options", []))
                print(
                    f"  → Enter choice (1–{state['n_options']}) or press Enter to skip: ",
                    end="",
                    flush=True,
                )
            elif t == MsgType.QUESTION_RESULT:
                with state_lock:
                    state["q_index"] = None
            elif t == MsgType.GAME_OVER:
                with state_lock:
                    state["game_over"] = True
                return

    listener = threading.Thread(target=listen, daemon=True)
    listener.start()

    # Main thread handles user input for answering questions.
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            break

        with state_lock:
            q_index = state["q_index"]
            n_options = state["n_options"]
            over = state["game_over"]

        if over:
            break

        if q_index is None:
            # Not in a question window — ignore stray input.
            continue

        line = line.strip()
        if not line:
            print("  (skipped — no answer submitted)")
            continue

        try:
            choice_1indexed = int(line)
        except ValueError:
            print(f"  Enter a number between 1 and {n_options}.")
            continue

        if not (1 <= choice_1indexed <= n_options):
            print(f"  Enter a number between 1 and {n_options}.")
            continue

        choice = choice_1indexed - 1  # convert to 0-indexed
        send_msg(
            sock,
            {"type": MsgType.ANSWER, "question_index": q_index, "choice": choice},
        )
        print(f"  → Submitted answer {choice_1indexed} (index {choice})")
        with state_lock:
            state["q_index"] = None  # prevent double-submit

    listener.join(timeout=2)
    sock.close()
    print("Done.")


if __name__ == "__main__":
    main()
