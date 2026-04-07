"""client.py — TCP (and later UDP) client for Sockit Trivia.

CMPT 371 A3: Sockit - Trivia Game
Architecture: Client-Server, TCP + UDP hybrid
Reference:    reference/protocol-foundation.md
Ownership:    Sanchit (Phase 2: scaffold); Praneet (Phase 2 Task 4 + Phase 3+)

Phase 2 scaffold (Sanchit — this file)
---------------------------------------
- GameClient class with connect / join / disconnect
- _recv_loop background thread
- _dispatch routing all 8 server→client TCP message types
- Stub on_* handlers with precise TODO comments for Praneet
- send_answer stub

Phase 2 Task 4 (Praneet — complete the TODOs below)
----------------------------------------------------
- Wire on_lobby_update and on_error to player_gui.py join/lobby screens
- Wire on_disconnect to show a reconnect notice in the GUI
- Test end-to-end: connect to server, join lobby, see lobby_update arrive

Phase 3+ (Praneet)
------------------
- Wire on_game_start, on_question, on_question_result, on_game_over,
  on_player_disconnected to the remaining GUI screens (Tasks 11-13)
- Integrate send_answer() with the answer button in the question screen
- Add UDP: on_timer_tick, on_answer_count, send_ping (Task 6)

Threading model
---------------
  Main thread (Tkinter) : creates GameClient, calls connect() and join()
  TCP recv thread        : _recv_loop — blocks on recv_msg, dispatches to on_*
                           handlers; NEVER update Tkinter widgets directly here —
                           always use widget.after(0, fn) to schedule on main thread
"""

import logging
import socket
import threading

from src.protocol import MsgType, recv_msg, send_msg

logger = logging.getLogger(__name__)


