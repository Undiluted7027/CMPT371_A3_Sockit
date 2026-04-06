# Sockit: Trivia Quiz Game (Kahoot-Style)

## What is it?

A real-time multiplayer trivia quiz game inspired by Kahoot. A host launches a game server, loads a quiz file (JSON), and shares a session code with players. Players join via a Tkinter GUI, enter a display name, and wait in a lobby. Once the host starts the game, questions are presented one at a time with a countdown timer. Players earn points based on correctness and speed — faster correct answers score higher. Between questions, the correct answer is revealed and a live leaderboard is shown (configurable per quiz). At the end, a full results summary with final rankings and stats is displayed.

## Design

### Architecture: Client-Server

- A **dedicated game server** manages all game state: quiz progression, timer, scoring, player tracking.
- **Players are clients** that connect to the server, submit answers, and receive game state updates.
- The server generates a **session code** on startup. Players connect using the server's IP address and the session code (acts as a room password to prevent unauthorized joins).
- No separate signaling server is needed — the game server is the known endpoint.

### Protocol: TCP + UDP Hybrid

| Channel | Transport | Purpose |
|---------|-----------|---------|
| Game logic | TCP | Player join/leave, answer submission, question delivery, score updates, leaderboard data, lobby state, game start/end |
| Real-time sync | UDP | Timer countdown sync, "X of Y players answered" live counter, heartbeat/latency measurement |

- TCP handles everything where reliability matters — answers, scores, questions, and game flow control.
- UDP handles high-frequency, loss-tolerant updates — timer ticks broadcast every 100ms so all clients display a synchronized countdown, plus live answer-count indicators during each question.
- Latency measurement via UDP ping/pong allows the server to calculate each client's round-trip time, enabling **latency-compensated scoring** — a player on a slow connection isn't penalized for network delay.

### Game Flow

```
[Server starts, loads quiz file, generates session code]
        |
[Players connect via IP + session code, enter display name]
        |
[Lobby: player list updates in real-time]
[Host sees "Start Game" button (enabled at 2+ players)]
[Host can trigger optional auto-start countdown (30s), cancellable]
        |
[Game begins]
        |
   +---> [Question delivered to all clients via TCP]
   |     [Timer starts, synced via UDP ticks]
   |     [Live "X/Y answered" counter via UDP]
   |     [Players submit answers via TCP]
   |     [Timer expires or all players answered]
   |     |
   |     [Correct answer revealed (if configured)]
   |     [Leaderboard shown (if configured)]
   |     [Brief pause, then next question]
   |     |
   +-----+ (repeat for all questions)
        |
[Game over: final results screen]
[Final rankings, per-player stats, fastest answer, etc.]
```

### Scoring

Scoring mirrors Kahoot's model:
- **Correct answer:** Base points (1000) scaled by speed, with a guaranteed minimum of 500 points for any correct answer regardless of speed. The faster the answer, the higher the score.
- **Formula:** `score = max(min_points, base_points * (time_remaining / time_limit))` where `base_points = 1000` and `min_points = 500` — answering instantly gets full points, answering at the buzzer still earns 500.
- **Latency compensation:** The server measures each client's RTT via UDP pings. The server records its own receive time for each answer and subtracts half the client's RTT to estimate when the client actually pressed the button. Client-reported timestamps are not used — the server is the sole authority on timing, eliminating a cheating vector.
- **Incorrect answer:** 0 points.
- **No answer (timeout):** 0 points.
- **Streak bonus:** Consecutive correct answers earn a multiplier (e.g., 2 in a row = 1.1x, 3+ = 1.2x), capped to avoid runaway scores.

### Data Flow

1. **Server loads quiz** -> parses JSON, validates structure, stores questions in memory.
2. **Player joins** -> TCP connection established, sends session code + display name, server validates and adds to lobby.
3. **Game starts** -> server sends first question to all clients via TCP, starts authoritative timer.
4. **During question** -> server broadcasts timer ticks via UDP (~10/sec), clients render countdown. As players answer (TCP), server broadcasts updated answer count via UDP.
5. **Question ends** -> server calculates scores, sends results (correct answer, per-player scores, updated leaderboard) via TCP.
6. **Between questions** -> clients display results/leaderboard for a configured duration, then server sends next question.
7. **Game ends** -> server sends final results summary via TCP (rankings, stats, highlights).

## Requirements

