"""client.py — TCP (and later UDP) client for BrainZap Trivia.

CMPT 371 A3: BrainZap - Trivia Game
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

from __future__ import annotations

import logging
import socket
import threading
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox
from typing import TYPE_CHECKING

from .protocol import MsgType, recv_msg, send_msg

if TYPE_CHECKING:
    from src.playerGUI import PlayerGUI

logger = logging.getLogger(__name__)


class GameClient:
    """Manages the TCP connection from a player to the BrainZap game server.

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

    on_lobby_update: Callable[[list[str], bool, float | None], None]
    on_error: Callable[[str], None]
    on_disconnect: Callable[[], None]
    question_callback: Callable[[str, list[str], int, float], None]

    def __init__(self) -> None:
        """Instantiate a GameClient to manage the TCP connection."""
        self._sock: socket.socket | None = None
        self._connected = False
        self.gui: PlayerGUI | None = None  # Set by PlayerGUI after instantiation

        # Default callbacks — override after instantiation to hook into GUI

        def default_lobby_update(
            players: list[str],
            host_started_countdown: bool,
            countdown_remaining: float | None,
        ) -> None:
            print(
                f"[LOBBY UPDATE] players={players}, "
                f"host_started_countdown={host_started_countdown}, "
                f"countdown_remaining={countdown_remaining}"
            )

        def default_error(message: str) -> None:
            print(f"[ERROR] {message}")

        def default_disconnect() -> None:
            print("[DISCONNECTED] Connection to server lost")

        def default_question(
            question_text: str,
            options: list[str],
            question_index: int,
            time_limit: float,
        ) -> None:
            print(
                f"[QUESTION] {question_text} | options={options} | index={question_index} | time={time_limit}"
            )

        self.on_lobby_update: Callable[[list[str], bool, float | None], None] = (
            default_lobby_update
        )

        self.on_error: Callable[[str], None] = default_error
        self.on_disconnect: Callable[[], None] = default_disconnect
        self.question_callback: Callable[[str, list[str], int, float], None] = (
            default_question
        )

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
                msg["players"],
                msg["host_started_countdown"],
                msg["countdown_remaining"],
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
            self.on_error(msg["message"])
        else:
            logger.debug("Unknown message type: %r", t)

    # ------------------------------------------------------------------
    # Event handlers — override or replace to connect to the GUI
    # ------------------------------------------------------------------
    # IMPORTANT: all on_* methods run on the TCP receiver thread.
    # Never call Tkinter APIs directly here — schedule via widget.after(0, fn).

    def on_game_start(self) -> None:
        """Server signalled that the game is starting.

        The server will immediately follow this with a `question` message.
        Transition the GUI to a "loading question" state and wait.
        """
        if self.gui is None:
            return
        gui = self.gui

        def show_loading() -> None:
            # Clear lobby screen and show a "loading" state
            # The server will immediately follow with on_question
            gui.clear_frame()
            frame = tk.Frame(gui.root, padx=20, pady=20)
            frame.pack(fill="both", expand=True)
            gui.current_frame = frame
            tk.Label(
                frame,
                text="Game Starting...",
                font=("Arial", 18),
            ).pack(expand=True)

        gui.root.after(0, show_loading)

    def on_question(self, msg: dict) -> None:
        """Server delivered a new question."""
        if self.gui is None:
            return
        gui = self.gui

        question_text: str = msg.get("text", "")
        options: list[str] = msg.get("options", [])
        question_index: int = msg.get("index", 0)
        time_limit: float = float(msg.get("time_limit", 20))

        self.question_callback(question_text, options, question_index, time_limit)

        gui.root.after(
            0,
            lambda: gui.show_question_screen(
                question_text=question_text,
                options=options,
                question_index=question_index,
            ),
        )

    def on_question_result(self, msg: dict) -> None:
        """Server sent post-question results."""
        if self.gui is None:
            return
        gui = self.gui
        gui.root.after(
            0,
            lambda: gui.show_results_screen(
                question_text=msg.get("question_text", ""),
                options=msg.get("options", []),
                correct_index=msg.get("correct_answer", -1),
                your_score=msg.get("your_score", 0),
                your_total=msg.get("your_total_score", 0),
            ),
        )

    def on_game_over(self, msg: dict) -> None:
        """Server sent the final game-over summary."""
        if self.gui is None:
            return
        gui = self.gui
        gui.root.after(
            0,
            lambda: gui.show_game_over_screen(
                final_rankings=msg.get("final_rankings", []),
                total_questions=msg.get("total_questions", 0),
            ),
        )

    def on_player_disconnected(self, display_name: str) -> None:
        """Another player disconnected during an active game."""
        if self.gui is None:
            return
        gui = self.gui
        gui.root.after(
            0,
            lambda: messagebox.showinfo("Player Left", f"{display_name} disconnected."),
        )

    # ------------------------------------------------------------------
    # Outbound messages
    # ------------------------------------------------------------------

    def send_answer(self, question_index: int, choice: int) -> None:
        """Submit the player's answer for the current question.

        Args:
            question_index: The 0-based index of the current question.
                            Store this from the most recent on_question call.
            choice:         0-based index into the question's options array.

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
