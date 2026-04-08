# CMPT 371 A3 Socket programming `BrainZap`

**Course:** CMPT 371 \- Data Communications & Networking

**Instructor:** Mirza Zaeem Baig

**Semester:** Spring 2026

<span style="color: purple;">***RUBRIC NOTE: As per submission guidelines, only one group member will submit the link to this repository on Canvas.***

| Name | Student ID | Email | GitHub Username |
| :---- | :---- | :---- | :---- |
| Praneet Kaur | 301575802 | pkb19@sfu.ca | [praneetkb](https://github.com/praneetkb) |
| Sanchit Jain | 301575896 | sja164@sfu.ca | [Undiluted7027](https://github.com/Undiluted7027) |

---

## **1\. Project Overview & Description**

BrainZap is a real-time multiplayer trivia quiz game inspired by Kahoot, built entirely with Python standard library modules (no external runtime dependencies). A **host** launches a game server, loads a JSON quiz file, and shares a session code with players. **Players** join through a Tkinter GUI by entering the server IP, port, session code, and a display name. Once two or more players are in the lobby the host can start the game.

Questions are delivered one at a time over TCP. Each question has a countdown timer. Players earn points based on correctness and speed — faster correct answers score higher (up to 1000 pts, minimum 500 pts for any correct answer). A streak bonus applies for consecutive correct answers. Between questions the correct answer is revealed and a live leaderboard is shown (both configurable per-quiz). At the end, a final rankings screen displays scores, correct-answer counts, and the fastest individual answer time.

**Architecture:** Client-server over TCP + UDP. TCP carries all reliable game logic messages (join, questions, answers, scores, game-over). UDP carries high-frequency timer ticks (~10/s) and live answer-count indicators, plus ping/pong for latency measurement.

**Disconnection handling:** If a player disconnects mid-game, they are removed from the active player list and remaining players receive a `PLAYER_DISCONNECTED` notification. The game continues without them and they are still included in the final rankings with their accumulated score.

---

## **2\. System Limitations & Edge Cases**

- **Single server, no redundancy:** If the server process crashes, the session is lost. No state persistence or recovery mechanism is implemented.
- **No mid-game joins:** Players who miss the lobby phase cannot participate. A disconnected player cannot rejoin with their previous score.
- **10-player cap:** One thread per client; works well at 10 players but is not designed to scale further.
- **UDP timer sync is best-effort:** Lost UDP packets may cause a client's countdown display to stutter. The server enforces the real deadline regardless — an answer submitted after the server's timer expires is silently discarded, even if the client's display still shows time remaining.
- **LAN-oriented:** Players need the server's LAN IP. No NAT traversal or relay is implemented; the game does not work over the public internet without port forwarding.
- **Quiz file errors at load time:** Validation runs on `load_quiz()`. A malformed file (wrong option count, bad answer index, unknown question type) raises a `ValueError` with a descriptive message before any network activity begins.
- **Text-only questions:** No images, audio, or video in questions.
- **No spectator mode:** Every connected client is a player. There is no read-only observer role.
- **Clock skew:** Time-based scoring uses `time.monotonic()` on the server. No client-side timestamps are trusted, eliminating a trivial cheating vector, but the scoring will still slightly favour players on faster connections.

---

## **3\. Video Demo**

<span style="color: purple;">***RUBRIC NOTE: Include a clickable link.***</span>

Our 2-minute video demonstration covering connection establishment, data exchange, real-time gameplay, and process termination can be viewed below:
[**▶️ Watch Project Demo on Loom**](https://www.loom.com/share/27f93922e8a844c891e50ad286e380cf)

Direct Link: https://www.loom.com/share/27f93922e8a844c891e50ad286e380cf

---

## **4\. Prerequisites (Fresh Environment)**

- **OS:** macOS, Linux, or WSL2 on Windows — any OS with `tkinter` support
- **Python:** 3.10 or higher (`python3 --version`)
- **Tkinter:** Ships with the standard Python installer on macOS and Windows. On Linux it may need a separate package (the setup script will warn you if it is missing).
- **No runtime pip packages required.** All game code uses the Python standard library.
- `pytest`, `ruff`, and `mypy` are installed by `setup.sh` for development/testing only.

---

## **5\. Step-by-Step Run Guide**

<span style="color: purple;">***RUBRIC NOTE: The grader must be able to copy-paste these commands.***</span>

### **Step 1 — Set up the environment (once)**

```bash
# macOS / Linux / WSL2
chmod +x setup.sh
./setup.sh
source .venv/bin/activate
```

> [!IMPORTANT]
> If the script warns that `tkinter` is missing, install it before continuing:
> - **Debian/Ubuntu:** `sudo apt-get install python3-tk`
> - **Fedora/RHEL:** `sudo dnf install python3-tkinter`
> - **macOS (Homebrew):** `brew install python-tk`
> - See the [tkinter docs](https://tkdocs.com/tutorial/install.html) for other platforms.

### **Step 2 — Launch the Host GUI**

Open a terminal window and run:

```bash
# With no arguments — load the quiz file from inside the GUI
python -m src.main host

# Or pre-load a quiz file directly from the command line (skips Browse/Load steps below)
python -m src.main host --quiz quizzes/sample.json
```

**If you used `--quiz`:** the quiz is already loaded when the window opens — skip to step 3.

**If you launched with no arguments:**
1. Click **Browse…** and select a quiz JSON file.
2. Click **Load Quiz**.

**Then for everyone:**

3. Click **Launch Server** (leave Bind host and Port at defaults for local play).
4. Note the **session code** displayed in the Lobby panel — share it with players.

### **Step 3 — Connect Players (repeat for each player)**

Open a new terminal window for each player:

```bash
python -m src.playerGUI
```

In the Player GUI:
1. Enter the **server IP** (use `127.0.0.1` for local play, or the host's LAN IP for network play). Default is local.
2. Enter the **port** (default `5001`).
3. Enter the **session code** shown on the host screen.
4. Enter a **display name** and click **Join**.

### **Step 4 — Start the Game**

Once **2 or more players** have joined, the **▶ Start Game** button becomes active on the host screen. Click it to begin.

- Players see each question with a countdown timer and coloured answer buttons.
- The host sees a live presenter view with the current question, countdown, and how many players have answered.
- After each question, both host and players see the correct answer and leaderboard.
- After all questions, a final rankings screen is shown to all players.

### **Step 5 — Run tests (optional)**

```bash
make check      # ruff lint + mypy type check + pytest
make test       # pytest only
make lint       # ruff only
make typecheck  # mypy only
```

---

## **6\. Technical Protocol Details**

All messages are JSON objects serialised with `json.dumps`. TCP messages are framed with a **4-byte big-endian length prefix** (implemented in `src/protocol.py`). UDP messages are sent as raw JSON-encoded datagrams (no length prefix needed — each datagram is one complete message).

> **Port sharing:** TCP and UDP both bind to the **same port number** (default `5001`). The OS demultiplexes them by transport protocol, so no separate port is needed for UDP.

### Message Types

#### Client → Server (TCP)

| Message | Fields | Description |
|---------|--------|-------------|
| `join` | `session_code`, `display_name` | Request to join the lobby |
| `answer` | `question_index`, `choice` | Submit answer (0-based index into options array) |

#### Server → Client (TCP)

| Message | Fields | Description |
|---------|--------|-------------|
| `lobby_update` | `players`, `host_started_countdown`, `countdown_remaining` | Lobby state pushed to all clients after any join/leave |
| `game_start` | — | Signals game is beginning |
| `question` | `index`, `text`, `question_type`, `options`, `time_limit` | One question broadcast to all |
| `question_result` | `correct_answer`, `your_score`, `your_total`, `your_streak`, `leaderboard`, `show_correct_answer`, `show_leaderboard`, `pause_duration` | Per-client result after question closes |
| `game_over` | `final_rankings`, `total_questions` | Final rankings at end of game |
| `player_disconnected` | `display_name` | Broadcast when a player's TCP connection drops |
| `error` | `message` | Sent to a client when their request is rejected (e.g. bad session code, duplicate name) |

#### Server → Client (UDP)

| Message | Fields | Description |
|---------|--------|-------------|
| `timer_tick` | `question_index`, `remaining` | Broadcast ~10×/s during a question |
| `answer_count` | `question_index`, `answered`, `total` | Live count of how many players have submitted |

#### Client ↔ Server (UDP)

| Message | Fields | Description |
|---------|--------|-------------|
| `ping` | `timestamp` | Client sends periodically for RTT measurement |
| `pong` | `timestamp`, `server_time` | Server replies immediately |

### Scoring Formula

```
base  = max(500, 1000 × (time_remaining / time_limit))
score = floor(base × streak_multiplier)
```

- Correct answer answered instantly → **1000 pts** (before streak multiplier)
- Correct answer at the buzzer → **500 pts** floor (before streak multiplier)
- Wrong answer or no answer → **0 pts**, streak resets
- **Streak bonus:** streak 1 → ×1.0, streak 2 → ×1.1, streak 3+ → ×1.2 (capped)
- RTT measurement via UDP ping/pong is implemented but latency compensation in scoring is **not yet applied** (`rtt` is recorded server-side for a future task)

### Module Layout

| Module | Responsibility |
|--------|---------------|
| `src/main.py` | CLI entry point for the host |
| `src/playerGUI.py` | Player GUI (join, lobby, question, results, game-over screens) |
| `src/host_gui.py` | Host GUI (session setup, lobby, presenter view) |
| `src/server.py` | TCP server: accept connections, session management, broadcast |
| `src/serverGameLoop.py` | Game loop: question sequencing, answer collection, scoring, game-over |
| `src/client.py` | TCP client: connect, join, send answers, dispatch incoming messages |
| `src/clientTCP.py` | Low-level TCP client socket wrapper |
| `src/clientUDP.py` | UDP client: timer ticks, answer counts, ping/pong |
| `src/protocol.py` | Message type constants, TCP framing (`send_msg` / `recv_msg`), UDP helpers |
| `src/quiz.py` | Quiz JSON parser and validator, `Question` and `Quiz` models |
| `src/scoring.py` | Score calculation (`score_answer`), streak multiplier, leaderboard and final-rankings builders |

---

## **7\. Academic Integrity & References**

- **Code Origin:**
  - The socket boilerplate was adapted from the course tutorial "TCP Echo Server". The core multithreaded game logic, protocol, and state management were written by the group.
- **GenAI Usage:**
  - Codex was used for code refactors and planning.
  - Claude Sonnet 4.6 was used to assist with code review, debugging, and implementation of specific modules during development.
- **References:**
  - [Python Socket Programming HOWTO](https://docs.python.org/3/howto/sockets.html)
  - [Real Python: Intro to Python Threading](https://realpython.com/intro-to-python-threading/)
  - [Tkinter installation](https://tkdocs.com/tutorial/install.html)
