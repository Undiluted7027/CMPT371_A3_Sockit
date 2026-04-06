# Trivia Quiz Game — Phased Implementation Plan

## Task Assignment

| Person | Tasks |
|---|---|
| **Praneet** | 1 (Quiz parser), 4 (Client TCP), 6 (Client UDP), 8 (Server game loop), 9 (Player GUI: join), 10 (Player GUI: lobby), 11 (Player GUI: question), 12 (Player GUI: results), 13 (Player GUI: game-over), 16 (Integration testing) |
| **Sanchit** | 2 (Protocol), 3 (Server TCP), 5 (Server UDP), 7 (Scoring engine), 14 (Host GUI), 15 (Disconnection handling), 16 (Integration testing) |

---

## Phase 1 — Protocol Foundation *(both, together)*

| Praneet | Sanchit |
|---|---|
| Review & agree on protocol | **Task 2** — Define all message types, JSON structure, TCP length-prefix framing |

Sanchit drives this. Praneet must review and sign off before either side starts networking work. This is the shared contract everything else depends on.

---

## Phase 2 — Networking Skeleton *(parallel)*

| Praneet | Sanchit |
|---|---|
| **Task 4** — Client TCP (connect to server, send/receive messages, basic dispatch) | **Task 3** — Server TCP (accept connections, session code validation, join/leave handshake) |

Goal: by end of this phase, a client can connect to the server and join a lobby. No game logic yet — just the connection layer working end-to-end.

---

## Phase 3 — Core Logic *(parallel)*

| Praneet | Sanchit |
|---|---|
| **Task 1** — Quiz file parser & validator | **Task 7** — Scoring engine (time-based formula, streak tracking, latency compensation) |
| **Task 9** — Player GUI: join screen | |
| **Task 10** — Player GUI: lobby | |

Praneet can build the parser in isolation and begin GUI screens against mock/hardcoded data — no live server needed yet. Sanchit builds and unit-tests the scoring engine independently.

---

## Phase 4 — Game Loop + UDP *(parallel)*

| Praneet | Sanchit |
|---|---|
| **Task 8** — Server game loop (lobby → questions → scoring → results) | **Task 5** — Server UDP (timer ticks, answer counts, ping/pong) |
| **Task 6** — Client UDP (receive ticks, send pings, RTT calc) | **Task 14** — Host GUI (lobby control, start button, game monitoring) |

> **Note:** Praneet cannot start Task 8 until Sanchit has completed Tasks 2, 3, and 7. Praneet should use any wait time to advance Tasks 6, 9, and 10.

By end of this phase, a full game should be playable end-to-end — connect, start, answer questions, see scores.

---

## Phase 5 — Remaining Player GUI *(Praneet)*

- **Task 11** — Player GUI: question screen (question text, answer buttons, countdown timer, X/Y answered indicator)
- **Task 12** — Player GUI: results screen (correct answer highlight, score earned, streak, leaderboard)
- **Task 13** — Player GUI: game-over screen (final rankings, personal stats, fastest answer, longest streak)

Wire these up in game-flow order. Each screen feeds into the next.

---

## Phase 6 — Hardening *(parallel)*

| Praneet | Sanchit |
|---|---|
| Wire all GUI screens to live server, fix layout/UX issues | **Task 15** — Disconnection handling (TCP drop detection, player removal, game continues) |

---

## Phase 7 — Integration & Polish *(both)*

**Task 16 — Integration testing**

Run multi-player sessions together. Test: mid-question disconnect, timer sync across clients, scoring correctness, edge cases (all wrong, all timeout, single player remaining, host-side monitoring during play). Fix bugs found here.

---

## Summary Timeline

```
Phase 1:  [Protocol] ← Sanchit drives, Praneet reviews
Phase 2:  [Server TCP (P2)] || [Client TCP (P1)]
Phase 3:  [Scoring engine (P2)] || [Quiz parser + Join/Lobby GUI (P1)]
Phase 4:  [Server UDP + Host GUI (P2)] || [Game loop + Client UDP (P1)*]
Phase 5:  [Question/Results/GameOver GUI (P1)]
Phase 6:  [Disconnection handling (P2)] || [GUI wiring + polish (P1)]
Phase 7:  [Integration testing (both)]

* Praneet's Task 8 (game loop) is blocked on P2 finishing Tasks 2, 3, 7 first.
```

The critical path is **2 → 3/4 → 7 → 8** — protocol, TCP layer, scoring engine, and game loop must all be complete before a full end-to-end game run is possible.