### Functional
- Host launches server, loads a quiz file, receives a session code
- Players join using server IP + session code
- Players enter a display name upon joining (server rejects duplicates — player must choose a different name)
- Lobby shows connected players in real-time, host has start controls
- Questions displayed one at a time with a countdown timer
- Support multiple choice and true/false question types
- Time-based scoring (faster correct answers = more points)
- Streak bonus for consecutive correct answers
- Correct answer reveal after each question (configurable)
- Live leaderboard between questions (configurable)
- Live "X of Y players answered" indicator during questions
- End-of-game summary: final rankings, total scores, fastest answer, longest streak
- Graceful handling of player disconnection mid-game (removed from active players, game continues)

### Non-Functional
- Support up to 10 concurrent players per session
- Timer synchronized across all clients with minimal drift
- Latency-compensated scoring for fairness
- Responsive GUI that doesn't freeze during network operations

### Explicitly Out of Scope
- Player-submitted questions during a game
- Mid-game joins (lobby phase only)
- Multiple concurrent game sessions on one server
- Persistent leaderboards across games
- Audio/video/image-based questions
- Team mode
- In-game chat between players

## Implementation

### Tech Stack
- **Language:** Python 3
- **GUI:** Tkinter (separate windows/frames for lobby, question, leaderboard, results)
- **Networking:** Python `socket` module (raw TCP + UDP sockets)
- **Serialization:** JSON for all messages and the quiz file format
- **Threading:** `threading` module for concurrent network I/O alongside the GUI event loop
- **Time:** `time.monotonic()` for server-side timing (immune to wall-clock adjustments)

### Module Breakdown

| Module | Responsibility |
|--------|---------------|
| `main.py` | Entry point — launch as host (server + host GUI) or player (client GUI). The host is a non-playing presenter who controls the game flow; they do not answer questions. |
| `server.py` | Game server: accept connections, manage game state, run quiz loop, calculate scores, broadcast updates |
| `client.py` | Client networking: connect to server, send answers, receive game state, handle disconnect/reconnect |
| `host_gui.py` | Host GUI: lobby management, player list, start game button, auto-start countdown, game monitoring |
| `player_gui.py` | Player GUI: join screen (IP + code + name), lobby waiting room, question display, answer buttons, leaderboard, results |
| `protocol.py` | Message format definitions, serialization/deserialization, message types, TCP framing |
| `quiz.py` | Quiz file parser, validation, question model, quiz configuration |
| `scoring.py` | Score calculation, streak tracking, latency compensation, final stats generation |

### Quiz File Format

```json
{
  "title": "Geography Quiz",
  "description": "Test your knowledge of world capitals",
  "settings": {
    "default_time_limit": 20,
    "show_correct_answer": true,
    "show_leaderboard": true,
    "pause_between_questions": 5
  },
  "questions": [
    {
      "text": "What is the capital of France?",
      "type": "multiple_choice",
      "options": ["London", "Paris", "Berlin", "Madrid"],
      "answer": 1,
      "time_limit": 15
    },
    {
      "text": "The Great Wall of China is visible from space.",
      "type": "true_false",
      "options": ["True", "False"],
      "answer": 1,
      "time_limit": 10
    }
  ]
}
```

- Per-question `time_limit` overrides the global `default_time_limit`.
- `show_correct_answer` and `show_leaderboard` control post-question flow.
- `pause_between_questions` is the seconds spent on the results/leaderboard screen before advancing.
- **Unified answer format:** Both `multiple_choice` and `true_false` questions use an integer index into the `options` array as the `answer`. For true/false, `options` is always `["True", "False"]` and `answer` is `0` (True) or `1` (False). This keeps the client answer protocol uniform — `choice` is always an integer index.

### Message Protocol

All messages are JSON, TCP-framed with a 4-byte big-endian length prefix.

**Client -> Server (TCP):**
```json
{
  "type": "join",
  "session_code": "ABCD",
  "display_name": "Alice"
}
```
```json
{
  "type": "answer",
  "question_index": 0,
  "choice": 1
}
```

**Server -> Client (TCP):**
```json
{
  "type": "lobby_update",
  "players": ["Alice", "Bob", "Charlie"],
  "host_started_countdown": false,
  "countdown_remaining": null
}
```
```json
{
  "type": "game_start"
}
```
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
```json
{
  "type": "game_over",
  "final_rankings": [
    {"rank": 1, "name": "Alice", "score": 8500, "correct": 8, "streak": 5, "fastest_answer": 1.2}
  ],
  "total_questions": 10
}
```
```json
{
  "type": "player_disconnected",
  "display_name": "Charlie"
}
```
```json
{
  "type": "error",
  "message": "Invalid session code"
}
```

