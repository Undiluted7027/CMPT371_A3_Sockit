# Protocol Foundation — `protocol.py`

This document is the implementation reference for `protocol.py`. Both Person 1 and Person 2
must agree on everything here before any other module is written. Every other module imports
from this one — it is the shared contract.

---

## Overview

- All messages are **JSON dictionaries** with a `"type"` field identifying the message.
- **TCP messages** are framed with a **4-byte big-endian length prefix**.
- **UDP messages** are plain JSON — no framing needed (each datagram is one complete message).
- There is no versioning. If the protocol changes, both sides must be updated together.

---

## 1. TCP Framing

TCP is a byte stream with no message boundaries. Without framing, a receiver cannot tell where
one message ends and the next begins. The solution is a length-prefix:

```
┌─────────────────────┬──────────────────────────────────┐
│  4 bytes (uint32)   │         N bytes (UTF-8 JSON)     │
│  big-endian length  │         the actual message       │
└─────────────────────┴──────────────────────────────────┘
```

### Sending

```python
import json
import struct

def send_msg(sock, msg: dict, lock=None) -> None:
    """Serialize msg to JSON, prepend 4-byte length, send over TCP socket.

    Pass a threading.Lock as `lock` when the socket is shared across threads
    (e.g. the server broadcasting to a client from multiple threads).

    Raises:
        OSError: if the socket is broken (BrokenPipeError, ConnectionResetError, etc.)
    """
    payload = json.dumps(msg).encode("utf-8")
    frame = struct.pack(">I", len(payload)) + payload  # ">I" = big-endian uint32
    if lock is not None:
        with lock:
            sock.sendall(frame)
    else:
        sock.sendall(frame)
```

### Receiving

`sock.recv(N)` does **not** guarantee N bytes — TCP can deliver partial data. Always loop:

```python
def _recv_exactly(sock, n: int) -> bytes:
    """Read exactly n bytes from sock. Raises ConnectionError if socket closes."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed by remote end")
        buf += chunk
    return buf

def recv_msg(sock) -> dict:
    """Read one framed message from TCP socket. Returns parsed dict.

    Raises:
        ConnectionError: if the socket is closed mid-read
        ValueError: if the payload is not valid JSON
    """
    raw_len = _recv_exactly(sock, 4)
    msg_len = struct.unpack(">I", raw_len)[0]
    payload = _recv_exactly(sock, msg_len)
    return json.loads(payload.decode("utf-8"))
```

### Why `recv_msg` raises instead of returning None

Callers need to distinguish "no message yet" from "connection gone." Raising `ConnectionError`
lets every caller handle disconnect the same way — catch the exception and clean up — rather
than checking return values everywhere.

---

## 2. UDP Serialization

UDP is datagram-based — each `sendto` / `recvfrom` call is one atomic message, so no framing
is needed. Messages are still JSON, just without the length prefix.

```python
def send_udp(sock, msg: dict, addr: tuple) -> None:
    """Serialize msg to JSON and send as a single UDP datagram."""
    payload = json.dumps(msg).encode("utf-8")
    sock.sendto(payload, addr)

def recv_udp(sock, buf_size: int = 4096) -> tuple[dict, tuple]:
    """Receive one UDP datagram. Returns (parsed dict, sender address).

    Raises:
        ValueError: if the datagram payload is not valid JSON
    """
    data, addr = sock.recvfrom(buf_size)
    return json.loads(data.decode("utf-8")), addr
```

Keep all UDP messages well under **1400 bytes** to avoid IP fragmentation. The timer tick and
answer count messages in this project are small (< 100 bytes), so this is not a concern in
practice.

---

## 3. Thread Safety

Multiple threads may call `send_msg` on the same socket concurrently (e.g. the server game
loop broadcasting a question while the per-client thread sends a lobby update). Concurrent
writes corrupt the stream.

**Rule:** every TCP socket on the server gets a paired `threading.Lock`. Pass it to `send_msg`.

```python
import threading

# Created once per client connection on the server:
client_send_lock = threading.Lock()

# Usage:
send_msg(client_sock, {"type": MsgType.QUESTION, "index": 0, ...}, lock=client_send_lock)  # pseudocode
```

Client sockets are written from fewer threads, but the same pattern applies if in doubt.

`recv_msg` intentionally has no lock parameter — each client connection has exactly one
dedicated reader thread, so reads are never concurrent on the same socket.

---

## 4. Message Type Constants

Define all `"type"` string values as constants to eliminate typo bugs. Both sides import this.