class GameClient:
    """Manages the TCP connection from a player to the Sockit game server.

    Usage::

        client = GameClient()
        client.connect("192.168.1.10", 5000)
        client.join("ABCD", "Alice")
        # Lobby updates arrive via on_lobby_update()
        # ...
        client.disconnect()

    Override the on_* methods (or monkey-patch them after construction) to wire
    server events to the GUI.  All on_* methods are called from the background
    TCP receiver thread — use widget.after(0, fn) before touching any Tkinter
    widget.
    """

    def __init__(self) -> None:
        """Instantiate a GameClient to manage the TCP connection."""
        self._sock: socket.socket | None = None
        self._connected = False

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self, host: str, port: int) -> None:
        """Open a TCP connection to the server and start the receiver thread.

        Args:
            host: Server IP address or hostname.
            port: Server TCP port (default 5000).

        Raises:
            OSError: if the connection is refused or times out.

        """
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.connect((host, port))
        self._connected = True
        logger.debug("Connected to %s:%d", host, port)

        recv_thread = threading.Thread(
            target=self._recv_loop, daemon=True, name="client-tcp-recv"
        )
        recv_thread.start()

    def join(self, session_code: str, display_name: str) -> None:
        """Send a join request to the server.

        The server responds asynchronously:
          - on_lobby_update fires on success (client is now in the lobby)
          - on_error fires on failure (bad session code, duplicate name, full game)
            The connection stays open so the player can retry with different input.

        Args:
            session_code:  The 4-character room code displayed on the host screen.
            display_name:  The player's chosen name (must be unique in the lobby).

        Raises:
            RuntimeError: if connect() has not been called yet.

        """
        if not self._connected or self._sock is None:
            raise RuntimeError("Not connected — call connect() before join()")
        send_msg(
            self._sock,
            {
                "type": MsgType.JOIN,
                "session_code": session_code,
                "display_name": display_name,
            },
        )

    def disconnect(self) -> None:
        """Close the TCP connection."""
        self._connected = False
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    # ------------------------------------------------------------------
    # Receive loop (background thread — do not call directly)
    # ------------------------------------------------------------------

    def _recv_loop(self) -> None:
        """Read messages from the server until the connection closes.

        Runs entirely on the TCP receiver thread. Routes each message to the
        appropriate on_* handler. A ConnectionError means the server closed the
        connection; a ValueError means a malformed JSON frame arrived (log and
        continue — one bad frame is not fatal).
        """
        assert self._sock is not None
        while self._connected:
            try:
                msg = recv_msg(self._sock)
            except ConnectionError:
                logger.debug("Connection to server closed")
                self.on_disconnect()
                return
            except OSError:
                # Socket was closed by disconnect() from another thread.
                return
            except ValueError:
                logger.warning("Received malformed message from server — skipping")
                continue
            self._dispatch(msg)

    def _dispatch(self, msg: dict) -> None:
        """Route a received message to the correct on_* handler."""
        t = msg.get("type")

        if t == MsgType.LOBBY_UPDATE:
            self.on_lobby_update(
                players=msg["players"],
                host_started_countdown=msg["host_started_countdown"],
                countdown_remaining=msg["countdown_remaining"],
            )
        elif t == MsgType.GAME_START:
            self.on_game_start()
        elif t == MsgType.QUESTION:
            self.on_question(msg)
        elif t == MsgType.QUESTION_RESULT:
            self.on_question_result(msg)
        elif t == MsgType.GAME_OVER:
            self.on_game_over(msg)
        elif t == MsgType.PLAYER_DISCONNECTED:
            self.on_player_disconnected(display_name=msg["display_name"])
        elif t == MsgType.ERROR:
            self.on_error(message=msg["message"])
        else:
            logger.debug("Unknown message type: %r", t)

    # ------------------------------------------------------------------
    # Event handlers — override or replace to connect to the GUI
    # ------------------------------------------------------------------
    # IMPORTANT: all on_* methods run on the TCP receiver thread.
    # Never call Tkinter APIs directly here — schedule via widget.after(0, fn).

    def on_lobby_update(
        self,
        players: list,
        host_started_countdown: bool,
        countdown_remaining: float | None,
    ) -> None:
        """Server sent an updated lobby state.

        Args:
            players:                 Current list of joined display names.
            host_started_countdown:  True when the host has triggered auto-start.
            countdown_remaining:     Seconds left in the countdown (float), or None.

        TODO (Praneet — Task 4 / Task 10):
            Wire to the lobby screen in player_gui.py:
              - Refresh the player list widget with `players`
              - If host_started_countdown is True and countdown_remaining is not
                None, show the countdown timer; hide it otherwise
            Example:
                def on_lobby_update(self, players, host_started_countdown,
                                    countdown_remaining):
                    self._root.after(0, lambda: self._gui.update_lobby(
                        players, host_started_countdown, countdown_remaining))

        """

    def on_game_start(self) -> None:
        """Server signalled that the game is starting.

        The server will immediately follow this with a `question` message.
        Transition the GUI to a "loading question" state and wait.

        TODO (Praneet — Task 11):
            Transition player_gui.py from the lobby frame to the question frame.
        """

    def on_question(self, msg: dict) -> None:
        """Server delivered a new question.

        Args:
            msg: {
                "index":         int   — 0-based question number,
                "text":          str   — question text,
                "question_type": str   — "multiple_choice" or "true_false",
                "options":       list  — answer option strings,
                "time_limit":    int   — seconds for this question,
            }

        TODO (Praneet — Task 11):
            Render the question screen in player_gui.py:
              - Display question text and answer buttons (4 for MC, 2 for T/F)
              - Start the client-side countdown display (authoritative timer via UDP)
              - Store msg["index"] so send_answer() can reference it

        """

    def on_question_result(self, msg: dict) -> None:
        """Server sent post-question results.

        Args:
            msg: {
                "correct_answer":      int   — 0-based index into options,
                "your_score":          int   — points earned this question,
                "your_total":          int   — cumulative score,
                "your_streak":         int   — consecutive correct answers,
                "leaderboard":         list  — [{"name": str, "score": int}, ...],
                "show_correct_answer": bool,
                "show_leaderboard":    bool,
                "pause_duration":      int   — seconds to show this screen,
            }

        TODO (Praneet — Task 12):
            Render the results screen in player_gui.py. Respect show_correct_answer
            and show_leaderboard flags — do not display those elements if False.

        """

    def on_game_over(self, msg: dict) -> None:
        """Server sent the final game-over summary.

        Args:
            msg: {
                "final_rankings": list — [
                    {"rank": int, "name": str, "score": int,
                     "correct": int, "streak": int, "fastest_answer": float},
                    ...
                ],
                "total_questions": int,
            }

        TODO (Praneet — Task 13):
            Render the game-over screen in player_gui.py.
            Use total_questions to display "correct: X / total_questions".

        """

    def on_player_disconnected(self, display_name: str) -> None:
        """Another player disconnected during an active game.

        Args:
            display_name: The name of the player who left.

        TODO (Praneet — Task 11 / Phase 6):
            Show a brief notification on the question or results screen, e.g.
            "{display_name} disconnected".

        """

    def on_error(self, message: str) -> None:
        """Server rejected the last request.

        The connection stays open — the player can send another join request
        with corrected input without reconnecting.

        Args:
            message: Human-readable error string from the server, e.g.
                     "Invalid session code" or "Name 'Alice' is already taken".

        TODO (Praneet — Task 4 / Task 9):
            Show the error message on the join screen in player_gui.py so the
            player can correct their input and retry.

        """

    def on_disconnect(self) -> None:
        """Handle the server closing the connection unexpectedly.

        TODO (Praneet — Task 4 / Phase 6):
            Show a disconnection notice in player_gui.py and offer a reconnect
            button or auto-retry logic.
        """

    # ------------------------------------------------------------------
    # Outbound messages
    # ------------------------------------------------------------------

    def send_answer(self, question_index: int, choice: int) -> None:
        """Submit the player's answer for the current question.

        Args:
            question_index: The 0-based index of the current question.
                            Store this from the most recent on_question call.
            choice:         0-based index into the question's options array.

        TODO (Praneet — Task 11):
            Call this from the answer button handler in player_gui.py.
            Disable the answer buttons after sending to prevent double-submit.

        """
        if not self._connected or self._sock is None:
            return
        send_msg(
            self._sock,
            {
                "type": MsgType.ANSWER,
                "question_index": question_index,
                "choice": choice,
            },
        )
