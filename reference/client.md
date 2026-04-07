# `client.py` — TCP Game Client

> **See also:** [`protocol-foundation.md`](protocol-foundation.md) for wire format and message catalog.
> **Audience:** Praneet (Task 4 + Phase 3+). This document describes what the scaffold provides and exactly what needs to be implemented.

---

## Overview

`client.py` provides `GameClient` — the networking layer between the player and the server. It manages the TCP connection, runs a background receiver thread, and routes every incoming server message to an `on_*` handler that the GUI layer overrides.

**Current scope (Phase 2 scaffold):** connect, join, disconnect, receive loop, dispatch routing, stubs for all handlers, `send_answer`.

**Praneet's work (Task 4):** wire the `on_*` handlers to `player_gui.py`.

---

## Usage pattern

```python
from client import GameClient

client = GameClient()
client.connect("192.168.1.10", 5000)   # opens TCP socket, starts recv thread
client.join("A3BX", "Alice")           # sends join — response arrives via on_lobby_update or on_error

# Later, when the player picks an answer:
client.send_answer(question_index=0, choice=2)

client.disconnect()
```

Responses from the server are **asynchronous** — `join()` and `send_answer()` return immediately. The actual server response arrives on the background receiver thread and is routed to the appropriate `on_*` handler.

---

## Public API

### `connect(host, port) → None`

Opens a TCP socket to the server and starts the background `_recv_loop` thread.

```python
client.connect("192.168.1.10", 5000)
```

Raises `OSError` if the connection is refused or times out — show an error in the GUI and let the player retry.

---

### `join(session_code, display_name) → None`

Sends a `join` message. The server responds asynchronously:

- **Success** → `on_lobby_update` fires with the current player list
- **Failure** → `on_error` fires with a human-readable reason; the connection stays open so the player can retry without reconnecting

Raises `RuntimeError` if called before `connect()`.

---

### `disconnect() → None`

Closes the TCP socket. The background recv thread exits cleanly on the next read attempt.

---

### `send_answer(question_index, choice) → None`

Sends the player's answer for the current question. No-op if not connected (safe to call without checking).

```python
client.send_answer(question_index=msg["index"], choice=2)
```

Disable answer buttons after calling this to prevent double-submit.

---

## Event handlers — what Praneet implements

All `on_*` methods are stubs that do nothing. Override them on a `GameClient` instance or subclass to wire events to the GUI.

**Critical threading rule:** all `on_*` methods are called from the background TCP receiver thread. Never update Tkinter widgets directly inside them — always schedule via `widget.after(0, fn)`.

```python
# Pattern to follow in every handler:
def on_lobby_update(self, players, host_started_countdown, countdown_remaining):
    self._root.after(0, lambda: self._gui.update_lobby(players, ...))
```

---

### `on_lobby_update(players, host_started_countdown, countdown_remaining)`

Called whenever the lobby state changes — player joins, leaves, or host toggles the countdown.

| Arg | Type | Description |
|---|---|---|
| `players` | `list[str]` | Current display names in the lobby |
| `host_started_countdown` | `bool` | `True` when host has triggered auto-start |
| `countdown_remaining` | `float \| None` | Seconds left, or `None` if no countdown active |

**Task 4 / Task 10:** refresh the player list in the lobby screen. Show the countdown timer if `host_started_countdown` is `True`.

---

### `on_game_start()`

Server signals the game is starting. The server immediately follows with a `question` message — transition to a "loading" state and wait.

**Task 11:** switch from the lobby frame to the question frame.

---

### `on_question(msg)`

A new question has arrived.

| `msg` key | Type | Description |
|---|---|---|
| `"index"` | `int` | 0-based question number — store this for `send_answer` |
| `"text"` | `str` | Question text to display |
| `"question_type"` | `str` | `"multiple_choice"` (4 options) or `"true_false"` (2 options) |
| `"options"` | `list[str]` | Answer option strings |
| `"time_limit"` | `int` | Seconds for this question (UDP timer is authoritative) |

**Task 11:** render question text and answer buttons. The UDP timer tick drives the visual countdown — do not start a local timer here.

---

### `on_question_result(msg)`

Post-question results after the timer expires or all players have answered.

| `msg` key | Type | Description |
|---|---|---|
| `"correct_answer"` | `int` | 0-based index into `options` |
| `"your_score"` | `int` | Points earned this question |
| `"your_total"` | `int` | Cumulative score |
| `"your_streak"` | `int` | Consecutive correct answers |
| `"leaderboard"` | `list` | `[{"name": str, "score": int}, ...]` — cumulative totals |
| `"show_correct_answer"` | `bool` | If `False`, do not reveal the correct option |
| `"show_leaderboard"` | `bool` | If `False`, skip the leaderboard display |
| `"pause_duration"` | `int` | Seconds to show this screen before the next message |

**Task 12:** render the results screen. Respect both `show_*` flags.

---

### `on_game_over(msg)`

Final summary after the last question result.

| `msg` key | Type | Description |
|---|---|---|
| `"final_rankings"` | `list` | `[{"rank": int, "name": str, "score": int, "correct": int, "streak": int, "fastest_answer": float}, ...]` sorted by rank. `streak` is the player's **longest** consecutive correct run across the whole game, not the streak at the final question. |
| `"total_questions"` | `int` | Use for "correct: X / total" display |

**Task 13:** render the game-over screen with final rankings and per-player stats.

---

### `on_player_disconnected(display_name)`

Another player dropped during an active game. The game continues without them.

**Task 11 / Phase 6:** show a brief notification, e.g. `"Charlie disconnected"`.

---

### `on_error(message)`

The server rejected the last request. The connection is still open — the player can retry.

Common messages: `"Invalid session code"`, `"Display name cannot be empty"`, `"Name 'Alice' is already taken"`, `"Game is full"`.

**Task 4 / Task 9:** display the error on the join screen so the player can correct their input.

---

### `on_disconnect()`

The server closed the connection unexpectedly (crash, shutdown, network drop).

**Task 4 / Phase 6:** show a disconnection notice and offer a reconnect option.

---

## Threading model

```
Main thread (Tkinter event loop)
  └─ client.connect()  →  starts TCP recv thread (daemon)
                               └─ _recv_loop()
                                    └─ _dispatch()
                                         └─ on_*() handlers
                                              └─ widget.after(0, fn)  →  back to main thread
```

The recv thread owns all reads. The main thread owns all Tkinter updates. GUI state flows only through `widget.after(0, fn)` — never touch Tkinter widgets directly from the recv thread. Note that `_connected` and `_sock` are shared between the two threads (`disconnect()` writes them from the main thread while the recv thread reads them), but no locking is needed here because `disconnect()` only ever closes the socket, which causes the recv thread to exit on its next read.

---

## What comes next

| Phase | Task | What gets added |
|---|---|---|
| Phase 2 | Task 4 | `on_lobby_update`, `on_error`, `on_disconnect` wired to GUI |
| Phase 4 | Task 6 | New UDP listener thread; add `on_timer_tick`, `on_answer_count` handlers and periodic `send_ping` — none of these exist yet |
| Phase 5 | Task 11 | `on_game_start`, `on_question` wired; `send_answer` called from answer buttons |
| Phase 5 | Task 12 | `on_question_result` wired to results screen |
| Phase 5 | Task 13 | `on_game_over` wired to game-over screen |
| Phase 6 | — | `on_player_disconnected` notification; reconnect logic in `on_disconnect` |