**Server -> Client (UDP):**
```json
{
  "type": "timer_tick",
  "question_index": 0,
  "remaining": 12.3
}
```
```json
{
  "type": "answer_count",
  "question_index": 0,
  "answered": 5,
  "total": 8
}
```

**Client <-> Server (UDP):**
```json
{
  "type": "ping",
  "timestamp": 1234567890.123
}
```
```json
{
  "type": "pong",
  "timestamp": 1234567890.123,
  "server_time": 1234567890.456
}
```

### Threading Model

**Server:**
- **Main thread:** Game loop — advances questions, manages timers, calculates scores
- **TCP acceptor thread:** Accepts new client connections
- **Per-client TCP thread:** Reads messages from each connected client
- **UDP listener thread:** Handles incoming pings, sends timer ticks and answer counts

**Client:**
- **Main thread:** Tkinter GUI event loop
- **TCP listener thread:** Receives game state updates from server
- **UDP listener thread:** Receives timer ticks and answer counts
- **UDP sender thread:** Sends periodic pings for latency measurement

## Tasks

1. **Quiz file parser & validator** — parse JSON quiz file, validate structure (required fields, valid answer indices, question types), expose quiz config and question list
2. **Protocol & serialization layer** — define all message types, implement JSON encode/decode, TCP length-prefix framing (4-byte big-endian)
3. **Server TCP networking** — accept client connections, handle join with session code validation, per-client read threads, broadcast to all clients
4. **Client TCP networking** — connect to server, send session code + display name, receive and dispatch incoming messages
5. **Server UDP networking** — bind UDP socket, handle pings, broadcast timer ticks and answer counts
6. **Client UDP networking** — receive timer ticks and answer counts, send periodic pings, calculate RTT
7. **Scoring engine** — implement time-based scoring formula, streak tracking, latency compensation, final stats aggregation
8. **Server game loop** — lobby state management, auto-start countdown, question sequencing, timer management, score calculation after each question, game-over detection
9. **Player GUI: join screen** — input fields for server IP, session code, display name; connect button; error display
10. **Player GUI: lobby** — waiting room showing connected players, countdown display
11. **Player GUI: question screen** — question text, answer buttons (4 for MC, 2 for T/F), countdown timer, "X/Y answered" indicator
12. **Player GUI: results screen** — correct answer highlight, score earned, streak indicator, leaderboard table
13. **Player GUI: game-over screen** — final rankings, personal stats, highlights (fastest answer, longest streak)
14. **Host GUI** — lobby player list, start game button (enabled at 2+ players), auto-start countdown trigger/cancel, game progress monitoring
15. **Disconnection handling** — detect client disconnect (TCP socket close or heartbeat timeout), remove from active players, notify remaining players, game continues
16. **Integration testing** — multi-player sessions, mid-question disconnect, timer sync verification, scoring correctness, edge cases (all wrong, all timeout, single player)

## Limitations

- **Single server, no redundancy:** If the server crashes, the game is lost. There is no state persistence or recovery mechanism.
- **No mid-game joins:** Players who miss the lobby phase cannot participate. If a player disconnects and reconnects, they rejoin as a new player with zero score (no state recovery).
- **Clock skew risk with scoring:** Time-based scoring relies on `time.monotonic()` on the server and client-reported timestamps. While latency compensation helps, a malicious client could send false timestamps to inflate scores. No anti-cheat is implemented.
- **UDP timer sync is best-effort:** If UDP packets are lost, the client's timer display may stutter or drift slightly from the server's authoritative timer. The server still enforces the real deadline — a client whose timer appears to have time left may find their answer rejected if the server's timer expired.
- **10-player cap:** The server uses a thread per client. At 10 players this is fine, but the architecture doesn't scale to hundreds. Acceptable for this scope.
- **No question media:** Questions are text-only. No support for images, audio, or video in questions — this limits quiz variety compared to real Kahoot.
- **Tkinter GUI constraints:** Tkinter has limited animation and styling capabilities. The GUI will be functional but won't match the polish of a web-based Kahoot experience. Custom widgets (countdown rings, animated scoreboards) are difficult to implement.
- **LAN-oriented:** Players need the server's IP address. Without port forwarding or a public IP, the game only works on a local network. No NAT traversal or relay is implemented.
- **No spectator mode:** Everyone in the session is a player. There's no way to observe without participating.
- **Quiz file errors at runtime:** If the quiz file has malformed questions discovered mid-game (e.g., answer index out of range), the server will skip the question rather than crash, but this degrades the experience. Validation at load time catches most issues but may not cover all edge cases.