```python
class MsgType:
    # --- TCP: Client → Server ---
    JOIN             = "join"
    ANSWER           = "answer"

    # --- TCP: Server → Client ---
    LOBBY_UPDATE     = "lobby_update"
    GAME_START       = "game_start"
    QUESTION         = "question"
    QUESTION_RESULT  = "question_result"
    GAME_OVER        = "game_over"
    PLAYER_DISCONNECTED = "player_disconnected"
    ERROR            = "error"

    # --- UDP: Server → Client ---
    TIMER_TICK       = "timer_tick"
    ANSWER_COUNT     = "answer_count"

    # --- UDP: Client ↔ Server ---
    PING             = "ping"
    PONG             = "pong"
```

---

## 5. Complete Message Catalog

### TCP — Client → Server

**`join`** — sent immediately after connecting; server validates session code and display name.
```json
{
  "type": "join",
  "session_code": "ABCD",
  "display_name": "Alice"
}
```
- On success: server adds the player to the lobby and broadcasts `lobby_update` to all connected clients (including the new joiner) with the updated player list.
- On failure: server sends `error` to the connecting client and keeps the connection open so the client can retry with a different name or code.

---

**`answer`** — player submits their answer for the current question.
```json
{
  "type": "answer",
  "question_index": 0,
  "choice": 1
}
```
- `choice` is always a 0-based integer index into the question's `options` array.
- `question_index` is included so the server can discard late answers for a previous question.

---

### TCP — Server → Client

**`lobby_update`** — broadcast to all clients whenever the lobby state changes (player joins,
leaves, or host triggers/cancels the auto-start countdown).
```json
{
  "type": "lobby_update",
  "players": ["Alice", "Bob", "Charlie"],
  "host_started_countdown": false,
  "countdown_remaining": null
}
```
- `countdown_remaining` is a float (seconds) when a countdown is active, otherwise `null`.
- Countdown display logic for clients:
  - `host_started_countdown: true, countdown_remaining: 25.5` → show countdown timer
  - `host_started_countdown: false, countdown_remaining: null` → show normal lobby (covers both "never started" and "was cancelled" — client treats both identically)

---

**`game_start`** — signals all clients to transition from lobby to the first question.
```json
{
  "type": "game_start"
}
```
- The server sends a `question` message immediately after `game_start`. Clients should transition to a "loading question" state and wait — there is nothing to render from `game_start` itself.

---

**`question`** — delivers the next question to all clients simultaneously.
```json
{
  "type": "question",
  "index": 0,
  "text": "What is the capital of France?",
  "question_type": "multiple_choice",
  "options": ["London", "Paris", "Berlin", "Madrid"],
  "time_limit": 15
}
```
- `index` is the 0-based question number — used by clients to display "Question X of Y" and to match against `question_index` in UDP timer/answer-count messages.
- `question_type` is `"multiple_choice"` (4 options) or `"true_false"` (2 options).
- For `"true_false"` questions, `options` is always `["True", "False"]` — `choice: 0` means True, `choice: 1` means False.
- `time_limit` is the authoritative deadline in seconds for this question.

---

