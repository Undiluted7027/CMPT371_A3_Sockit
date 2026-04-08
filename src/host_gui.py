"""Tkinter host GUI for launching and monitoring a Sockit game session."""

from __future__ import annotations

import contextlib
import socket
import threading
import tkinter as tk
from tkinter import filedialog
from typing import TYPE_CHECKING

from .quiz import Quiz, load_quiz
from .server import DEFAULT_PORT, GameServer
from .serverGameLoop import GameLoop, HostObserver

if TYPE_CHECKING:
    from collections.abc import Callable

# ---------------------------------------------------------------------------
# Visual theme
# ---------------------------------------------------------------------------

_BG = "#f5f5f7"  # window background (light grey)
_CARD = "#ffffff"  # section card background
_PURPLE = "#46178f"  # Kahoot-style primary accent
_GREEN = "#26890c"  # Start Game button (active)
_BLUE = "#0071e3"  # Launch Server button
_TEXT = "#1d1d1f"  # primary text
_MUTED = "#6e6e73"  # secondary / label text
_DISABLED_BG = "#d1d1d6"
_DISABLED_FG = "#8e8e93"

_FONT = ("Helvetica Neue", 12)
_FONT_BOLD = ("Helvetica Neue", 12, "bold")
_FONT_SM = ("Helvetica Neue", 10)
_FONT_CODE = ("Courier New", 30, "bold")  # big session code


