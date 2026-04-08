"""Player GUI - handles the player's view of the game."""

import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox

from src.client import GameClient


class PlayerGUI:
    """Main GUI controller for the player."""

    def __init__(self) -> None:
        """Initialize the GUI and client."""
        self.root = tk.Tk()
        self.root.title("BrainZap - Player")

        self.client = GameClient()

        # Store current frame
        self.current_frame: tk.Frame | None = None

        # Widget references (populated by show_lobby_screen / show_question_screen)
        self.player_listbox: tk.Listbox | None = None
        self.status_label: tk.Label | None = None
        self.countdown_label: tk.Label | None = None
        self.question_label: tk.Label | None = None
        self.timer_label: tk.Label | None = None
        self.answer_buttons: list[tk.Button] = []
        self.current_question_index: int = -1

        # Wire callbacks before showing first screen
        self._wire_callbacks()

        # Start with join screen
        self.show_join_screen()

    # ------------------------------------------------------------------
    # Callback wiring
    # ------------------------------------------------------------------

    def _wire_callbacks(self) -> None:
        """Hook GameClient callbacks to the GUI."""

        def handle_lobby_update(
            players: list[str],
            host_started_countdown: bool,
            countdown_remaining: float | None,
        ) -> None:
            """Handle lobby updates from server and refresh the lobby screen UI."""

            def update_ui() -> None:
                # First time receiving lobby update — switch to lobby screen
                if self.player_listbox is None:
                    self.show_lobby_screen()

                assert self.player_listbox is not None
                assert self.status_label is not None

                # Update player list
                self.player_listbox.delete(0, tk.END)
                for player in players:
                    self.player_listbox.insert(tk.END, player)

                # Update status and countdown
                if host_started_countdown and countdown_remaining is not None:
                    self.status_label.config(text="Game starting soon...")
                    if self.countdown_label is not None:
                        self.countdown_label.config(
                            text=f"Starting in {int(countdown_remaining)}s"
                        )
                else:
                    self.status_label.config(text="Waiting for host...")
                    if self.countdown_label is not None:
                        self.countdown_label.config(text="")

            self.root.after(0, update_ui)

        def handle_error(message: str) -> None:
            """Show server error as a messagebox."""
            self.root.after(
                0,
                lambda: messagebox.showerror("Error", message),
            )

        def handle_disconnect() -> None:
            """Show reconnect notice on disconnect."""
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
            """Show question screen when server sends a new question."""
            self.root.after(
                0,
                lambda: self.show_question_screen(
                    question_text, options, question_index
                ),
            )

        # Assign to client attributes (not called as methods)
        self.client.on_lobby_update = handle_lobby_update
        self.client.on_error = handle_error
        self.client.on_disconnect = handle_disconnect
        self.client.question_callback = handle_question

    # ------------------------------------------------------------------
    # Screen switching
    # ------------------------------------------------------------------

    def clear_frame(self) -> None:
        """Remove current frame."""
        if self.current_frame is not None:
            self.current_frame.destroy()

    def show_join_screen(self) -> None:
        """Display the join screen."""
        self.clear_frame()

        frame = tk.Frame(self.root, padx=20, pady=20)
        frame.pack()
        self.current_frame = frame

        tk.Label(frame, text="Join Game", font=("Arial", 18)).pack(pady=10)

        tk.Label(frame, text="Server IP").pack()
        ip_entry = tk.Entry(frame)
        ip_entry.pack()

        tk.Label(frame, text="Session Code").pack()
        code_entry = tk.Entry(frame)
        code_entry.pack()

        tk.Label(frame, text="Display Name").pack()
        name_entry = tk.Entry(frame)
        name_entry.pack()

        error_label = tk.Label(frame, text="", fg="red")
        error_label.pack(pady=5)

        def on_join() -> None:
            ip = ip_entry.get().strip()
            code = code_entry.get().strip()
            name = name_entry.get().strip()

            if not ip or not code or not name:
                error_label.config(text="All fields are required")
                return

            try:
                self.client.connect(ip, 5000)
                self.client.join(code, name)
            except Exception as e:
                error_label.config(text=str(e))

        tk.Button(frame, text="Join", command=on_join).pack(pady=10)

    def show_lobby_screen(self) -> None:
        """Display the lobby screen."""
        self.clear_frame()

        frame = tk.Frame(self.root, padx=20, pady=20)
        frame.pack(fill="both", expand=True)
        self.current_frame = frame

        tk.Label(frame, text="Lobby", font=("Arial", 18)).pack(pady=10)
        tk.Label(frame, text="Players:").pack()

        self.player_listbox = tk.Listbox(frame, height=10)
        self.player_listbox.pack(fill="both", expand=True, pady=10)

        self.status_label = tk.Label(frame, text="Waiting for host...")
        self.status_label.pack(pady=5)

        self.countdown_label = tk.Label(frame, text="")
        self.countdown_label.pack()

    def show_question_screen(
        self,
        question_text: str,
        options: list[str],
        question_index: int,
    ) -> None:
        """Display the question screen."""
        self.clear_frame()

        frame = tk.Frame(self.root, padx=20, pady=20)
        frame.pack(fill="both", expand=True)
        self.current_frame = frame

        self.current_question_index = question_index

        self.question_label = tk.Label(
            frame, text=question_text, font=("Arial", 16), wraplength=400
        )
        self.question_label.pack(pady=10)

        self.answer_buttons = []
        for i, option in enumerate(options):

            def make_command(idx: int) -> Callable[[], None]:
                return lambda: self.submit_answer(idx)

            btn = tk.Button(
                frame,
                text=option,
                width=30,
                command=make_command(i),
            )

            btn.pack(pady=5)
            self.answer_buttons.append(btn)

        self.timer_label = tk.Label(frame, text="")
        self.timer_label.pack(pady=10)

    def submit_answer(self, choice: int) -> None:
        """Send answer to server and disable buttons."""
        for btn in self.answer_buttons:
            btn.config(state=tk.DISABLED)

        try:
            self.client.send_answer(self.current_question_index, choice)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to submit answer: {e}")

    def show_results_screen(
        self,
        question_text: str,
        options: list[str],
        correct_index: int,
        your_score: int,
        your_total: int,
        leaderboard: list[dict[str, int]] | None = None,
    ) -> None:
        """Display the results screen after a question is answered."""
        self.clear_frame()

        frame = tk.Frame(self.root, padx=20, pady=20)
        frame.pack(fill="both", expand=True)
        self.current_frame = frame

        # Questions
        tk.Label(
            frame,
            text=f"Correct Answer: {options[correct_index]}",
            font=("Arial", 16),
            fg="green",
            wraplength=400,
        ).pack(pady=10)

        # Correct answer
        if 0 <= correct_index < len(options):
            tk.Label(
                frame,
                text=f"Correct Answer: {options[correct_index]}",
                font=("Arial", 16),
                fg="green",
                wraplength=400,
            ).pack(pady=10)

        # Player Score
        tk.Label(
            frame,
            text=f"Your Score: {your_score}/{your_total}",
            font=("Arial", 16),
            wraplength=400,
        ).pack(pady=10)

        # Leaderboard
        if leaderboard is not None:
            tk.Label(frame, text="Leaderboard:", font=("Arial", 14)).pack(pady=5)
            for entry in leaderboard:
                tk.Label(
                    frame,
                    text=f"{entry['display_name']}: {entry['score']} points",
                    font=("Arial", 12),
                    wraplength=400,
                ).pack()

    def show_game_over_screen(
        self,
        final_rankings: list[dict[str, int | str | float]],
        total_questions: int,
    ) -> None:
        """Display the game over screen with final rankings."""
        self.clear_frame()

        frame = tk.Frame(self.root, padx=20, pady=20)
        frame.pack(fill="both", expand=True)
        self.current_frame = frame

        tk.Label(frame, text="Game Over", font=("Arial", 18)).pack(pady=10)

        tk.Label(
            frame,
            text=f"Total Questions: {total_questions}",
            font=("Arial", 14),
            wraplength=400,
        ).pack(pady=5)

        tk.Label(frame, text="Final Rankings:", font=("Arial", 14)).pack(pady=5)
        for i, entry in enumerate(final_rankings, start=1):
            tk.Label(
                frame,
                text=f"{i}. {entry['display_name']} - {entry['score']} points (Avg Time: {entry['avg_time']:.2f}s)",
                font=("Arial", 12),
                wraplength=400,
            ).pack()

        # Button to close GUI
        tk.Button(frame, text="Exit", command=self.root.destroy).pack(pady=20)

    # ------------------------------------------------------------------
    # Run GUI
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