**`question_result`** — sent after each question ends; carries per-player score and leaderboard.
```json
{
  "type": "question_result",
  "correct_answer": 1,
  "your_score": 850,
  "your_total": 850,
  "your_streak": 1,
  "leaderboard": [
    {"name": "Alice", "score": 850},
    {"name": "Bob", "score": 720}
  ],
  "show_correct_answer": true,
  "show_leaderboard": true,
  "pause_duration": 5
}
```
- `correct_answer` = 0-based index into the question's `options` array. To display the answer text, look up `options[correct_answer]`.
- `your_score` = points earned on **this question only**. `your_total` = cumulative score across all questions so far.
- `your_streak` = number of consecutive correct answers ending at this question. Resets to 0 if this question was wrong or unanswered.
- `leaderboard[].score` = each player's **cumulative total** score (not this-question score).
- `show_correct_answer` and `show_leaderboard` come from the quiz file settings — clients
  must respect them (don't show the answer if `false`).
- `pause_duration` tells the client how many seconds to display this screen before the server
  sends the next message — either another `question` (if questions remain) or `game_over` (after the last question).

---

**`game_over`** — sent after the final question result; ends the game.
```json
{
  "type": "game_over",
  "final_rankings": [
    {"rank": 1, "name": "Alice", "score": 8500, "correct": 8, "streak": 5, "fastest_answer": 1.2}
  ],
  "total_questions": 10
}
```
- `final_rankings` is sorted ascending by rank (rank 1 first) — render it in order, no client-side sorting needed.
- `rank` = 1-based finishing position (1 = winner).
- `score` = player's total accumulated score for the entire game.
- `correct` = total count of correctly answered questions for that player this game.
- `streak` = the player's **longest** streak of consecutive correct answers across the whole game (not the streak at the final question).
- `fastest_answer` is in seconds (float) — the player's single fastest correct answer this game.
- `total_questions` = total number of questions in the quiz — use this to display per-player stats like "correct: 8 / 10".

---

**`player_disconnected`** — broadcast to all remaining clients when a player drops **during an active game** (after `game_start`); game continues without them.
```json
{
  "type": "player_disconnected",
  "display_name": "Charlie"
}
```
- **Lobby-phase disconnections** do not use this message. If a player leaves before the game starts, the server simply sends a `lobby_update` to all remaining clients with the updated player list.

---

**`error`** — sent to a specific client when their request fails (bad session code, duplicate
name, etc.).
```json
{
  "type": "error",
  "message": "Invalid session code"
}
```

---

### UDP — Server → Client

**`timer_tick`** — broadcast ~10 times per second during a question; clients use this to render
the countdown. The server's authoritative timer still enforces the deadline regardless.
```json
{
  "type": "timer_tick",
  "question_index": 0,
  "remaining": 12.3
}
```
- `question_index` lets clients discard stale ticks from the previous question.

---

**`answer_count`** — broadcast each time a new answer arrives; clients show "X of Y answered."
```json
{
  "type": "answer_count",
  "question_index": 0,
  "answered": 5,
  "total": 8
}
```
- `question_index` lets clients discard stale counts from the previous question.
- `total` = total number of players currently in the game. `answered` / `total` gives the "X of Y" display ratio.

---

### UDP — Client ↔ Server (Latency Measurement)

**`ping`** — sent by the client periodically; server echoes back as `pong`.
```json
{
  "type": "ping",
  "timestamp": 1234567890.123
}
```

**`pong`** — server's reply; includes the original client timestamp plus the server's current
time. The client calculates RTT as `now - ping.timestamp`. The server uses half the RTT to
compensate for network delay when scoring answers.
```json
{
  "type": "pong",
  "timestamp": 1234567890.123,
  "server_time": 1234567890.456
}
```
- `timestamp` is echoed back unchanged so the client can compute `RTT = time.time() - pong["timestamp"]`.
- `server_time` is the server's `time.time()` at the moment it sent the pong — used to
  estimate clock offset if needed.
- **Both sides must use `time.time()` (wall-clock) for ping/pong timestamps**, not
  `time.monotonic()`. The game loop uses `time.monotonic()` for authoritative timers, but
  RTT measurement requires wall-clock time on both client and server to be compatible.

---

## 6. Threading Model — Protocol Implications

| Component | Threads | Implication |
|---|---|---|
| Server — game loop | Main thread | Writes questions, results, game_over to all clients — needs send lock per client |
| Server — TCP acceptor | Dedicated thread | Only accepts; hands socket to per-client thread |
| Server — per-client TCP | One thread per client | Reads only; writes go through game loop or disconnect handler |
| Server — UDP listener | Dedicated thread | Reads pings; sends pong. Receives timer/answer-count triggers from other threads via a shared queue and sends timer_tick, answer_count |
| Client — Tkinter GUI | Main thread | Never blocks; all socket reads happen on background threads |
| Client — TCP listener | Dedicated thread | Reads only; dispatches to GUI via `widget.after()` |
| Client — UDP listener | Dedicated thread | Reads timer_tick and answer_count |
| Client — UDP sender | Dedicated thread | Sends ping periodically |

**Key rule:** GUI updates from background threads must always go through `widget.after(0, fn)`
— Tkinter is not thread-safe.

**UDP send thread safety:** Individual UDP `sendto` calls for small datagrams are atomic at
the OS level, so `send_udp` does not need a lock. However, per the threading model above, the
UDP listener thread is the sole owner of all UDP sends. The game loop and per-client TCP threads
signal it via a shared `queue.Queue` rather than calling `send_udp` directly — this keeps all
UDP I/O on one thread and avoids subtle ordering issues between timer ticks and pong replies.

---

## 7. Implementation Checklist

- [ ] `send_msg(sock, msg, lock=None)` implemented and tested
- [ ] `recv_msg(sock)` with `_recv_exactly` loop implemented and tested
- [ ] `send_udp` / `recv_udp` implemented
- [ ] `MsgType` constants defined and cover all 13 message types
- [ ] `ConnectionError` raised (not swallowed) on socket close in `recv_msg`
- [ ] Tested with a simple echo script: send 3 messages back-to-back, verify all 3 received correctly with no boundary issues
- [ ] UDP listener queue (`queue.Queue`) implemented and tested — game loop and per-client TCP threads enqueue timer_tick/answer_count payloads; UDP listener thread dequeues and sends them
- [ ] Both Person 1 and Person 2 have reviewed the full message catalog — no field names changed without telling each other