class HostGUI(HostObserver):
    """Host-side Tkinter window for session launch, control, and monitoring."""

    def __init__(
        self,
        root: tk.Tk,
        *,
        server: GameServer | None = None,
        quiz: Quiz | None = None,
        quiz_path: str | None = None,
        bind_host: str = "0.0.0.0",
        port: int = DEFAULT_PORT,
        game_loop_factory: type[GameLoop] = GameLoop,
    ) -> None:
        """Initialize the host GUI around an optional server and quiz."""
        self.root = root
        self.server = server
        self.quiz = quiz
        self.quiz_path = quiz_path
        self.bind_host = bind_host
        self.port = port
        self.game_loop_factory = game_loop_factory

        self._owns_server = server is None
        self._game_loop: GameLoop | None = None
        self._game_thread: threading.Thread | None = None
        self._game_started = False
        self._closed = False
        self._poll_after_id: str | None = None

        self.quiz_title_var = tk.StringVar(
            value=self.quiz.title if self.quiz is not None else "No quiz loaded"
        )
        self.quiz_path_var = tk.StringVar(value=self.quiz_path or "")
        self.session_code_var = tk.StringVar(
            value=self.server.session_code if self.server is not None else "Not started"
        )
        self.address_var = tk.StringVar(
            value=self._format_address()
            if self.server is not None
            else "Server offline"
        )
        self.status_var = tk.StringVar(value="Load a quiz and launch the server.")
        self.phase_var = tk.StringVar(value="Lobby")
        self.question_var = tk.StringVar(value="Waiting for game start")
        self.countdown_var = tk.StringVar(value="Countdown: --")
        self.answered_var = tk.StringVar(value="Answered: 0 / 0")
        self.result_var = tk.StringVar(value="")

        self.root.title("Sockit Host")
        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)

        self._build_ui()
        self._sync_server_labels()
        self.refresh_lobby()
        self._schedule_lobby_refresh()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Create the host window widgets."""
        self.root.configure(bg=_BG)
        container = tk.Frame(self.root, bg=_BG, padx=16, pady=16)
        container.pack(fill="both", expand=True)

        # ---- Session Setup ----
        launch = tk.LabelFrame(
            container,
            text="  Session Setup  ",
            bg=_CARD,
            fg=_TEXT,
            font=_FONT_BOLD,
            padx=12,
            pady=10,
            relief="groove",
        )
        launch.pack(fill="x")

        tk.Label(launch, text="Quiz file", bg=_CARD, fg=_MUTED, font=_FONT).grid(
            row=0, column=0, sticky="w"
        )
        tk.Entry(launch, textvariable=self.quiz_path_var, width=48, font=_FONT).grid(
            row=0, column=1, sticky="ew", padx=(8, 8)
        )
        tk.Button(
            launch,
            text="Browse…",
            command=self.browse_quiz,
            font=_FONT,
            bg="#e5e5ea",
            fg=_TEXT,
            relief="flat",
            activebackground="#d1d1d6",
            padx=8,
        ).grid(row=0, column=2, sticky="ew")
        tk.Button(
            launch,
            text="Load Quiz",
            command=self.load_selected_quiz,
            font=_FONT,
            bg="#e5e5ea",
            fg=_TEXT,
            relief="flat",
            activebackground="#d1d1d6",
            padx=8,
        ).grid(row=0, column=3, sticky="ew", padx=(6, 0))

        tk.Label(launch, text="Bind host", bg=_CARD, fg=_MUTED, font=_FONT).grid(
            row=1, column=0, sticky="w", pady=(10, 0)
        )
        self.host_entry = tk.Entry(launch, width=20, font=_FONT)
        self.host_entry.grid(row=1, column=1, sticky="w", pady=(10, 0), padx=(8, 8))
        self.host_entry.insert(0, self.bind_host)

        tk.Label(launch, text="Port", bg=_CARD, fg=_MUTED, font=_FONT).grid(
            row=1, column=2, sticky="w", pady=(10, 0)
        )
        self.port_entry = tk.Entry(launch, width=8, font=_FONT)
        self.port_entry.grid(row=1, column=3, sticky="w", pady=(10, 0))
        self.port_entry.insert(0, str(self.port))

        self.launch_button = tk.Button(
            launch,
            text="Launch Server",
            command=self.launch_server,
            font=_FONT_BOLD,
            bg=_BLUE,
            fg="white",
            activebackground="#0077ed",
            activeforeground="white",
            relief="flat",
            padx=12,
            pady=7,
        )
        self.launch_button.grid(
            row=2, column=0, columnspan=4, sticky="ew", pady=(12, 0)
        )
        launch.grid_columnconfigure(1, weight=1)

        # ---- Lobby ----
        info = tk.LabelFrame(
            container,
            text="  Lobby  ",
            bg=_CARD,
            fg=_TEXT,
            font=_FONT_BOLD,
            padx=12,
            pady=10,
            relief="groove",
        )
        info.pack(fill="x", pady=(12, 0))

        tk.Label(info, text="Quiz", bg=_CARD, fg=_MUTED, font=_FONT).grid(
            row=0, column=0, sticky="w"
        )
        tk.Label(
            info, textvariable=self.quiz_title_var, bg=_CARD, fg=_TEXT, font=_FONT_BOLD
        ).grid(row=0, column=1, sticky="w", padx=(8, 0))
        tk.Label(info, text="Session code", bg=_CARD, fg=_MUTED, font=_FONT).grid(
            row=1, column=0, sticky="w", pady=(4, 0)
        )
        tk.Label(
            info,
            textvariable=self.session_code_var,
            bg=_CARD,
            fg=_PURPLE,
            font=_FONT_CODE,
        ).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(4, 0))
        tk.Label(info, text="Address", bg=_CARD, fg=_MUTED, font=_FONT).grid(
            row=2, column=0, sticky="w"
        )
        tk.Label(
            info, textvariable=self.address_var, bg=_CARD, fg=_TEXT, font=_FONT_BOLD
        ).grid(row=2, column=1, sticky="w", padx=(8, 0))
        tk.Label(info, text="Status", bg=_CARD, fg=_MUTED, font=_FONT).grid(
            row=3, column=0, sticky="w"
        )
        tk.Label(
            info, textvariable=self.status_var, bg=_CARD, fg=_MUTED, font=_FONT
        ).grid(row=3, column=1, sticky="w", padx=(8, 0))

        self.players_list = tk.Listbox(
            info,
            height=6,
            exportselection=False,
            font=_FONT,
            relief="solid",
            bd=1,
            selectbackground=_PURPLE,
            selectforeground="white",
        )
        self.players_list.grid(row=0, column=2, rowspan=4, sticky="nsew", padx=(16, 0))

        self.start_button = tk.Button(
            info,
            text="▶  Start Game",
            command=self.start_game,
            state="disabled",
            font=_FONT_BOLD,
            bg=_DISABLED_BG,
            fg=_DISABLED_FG,
            relief="flat",
            padx=12,
            pady=8,
        )
        self.start_button.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        info.grid_columnconfigure(1, weight=1)
        info.grid_columnconfigure(2, weight=1)

        # ---- Presenter View ----
        presenter = tk.LabelFrame(
            container,
            text="  Presenter View  ",
            bg=_CARD,
            fg=_TEXT,
            font=_FONT_BOLD,
            padx=12,
            pady=10,
            relief="groove",
        )
        presenter.pack(fill="both", expand=True, pady=(12, 0))

        tk.Label(presenter, text="Phase", bg=_CARD, fg=_MUTED, font=_FONT).grid(
            row=0, column=0, sticky="w"
        )
        tk.Label(
            presenter,
            textvariable=self.phase_var,
            bg=_CARD,
            fg=_PURPLE,
            font=_FONT_BOLD,
        ).grid(row=0, column=1, sticky="w", padx=(8, 0))
        tk.Label(presenter, text="Question", bg=_CARD, fg=_MUTED, font=_FONT).grid(
            row=1, column=0, sticky="nw", pady=(6, 0)
        )
        tk.Label(
            presenter,
            textvariable=self.question_var,
            justify="left",
            wraplength=400,
            bg=_CARD,
            fg=_TEXT,
            font=_FONT_BOLD,
        ).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(6, 0))

        tk.Label(
            presenter, textvariable=self.countdown_var, bg=_CARD, fg=_MUTED, font=_FONT
        ).grid(row=2, column=0, sticky="w", pady=(8, 0))
        tk.Label(
            presenter,
            textvariable=self.answered_var,
            bg=_CARD,
            fg=_TEXT,
            font=_FONT_BOLD,
        ).grid(row=2, column=1, sticky="w", pady=(8, 0))
        tk.Label(
            presenter,
            textvariable=self.result_var,
            justify="left",
            bg=_CARD,
            fg=_MUTED,
            font=_FONT_SM,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(2, 0))

        tk.Label(presenter, text="Options", bg=_CARD, fg=_MUTED, font=_FONT).grid(
            row=4, column=0, sticky="nw", pady=(12, 0)
        )
        self.options_list = tk.Listbox(
            presenter,
            height=5,
            exportselection=False,
            font=_FONT,
            relief="solid",
            bd=1,
            selectbackground=_PURPLE,
            selectforeground="white",
        )
        self.options_list.grid(row=4, column=1, sticky="nsew", pady=(12, 0))

        tk.Label(
            presenter, text="Leaderboard\n/ Rankings", bg=_CARD, fg=_MUTED, font=_FONT
        ).grid(row=5, column=0, sticky="nw", pady=(12, 0))
        self.leaderboard_list = tk.Listbox(
            presenter,
            height=8,
            exportselection=False,
            font=_FONT,
            relief="solid",
            bd=1,
            selectbackground=_PURPLE,
            selectforeground="white",
        )
        self.leaderboard_list.grid(row=5, column=1, sticky="nsew", pady=(12, 0))
        presenter.grid_columnconfigure(1, weight=1)
        presenter.grid_rowconfigure(5, weight=1)

    # ------------------------------------------------------------------
    # Host-side actions
    # ------------------------------------------------------------------

    def browse_quiz(self) -> None:
        """Open a file picker and populate the quiz-path field."""
        path = filedialog.askopenfilename(
            title="Choose a quiz file",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.quiz_path_var.set(path)

    def load_selected_quiz(self) -> None:
        """Load the quiz file currently shown in the path entry."""
        path = self.quiz_path_var.get().strip()
        if not path:
            self.status_var.set("Choose a quiz file first.")
            return
        self.load_quiz_file(path)

    def load_quiz_file(self, path: str) -> None:
        """Load a quiz from disk and refresh host labels."""
        self.quiz = load_quiz(path)
        self.quiz_path = path
        self.quiz_title_var.set(self.quiz.title)
        self.status_var.set("Quiz loaded. Launch the server when ready.")
        self._update_start_button_state()

    def launch_server(self) -> None:
        """Create and start a GameServer for this host session."""
        if self.server is not None:
            self._sync_server_labels()
            self.status_var.set("Server already running.")
            return

        bind_host = self.host_entry.get().strip() or self.bind_host
        port_text = self.port_entry.get().strip()
        try:
            port = int(port_text)
        except ValueError:
            self.status_var.set("Port must be an integer.")
            return

        self.bind_host = bind_host
        self.port = port
        self.server = GameServer(host=bind_host, port=port)
        self.server.start()
        self._sync_server_labels()
        self.status_var.set("Server running. Waiting for players.")
        self._update_start_button_state()

    def start_game(self) -> None:
        """Start the local GameLoop once the lobby is ready."""
        if not self.can_start_game():
            return
        if self.server is None or self.quiz is None:
            return
        if self._game_started:
            return

        self._game_loop = self.game_loop_factory(
            self.server,
            self.quiz,
            min_players=2,
            lobby_countdown=0,
            host_observer=self,
        )
        self._game_thread = threading.Thread(
            target=self._game_loop.start,
            daemon=True,
            name="host-game-loop",
        )
        self._game_started = True
        self.phase_var.set("Question")
        self.status_var.set("Game started.")
        self._update_start_button_state()
        self._game_thread.start()

    def can_start_game(self) -> bool:
        """Return True when the host has enough state to start the game."""
        if self.server is None or self.quiz is None or self._game_started:
            return False
        return len(self.server.get_players()) >= 2

    def refresh_lobby(self) -> None:
        """Refresh lobby-related labels and the joined-player list."""
        players: list[str] = []
        if self.server is not None:
            players = self.server.get_players()
            self._sync_server_labels()

        self.players_list.delete(0, tk.END)
        for player in players:
            self.players_list.insert(tk.END, player)

        if self.server is None:
            self.status_var.set("Load a quiz and launch the server.")
        elif self._game_started:
            self.status_var.set("Game in progress.")
        elif self.quiz is None:
            self.status_var.set("Server running. Load a quiz to continue.")
        elif len(players) < 2:
            self.status_var.set("Waiting for 2+ players.")
        else:
            self.status_var.set("Ready to start.")
        self._update_start_button_state()

    def shutdown(self) -> None:
        """Stop polling, stop the owned server, and close the window."""
        self._closed = True
        if self._poll_after_id is not None:
            with contextlib.suppress(tk.TclError):
                self.root.after_cancel(self._poll_after_id)
            self._poll_after_id = None

        if self.server is not None and self._owns_server:
            self.server.stop()
            self.server = None

        self.root.destroy()

    # ------------------------------------------------------------------
    # Observer callbacks from GameLoop
    # ------------------------------------------------------------------

    def on_game_started(self, total_questions: int) -> None:
        """Show the transition from lobby to active game."""
        self._schedule_ui(
            self._set_phase,
            "Question",
            f"Game started. {total_questions} question(s) queued.",
        )

    def on_question(
        self,
        index: int,
        total: int,
        text: str,
        question_type: str,
        options: list[str],
        time_limit: float,
    ) -> None:
        """Render a new active question in the presenter view."""
        self._schedule_ui(
            self._show_question,
            index,
            total,
            text,
            question_type,
            options,
            time_limit,
        )

    def on_timer_tick(
        self, question_index: int, remaining: float, answered: int, total: int
    ) -> None:
        """Refresh countdown and answer-progress labels."""
        self._schedule_ui(
            self._show_timer_tick, question_index, remaining, answered, total
        )

    def on_question_result(
        self,
        correct_answer: int,
        leaderboard: list[dict[str, int | str]],
        pause_duration: int,
        show_correct_answer: bool,
        show_leaderboard: bool,
    ) -> None:
        """Render between-question result state for the host."""
        self._schedule_ui(
            self._show_question_result,
            correct_answer,
            leaderboard,
            pause_duration,
            show_correct_answer,
            show_leaderboard,
        )

    def on_game_over(
        self, final_rankings: list[dict[str, int | float | str]], total_questions: int
    ) -> None:
        """Render the final rankings in the presenter view."""
        self._schedule_ui(self._show_game_over, final_rankings, total_questions)

    # ------------------------------------------------------------------
    # UI update helpers
    # ------------------------------------------------------------------

    def _schedule_lobby_refresh(self) -> None:
        """Poll simple lobby/server state from the Tkinter thread."""
        if self._closed:
            return
        self.refresh_lobby()
        self._poll_after_id = self.root.after(250, self._schedule_lobby_refresh)

    def _schedule_ui(self, fn: Callable[..., None], *args: object) -> None:
        """Run one UI mutation on the Tkinter thread."""
        if self._closed:
            return
        self.root.after(0, lambda: fn(*args))

    def _set_phase(self, phase: str, status: str) -> None:
        """Update the phase and status labels together."""
        self.phase_var.set(phase)
        self.status_var.set(status)

    def _show_question(
        self,
        index: int,
        total: int,
        text: str,
        question_type: str,
        options: list[str],
        time_limit: float,
    ) -> None:
        """Render the presenter view for one active question."""
        self.phase_var.set("Question")
        self.question_var.set(
            f"Q{index + 1}/{total}: {text} ({question_type}, {time_limit:.1f}s)"
        )
        self.countdown_var.set(f"Countdown: {time_limit:.1f}s")
        self.answered_var.set("Answered: 0 / 0")
        self.result_var.set("")
        self.options_list.delete(0, tk.END)
        for option_index, option in enumerate(options):
            self.options_list.insert(tk.END, f"{option_index}: {option}")

    def _show_timer_tick(
        self, question_index: int, remaining: float, answered: int, total: int
    ) -> None:
        """Update the live presenter counters."""
        self.phase_var.set("Question")
        self.countdown_var.set(f"Countdown: {remaining:.1f}s (Q{question_index + 1})")
        self.answered_var.set(f"Answered: {answered} / {total}")

    def _show_question_result(
        self,
        correct_answer: int,
        leaderboard: list[dict[str, int | str]],
        pause_duration: int,
        show_correct_answer: bool,
        show_leaderboard: bool,
    ) -> None:
        """Render the between-question results state."""
        self.phase_var.set("Results")
        result_bits: list[str] = [f"Next question in {pause_duration}s"]
        if show_correct_answer:
            result_bits.insert(0, f"Correct answer index: {correct_answer}")
        if not show_leaderboard:
            result_bits.append("Leaderboard hidden by quiz settings")
        self.result_var.set(" | ".join(result_bits))
        self._populate_rankings(leaderboard)

    def _show_game_over(
        self, final_rankings: list[dict[str, int | float | str]], total_questions: int
    ) -> None:
        """Render the final summary view."""
        self.phase_var.set("Game Over")
        self.result_var.set(f"Game complete. {total_questions} question(s) played.")
        self._populate_rankings(final_rankings)

    def _populate_rankings(
        self, rows: list[dict[str, int | str]] | list[dict[str, int | float | str]]
    ) -> None:
        """Render leaderboard or final-ranking rows into the shared listbox."""
        self.leaderboard_list.delete(0, tk.END)
        for row in rows:
            if "rank" in row:
                self.leaderboard_list.insert(
                    tk.END,
                    (
                        f"#{row['rank']} {row['name']} - {row['score']} pts, "
                        f"{row['correct']} correct, streak {row['streak']}, "
                        f"fastest {row['fastest_answer']}"
                    ),
                )
            else:
                self.leaderboard_list.insert(
                    tk.END, f"{row['name']} - {row['score']} pts"
                )

    def _sync_server_labels(self) -> None:
        """Refresh server/session metadata labels."""
        if self.server is None:
            self.session_code_var.set("Not started")
            self.address_var.set("Server offline")
            return
        self.session_code_var.set(self.server.session_code)
        self.address_var.set(self._format_address())

    def _format_address(self) -> str:
        """Return a human-readable server address line for the host."""
        if self.server is None:
            return "Server offline"
        display_host = self.server.host
        if display_host == "0.0.0.0":
            display_host = self._get_local_ip()
        return f"{display_host}:{self.server.port}"

    def _update_start_button_state(self) -> None:
        """Enable or disable the Start button based on current readiness."""
        if self.can_start_game():
            self.start_button.configure(
                state="normal",
                bg=_GREEN,
                fg="white",
                activebackground="#1e6e09",
                activeforeground="white",
            )
        else:
            self.start_button.configure(
                state="disabled",
                bg=_DISABLED_BG,
                fg=_DISABLED_FG,
            )

    @staticmethod
    def _get_local_ip() -> str:
        """Best-effort lookup of the host's LAN IP for display purposes."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                return str(sock.getsockname()[0])
        except OSError:
            return "127.0.0.1"


def launch_host_app(
    quiz_path: str | None = None,
    *,
    bind_host: str = "0.0.0.0",
    port: int = DEFAULT_PORT,
) -> None:
    """Launch the host GUI and enter the Tkinter main loop."""
    root = tk.Tk()
    root.minsize(720, 640)
    quiz = load_quiz(quiz_path) if quiz_path is not None else None
    gui = HostGUI(root, quiz=quiz, quiz_path=quiz_path, bind_host=bind_host, port=port)
    if quiz is not None:
        gui.status_var.set("Quiz loaded. Launch the server when ready.")
    root.mainloop()
