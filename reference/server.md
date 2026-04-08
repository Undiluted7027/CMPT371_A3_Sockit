# `server.py` — TCP Game Server

> **See also:** [`protocol-foundation.md`](protocol-foundation.md) for wire format and message catalog.

---

## Overview

`server.py` implements the TCP server for one BrainZap game session. It handles everything from accepting raw connections to maintaining lobby state and broadcasting updates. Game logic (question sequencing, scoring) is not here yet — that arrives in Phase 4.

**Current scope (Phase 2 — Task 3):** session code, join handshake, lobby state, disconnect.

---

## Starting the server

```python
from server import GameServer

server = GameServer()          # binds to 0.0.0.0:5000 by default
server.start()                 # non-blocking — starts acceptor thread
print(server.session_code)     # e.g. "A3BX"
# ... main thread free to do other work (game loop in Phase 4) ...
server.stop()
```

Or run directly:

```bash
python src/server.py           # default port 5000
python src/server.py 6000      # custom port
```

`start()` is non-blocking. `self.port` is updated to the actual bound port after `bind()` — pass `port=0` to let the OS assign an ephemeral port (useful in tests).

---

## Public API

### `GameServer(host, port)`

| Parameter | Default | Description |
|---|---|---|
| `host` | `"0.0.0.0"` | Interface to listen on. Use `"127.0.0.1"` to restrict to loopback. |
| `port` | `5000` | TCP port. Pass `0` for OS-assigned ephemeral port. |

After construction, `server.session_code` holds the randomly generated 4-character join code (uppercase alphanumeric).

---

### `start() → None`

Binds the socket, starts the acceptor thread, returns immediately. Updates `self.port` to the actual bound port.

---

### `stop() → None`

Sets `_running = False`, then closes the listening socket. This stops the acceptor thread from accepting new connections. Per-client threads are **not** forcibly terminated — they keep running until their individual clients disconnect and `recv_msg` errors out.

---

### `get_players() → list[str]`

Returns a thread-safe snapshot of currently joined display names. Use this from the game loop to know who is in the game.

```python
players = server.get_players()   # ["Alice", "Bob", "Charlie"]
```

---

### `broadcast(msg: dict) → None`

Sends `msg` to every joined client. Thread-safe — takes a snapshot of `_joined` before releasing the lock, then does I/O outside the critical section so a slow socket cannot stall others.

Dead sockets raise `OSError` which is silently swallowed — the per-client thread handles cleanup on its next `recv`.

```python
# Phase 4 usage (game loop):
server.broadcast({"type": MsgType.QUESTION, "index": 0, ...})
server.broadcast({"type": MsgType.GAME_START})
```

---

### `send_to(display_name: str, msg: dict) → None`

Sends `msg` to one specific player. Silent no-op if the player is not in the lobby.

```python
# Phase 4 usage (personalised question_result per player):
server.send_to("Alice", {"type": MsgType.QUESTION_RESULT, "your_score": 850, ...})
```

---

## Threading model

```
Main thread
  └─ server.start()
       └─ Acceptor thread (daemon)  ← _accept_loop()
            └─ Per-client thread (daemon, one per connection)  ← _client_thread()
```

- **Acceptor thread** — calls `accept()` in a loop, hands each socket to a new per-client thread.
- **Per-client thread** — reads messages from one client for its entire lifetime. Calls `_remove_client` in a `finally` block so cleanup always runs.
- **Main thread** — free to run the game loop (Phase 4). Calls `broadcast()` and `send_to()` which are thread-safe.

---

## Lock discipline

Two levels of locking keep concurrent access safe:

| Lock | Scope | Protects |
|---|---|---|
| `_joined_lock` | `GameServer` | The `_joined` dict — all reads and writes |
| `client.send_lock` | `_ClientConn` | One client's socket send path |

**Rule for `_joined_lock`:** never hold it while doing I/O. Always take a snapshot inside the lock, release the lock, then iterate and send. This prevents a dead or slow socket from blocking all other threads.

```python
# Illustrative pattern — actual code uses contextlib.suppress(OSError)
with self._joined_lock:
    snapshot = list(self._joined.values())   # snapshot taken
# lock released — I/O happens here
for client in snapshot:
    with contextlib.suppress(OSError):
        send_msg(client.sock, msg, lock=client.send_lock)
```

---

## Join handshake

When a client connects and sends a `join` message, the server validates in order:

1. `session_code` must match `server.session_code`
2. `display_name` must be non-empty after stripping whitespace
3. Lobby must have fewer than `MAX_PLAYERS` (10) clients
4. `display_name` must be unique among currently joined players

Steps 3 and 4 are checked atomically under `_joined_lock` so two simultaneous joins with the same name cannot both pass.

**On failure** — server sends `error` and keeps the connection open. The client can send another `join` on the same socket (e.g. with a different name). No reconnect needed.

**On success** — client is registered in `_joined` and `lobby_update` is broadcast to all joined clients including the new joiner.

---

## Disconnect handling (lobby phase)

When a per-client thread's read loop exits — either `recv_msg` raises `ConnectionError` (clean disconnect) or `ValueError` (malformed frame, connection closed as a result) — the `finally` block always calls `_remove_client`:

1. Closes the socket.
2. Removes the client from `_joined` (if they had joined).
3. Broadcasts `lobby_update` to remaining clients with the updated player list.

Clients that connected but never successfully joined (e.g. wrong code) are cleaned up silently — no broadcast is sent.

---

## What Phase 4 will add

The following hooks are already stubbed and ready for Phase 4:

| Hook | Used by |
|---|---|
| `broadcast()` | Game loop to send `QUESTION`, `GAME_START`, `GAME_OVER` |
| `send_to()` | Game loop to send personalised `QUESTION_RESULT` |
| `get_players()` | Game loop to iterate over active players |
| `_dispatch()` | Will gain an `ANSWER` branch for scoring |
| `_broadcast_lobby_update()` | Will set `host_started_countdown` / `countdown_remaining` |
