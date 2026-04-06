# Using `protocol.py`

> **See also:** [`protocol-foundation.md`](protocol-foundation.md) — the canonical spec this
> module implements. It defines the wire format, full message catalog, and threading model.
> When in doubt about a field name or message structure, that document is the authority.

This document is a practical guide for every module that imports from `protocol.py`.
It covers what the module gives you, how to use each piece, and the rules you must
follow to keep the protocol working correctly.

---

## What `protocol.py` provides

| Export | Kind | Purpose |
|---|---|---|
| `MsgType` | class (constants) | All valid `"type"` string values |
| `send_msg` | function | Serialize a dict and send it over TCP |
| `recv_msg` | function | Read one framed message from a TCP socket |
| `send_udp` | function | Serialize a dict and send it as a UDP datagram |
| `recv_udp` | function | Receive one UDP datagram and parse it |
| `UdpSendQueue` | class | Thread-safe queue that routes UDP sends through one thread |

Import what you need:

```python
from protocol import MsgType, send_msg, recv_msg, send_udp, recv_udp, UdpSendQueue
```

---

## 1. `MsgType` — use constants, never raw strings

Every message has a `"type"` field. Always use `MsgType` constants instead of
writing the string directly.

```python
# Good
msg = {"type": MsgType.QUESTION, "index": 0, ...}

# Bad — typos fail silently at runtime
msg = {"type": "queston", "index": 0, ...}
```

When you receive a message, switch on the `"type"` field using the same constants:

```python
msg = recv_msg(sock)
t = msg["type"]

if t == MsgType.JOIN:
    handle_join(msg)
elif t == MsgType.ANSWER:
    handle_answer(msg)
elif t == MsgType.ERROR:
    handle_error(msg)
```

---

## 2. `send_msg` — sending TCP messages

```python
send_msg(sock, msg)               # no lock (client side, single writer thread)
send_msg(sock, msg, lock=lock)    # with lock (server side, multiple writer threads)
```

`send_msg` serializes `msg` to JSON, prepends a 4-byte big-endian length, and
calls `sendall` so the entire frame goes out atomically.

**When to pass a lock:**
On the server, multiple threads write to the same client socket (the game loop
broadcasts questions; a disconnect handler sends errors). Concurrent `sendall`
calls corrupt the byte stream. Every client socket on the server must have a
paired `threading.Lock` passed to every `send_msg` call for that socket.

```python
# Server setup — once per accepted connection
client_lock = threading.Lock()

# Any thread that writes to client_sock:
send_msg(client_sock, {"type": MsgType.QUESTION, ...}, lock=client_lock)
send_msg(client_sock, {"type": MsgType.ERROR, ...},    lock=client_lock)
```

On the client, a single thread writes to the server socket, so `lock=None`
(the default) is fine.

**Error handling:**
`send_msg` raises `OSError` (including `BrokenPipeError`, `ConnectionResetError`)
if the socket is broken. Let it propagate to the caller — the per-client thread
or disconnect handler is responsible for cleanup.

```python
try:
    send_msg(client_sock, msg, lock=client_lock)
except OSError:
    remove_client(client_id)
```

---

## 3. `recv_msg` — receiving TCP messages

```python
msg = recv_msg(sock)   # blocks until one complete message arrives
```

`recv_msg` reads the 4-byte length prefix, then reads exactly that many bytes,
and returns the parsed dict. It blocks — call it from a dedicated reader thread,
not the GUI thread or the game loop.

**Disconnect detection:**

```python
# Per-client reader thread pattern
def client_reader(sock, client_id):
    while True:
        try:
            msg = recv_msg(sock)
        except ConnectionError:
            # Remote end closed the connection
            handle_disconnect(client_id)
            return
        except ValueError:
            # Received bytes that aren't valid JSON — protocol error
            log_error(client_id, "malformed message")
            return
        dispatch(msg)
```

`recv_msg` raises `ConnectionError` rather than returning `None` so every
reader thread handles disconnect the same way — one `except` clause, no
`if msg is None` checks scattered everywhere.

---

## 4. `send_udp` / `recv_udp` — UDP messages

UDP is datagram-based — no framing needed. Each call is one complete message.

```python
# Send
send_udp(sock, {"type": MsgType.TIMER_TICK, "question_index": 0, "remaining": 9.8}, addr)

# Receive — returns (dict, sender_addr)
msg, sender_addr = recv_udp(sock)
```

**Rules:**
- Keep all UDP messages well under 1400 bytes (all messages in this project are
  under 100 bytes, so this is not a concern).
- `recv_udp` raises `ValueError` on malformed JSON. Log and continue — one bad
  datagram is not a reason to kill the thread.
- UDP is lossy. Never send anything over UDP that the game cannot tolerate
  losing. Timer ticks and answer counts are fine — they are sent repeatedly, so
  a lost packet goes unnoticed. Questions, scores, and answers go over TCP.

---

## 5. `UdpSendQueue` — routing UDP sends through one thread

The threading model requires all UDP sends to go through the UDP listener thread.
Other threads (game loop, per-client TCP threads) must not call `send_udp` directly
— they enqueue work instead.

```python
# Created once, shared across threads
udp_queue = UdpSendQueue()
```

**Producer side** (game loop thread, per-client TCP thread):

```python
# Broadcast a timer tick to one client
udp_queue.put(
    {"type": MsgType.TIMER_TICK, "question_index": idx, "remaining": t},
    (client_ip, client_udp_port),
)
```

**Consumer side** (UDP listener thread — the only thread that calls `send_udp`):

```python
def udp_thread(udp_sock, udp_queue):
    while True:
        try:
            msg, addr = udp_queue.get(timeout=0.1)
            send_udp(udp_sock, msg, addr)
        except queue.Empty:
            continue   # nothing queued, loop back and also check for incoming pings
```

**Why:** keeping all UDP I/O on one thread eliminates ordering issues between
timer ticks, pong replies, and answer counts, and avoids any question of whether
`sendto` for small datagrams is atomic on the target OS.

---

## 6. Common mistakes to avoid

| Mistake | Consequence | Fix |
|---|---|---|
| Calling `recv_msg` from the GUI thread | GUI freezes while waiting for data | Always call `recv_msg` from a background reader thread; dispatch to GUI via `widget.after(0, fn)` |
| Calling `send_msg` on a server socket without a lock | Concurrent writes corrupt the TCP stream | Create one `threading.Lock` per client socket and always pass it |
| Calling `send_udp` from the game loop thread directly | Race conditions with pong replies | Enqueue via `UdpSendQueue` instead |
| Swallowing `ConnectionError` from `recv_msg` | Client appears alive after disconnect | Re-raise or call the disconnect handler |
| Using raw strings instead of `MsgType` constants | Typos silently send wrong type values | Always use `MsgType.X` |
| Sending game state (questions, scores) over UDP | Message may never arrive | TCP only for anything that must be delivered |
