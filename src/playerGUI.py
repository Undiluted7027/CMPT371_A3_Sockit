"""Player GUI - handles the player's view of the game."""

import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox

from .client import GameClient
from .server import DEFAULT_PORT

# ---------------------------------------------------------------------------
# Visual theme — mirrors host_gui.py palette
# ---------------------------------------------------------------------------

_BG = "#f5f5f7"
_CARD = "#ffffff"
_PURPLE = "#46178f"
_GREEN = "#26890c"
_BLUE = "#0071e3"
_RED = "#d70015"
_TEXT = "#1d1d1f"
_MUTED = "#6e6e73"
_DISABLED_BG = "#d1d1d6"
_DISABLED_FG = "#8e8e93"

_FONT = ("Helvetica Neue", 12)
_FONT_BOLD = ("Helvetica Neue", 12, "bold")
_FONT_LG = ("Helvetica Neue", 16, "bold")
_FONT_XL = ("Helvetica Neue", 22, "bold")
_FONT_SM = ("Helvetica Neue", 10)


def _card(parent: tk.Widget, **kw: object) -> tk.Frame:
    """Return a white card frame with consistent padding."""
    return tk.Frame(parent, bg=_CARD, padx=16, pady=14, **kw)  # type: ignore[arg-type]


class PlayerGUI:
    """Main GUI controller for the player."""

    def __init__(self) -> None:
        """Initialize the GUI and client."""
        self.root = tk.Tk()
        self.root.title("BrainZap - Player")
        self.root.configure(bg=_BG)
        self.root.minsize(480, 400)

        self.client = GameClient()

        # Widget references (populated by show_* methods)
        self.current_frame: tk.Frame | None = None
        self.player_listbox: tk.Listbox | None = None
        self.status_label: tk.Label | None = None
        self.countdown_label: tk.Label | None = None
        self.question_label: tk.Label | None = None
        self.timer_label: tk.Label | None = None
        self.answer_buttons: list[tk.Button] = []
        self.current_question_index: int = -1
        self._join_error_label: tk.Label | None = None
        self._game_started: bool = False

        self._wire_callbacks()
        self.show_join_screen()

    # ------------------------------------------------------------------
    # Callback wiring
    # ------------------------------------------------------------------

    def _wire_callbacks(self) -> None:
        """Hooks GameClient callbacks to the GUI."""

        def handle_lobby_update(
            players: list[str],
            host_started_countdown: bool,
            countdown_remaining: float | None,
        ) -> None:
            def update_ui() -> None:
                if self._game_started:
                    return  # ignore lobby polls once the game is underway
                if self.player_listbox is None:
                    self.show_lobby_screen()

                assert self.player_listbox is not None
                assert self.status_label is not None

                self.player_listbox.delete(0, tk.END)
                for player in players:
                    self.player_listbox.insert(tk.END, player)

                if host_started_countdown and countdown_remaining is not None:
                    self.status_label.config(text="Game starting soon...")
                    if self.countdown_label is not None:
                        self.countdown_label.config(
                            text=f"Starting in {int(countdown_remaining)}s"
                        )
                else:
                    self.status_label.config(text="Waiting for host to start the game.")
                    if self.countdown_label is not None:
                        self.countdown_label.config(text="")

            self.root.after(0, update_ui)

        def handle_error(message: str) -> None:
            def update() -> None:
                if self._join_error_label is not None:
                    self._join_error_label.config(text=message)
                else:
                    messagebox.showerror("Error", message)

            self.root.after(0, update)

        def handle_disconnect() -> None:
            self.root.after(
                0,
                lambda: messagebox.showerror(
                    "Disconnected", "Lost connection to server."
                ),
            )

        def handle_question(
            question_text: str,
            options: list[str],
            question_index: int,
            time_limit: float,
        ) -> None:
            self.root.after(
                0,
                lambda: self.show_question_screen(
                    question_text, options, question_index
                ),
            )

        self.client.on_lobby_update = handle_lobby_update  # type: ignore[method-assign]
        self.client.on_error = handle_error  # type: ignore[method-assign]
        self.client.on_disconnect = handle_disconnect  # type: ignore[method-assign]
        self.client.question_callback = handle_question

    # ------------------------------------------------------------------
    # Screen switching helpers
    # ------------------------------------------------------------------

    def clear_frame(self) -> None:
        """Destroy the current content frame."""
        if self.current_frame is not None:
            self.current_frame.destroy()
            self.current_frame = None

    def _container(self) -> tk.Frame:
        """Create a fresh padded container that fills the window."""
        self.clear_frame()
        frame = tk.Frame(self.root, bg=_BG, padx=24, pady=24)
        frame.pack(fill="both", expand=True)
        self.current_frame = frame
        return frame

    # ------------------------------------------------------------------
    # Screens
    # ------------------------------------------------------------------

    def show_join_screen(self) -> None:
        """Join screen: IP, port, session code, display name."""
        container = self._container()

        tk.Label(container, text="Join Game", bg=_BG, fg=_PURPLE, font=_FONT_XL).pack(
            pady=(0, 16)
        )

        card = _card(container, relief="groove")
        card.pack(fill="x")

        def _row(label: str, row: int, default: str = "") -> tk.Entry:
            tk.Label(
                card, text=label, bg=_CARD, fg=_MUTED, font=_FONT, anchor="w"
            ).grid(row=row, column=0, sticky="w", pady=(6, 0))
            entry = tk.Entry(card, font=_FONT, width=28, relief="solid", bd=1)
            entry.insert(0, default)
            entry.grid(row=row, column=1, sticky="ew", padx=(10, 0), pady=(6, 0))
            return entry

        ip_entry = _row("Server IP", 0, "127.0.0.1")
        port_entry = _row("Port", 1, str(DEFAULT_PORT))
        code_entry = _row("Session Code", 2)
        name_entry = _row("Display Name", 3)
        card.grid_columnconfigure(1, weight=1)

        error_label = tk.Label(container, text="", bg=_BG, fg=_RED, font=_FONT_SM)
        error_label.pack(pady=(10, 0))
        self._join_error_label = error_label

        def on_join() -> None:
            ip = ip_entry.get().strip()
            port_str = port_entry.get().strip()
            code = code_entry.get().strip().upper()
            name = name_entry.get().strip()

            if not ip or not code or not name:
                error_label.config(text="All fields are required.")
                return
            try:
                port = int(port_str)
            except ValueError:
                error_label.config(text="Port must be a number.")
                return

            try:
                self.client.connect(ip, port)
                self.client.join(code, name)
            except Exception as e:
                error_label.config(text=str(e))

        tk.Button(
            container,
            text="Join",
            command=on_join,
            font=_FONT_BOLD,
            bg=_BLUE,
            fg="white",
            activebackground="#0077ed",
            activeforeground="white",
            relief="flat",
            padx=20,
            pady=8,
        ).pack(pady=(16, 0))

    def show_game_start_screen(self) -> None:
        """Brief transition frame shown between lobby and first question."""
        self._game_started = True
        self._join_error_label = None
        self.player_listbox = None  # widget is about to be destroyed by _container()
        container = self._container()
        tk.Label(
            container, text="Game Starting…", bg=_BG, fg=_PURPLE, font=_FONT_XL
        ).pack(expand=True)

    def show_lobby_screen(self) -> None:
        """Show joined players in the waiting room and display the host countdown."""
        self._join_error_label = None
        container = self._container()

        tk.Label(container, text="Lobby", bg=_BG, fg=_PURPLE, font=_FONT_XL).pack(
            pady=(0, 12)
        )

        card = _card(container, relief="groove")
        card.pack(fill="both", expand=True)

        tk.Label(
            card, text="Players in session:", bg=_CARD, fg=_MUTED, font=_FONT
        ).pack(anchor="w")
        self.player_listbox = tk.Listbox(
            card,
            font=_FONT,
            height=8,
            relief="solid",
            bd=1,
            selectbackground=_PURPLE,
            selectforeground="white",
        )
        self.player_listbox.pack(fill="both", expand=True, pady=(6, 0))

        self.status_label = tk.Label(
            card,
            text="Waiting for host to start the game.",
            bg=_CARD,
            fg=_MUTED,
            font=_FONT,
        )
        self.status_label.pack(pady=(10, 0))

        self.countdown_label = tk.Label(
            card, text="", bg=_CARD, fg=_PURPLE, font=_FONT_BOLD
        )
        self.countdown_label.pack()

    def show_question_screen(
        self,
        question_text: str,
        options: list[str],
        question_index: int,
    ) -> None:
        """Active question with answer buttons."""
        container = self._container()
        self.current_question_index = question_index

        # Question text
        card = _card(container, relief="groove")
        card.pack(fill="x")
        tk.Label(
            card,
            text=question_text,
            bg=_CARD,
            fg=_TEXT,
            font=_FONT_LG,
            wraplength=400,
            justify="left",
        ).pack(anchor="w")

        # Answer buttons
        btn_frame = tk.Frame(container, bg=_BG)
        btn_frame.pack(fill="x", pady=(12, 0))

        _COLOURS = ["#e21b3c", "#1368ce", "#d89e00", "#26890c"]
        self.answer_buttons = []

        for i, option in enumerate(options):

            def make_command(idx: int) -> Callable[[], None]:
                return lambda: self.submit_answer(idx)

            colour = _COLOURS[i % len(_COLOURS)]
            btn = tk.Button(
                btn_frame,
                text=option,
                command=make_command(i),
                font=_FONT_BOLD,
                bg=colour,
                fg="white",
                activebackground=colour,
                activeforeground="white",
                relief="flat",
                padx=12,
                pady=10,
                wraplength=360,
                justify="left",
            )
            btn.pack(fill="x", pady=4)
            self.answer_buttons.append(btn)

        self.timer_label = tk.Label(
            container, text="", bg=_BG, fg=_MUTED, font=_FONT_SM
        )
        self.timer_label.pack(pady=(8, 0))

    def submit_answer(self, choice: int) -> None:
        """Send answer and lock buttons to prevent double submit."""
        for btn in self.answer_buttons:
            btn.config(state=tk.DISABLED, bg=_DISABLED_BG, fg=_DISABLED_FG)

        try:
            self.client.send_answer(self.current_question_index, choice)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to submit answer: {e}")

    def show_results_screen(
        self,
        correct_index: int,
        your_score: int,
        your_total: int,
        leaderboard: list[dict[str, int | str]] | None = None,
    ) -> None:
        """Between-question results: correct answer, score, leaderboard."""
        container = self._container()

        tk.Label(container, text="Results", bg=_BG, fg=_PURPLE, font=_FONT_XL).pack(
            pady=(0, 12)
        )

        score_card = _card(container, relief="groove")
        score_card.pack(fill="x")
        tk.Label(
            score_card,
            text=f"Correct answer: option {correct_index + 1}",
            bg=_CARD,
            fg=_GREEN,
            font=_FONT_BOLD,
        ).pack(anchor="w")
        tk.Label(
            score_card,
            text=f"+{your_score} pts    Total: {your_total}",
            bg=_CARD,
            fg=_TEXT,
            font=_FONT_LG,
        ).pack(anchor="w", pady=(6, 0))

        if leaderboard:
            lb_card = _card(container, relief="groove")
            lb_card.pack(fill="both", expand=True, pady=(12, 0))
            tk.Label(
                lb_card, text="Leaderboard", bg=_CARD, fg=_MUTED, font=_FONT_BOLD
            ).pack(anchor="w")
            for rank, entry in enumerate(leaderboard, start=1):
                tk.Label(
                    lb_card,
                    text=f"{rank}.  {entry['name']}  —  {entry['score']} pts",
                    bg=_CARD,
                    fg=_TEXT,
                    font=_FONT,
                    anchor="w",
                ).pack(fill="x", pady=2)

    def show_game_over_screen(
        self,
        final_rankings: list[dict[str, int | str | float]],
        total_questions: int,
    ) -> None:
        """Show final rankings screen."""
        container = self._container()

        tk.Label(container, text="Game Over", bg=_BG, fg=_PURPLE, font=_FONT_XL).pack(
            pady=(0, 4)
        )
        tk.Label(
            container,
            text=f"{total_questions} questions played",
            bg=_BG,
            fg=_MUTED,
            font=_FONT,
        ).pack(pady=(0, 12))

        card = _card(container, relief="groove")
        card.pack(fill="both", expand=True)
        tk.Label(
            card, text="Final Rankings", bg=_CARD, fg=_MUTED, font=_FONT_BOLD
        ).pack(anchor="w", pady=(0, 8))

        for entry in final_rankings:
            fastest = entry.get("fastest_answer", 0.0)
            fastest_str = f"{fastest:.2f}s" if fastest else "—"
            tk.Label(
                card,
                text=(
                    f"#{entry['rank']}  {entry['name']}"
                    f"  —  {entry['score']} pts"
                    f"  ·  {entry['correct']} correct"
                    f"  ·  fastest {fastest_str}"
                ),
                bg=_CARD,
                fg=_TEXT,
                font=_FONT,
                anchor="w",
            ).pack(fill="x", pady=3)

        tk.Button(
            container,
            text="Exit",
            command=self.root.destroy,
            font=_FONT_BOLD,
            bg=_MUTED,
            fg="white",
            activebackground="#555",
            relief="flat",
            padx=16,
            pady=8,
        ).pack(pady=(16, 0))

    def show_player_disconnected(self, display_name: str) -> None:
        """Non-blocking notice that another player left."""
        messagebox.showinfo("Player Left", f"{display_name} disconnected.")

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start the Tkinter main loop."""
        self.root.mainloop()


def main() -> None:
    """Create and run the Player GUI."""
    gui = PlayerGUI()
    gui.client.gui = gui
    gui.run()


if __name__ == "__main__":
    main()
