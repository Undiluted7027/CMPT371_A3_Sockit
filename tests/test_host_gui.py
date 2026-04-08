"""Tests for the host GUI and presenter-observer wiring."""

import json
import socket
from collections.abc import Callable, Generator
from pathlib import Path

import pytest

import src.host_gui as host_gui_module
from src.host_gui import HostGUI
from src.protocol import MsgType, recv_msg, send_msg
from src.quiz import Quiz, load_quiz
from src.server import GameServer


class FakeStringVar:
    """Minimal stand-in for tkinter.StringVar used in headless tests."""

    def __init__(self, value: str = "") -> None:
        """Initialize the variable with an optional starting value."""
        self.value = value

    def set(self, value: str) -> None:
        """Set the stored string value."""
        self.value = value

    def get(self) -> str:
        """Return the stored string value."""
        return self.value


class FakeWidget:
    """Base widget with no-op geometry methods."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        """Initialize widget options from keyword arguments."""
        self.options: dict[str, object] = dict(_kwargs)

    def pack(self, *_args: object, **_kwargs: object) -> None:
        """Simulate Tk pack geometry management as a no-op."""
        pass

    def grid(self, *_args: object, **_kwargs: object) -> None:
        """Simulate Tk grid geometry management as a no-op."""
        pass

    def configure(self, **kwargs: object) -> None:
        """Update stored widget options."""
        self.options.update(kwargs)

    def cget(self, key: str) -> object:
        """Return one configured option by key."""
        return self.options[key]


class FakeFrame(FakeWidget):
    """Frame-like widget with layout configuration stubs."""

    def grid_columnconfigure(self, *_args: object, **_kwargs: object) -> None:
        """Simulate Tk column layout configuration as a no-op."""
        pass

    def grid_rowconfigure(self, *_args: object, **_kwargs: object) -> None:
        """Simulate Tk row layout configuration as a no-op."""
        pass


class FakeEntry(FakeWidget):
    """Entry-like widget storing plain text."""

    def __init__(self, *_args: object, **kwargs: object) -> None:
        """Initialize an empty entry widget."""
        super().__init__(*_args, **kwargs)
        self.text = ""

    def insert(self, _index: int, text: str) -> None:
        """Set entry text to the provided value."""
        self.text = text

    def get(self) -> str:
        """Return the current entry text."""
        return self.text


class FakeButton(FakeWidget):
    """Button-like widget with a callable command."""

    def __init__(self, *_args: object, **kwargs: object) -> None:
        """Initialize a button and capture its optional command callback."""
        super().__init__(*_args, **kwargs)
        self.command = kwargs.get("command")

    def invoke(self) -> None:
        """Invoke the stored command callback when callable."""
        if callable(self.command):
            self.command()


class FakeListbox(FakeWidget):
    """Listbox-like widget backed by a Python list."""

    def __init__(self, *_args: object, **kwargs: object) -> None:
        """Initialize an empty list-backed listbox."""
        super().__init__(*_args, **kwargs)
        self.items: list[str] = []

    def delete(self, start: int, end: object | None = None) -> None:
        """Delete items, clearing all entries when start is zero."""
        del end
        if start == 0:
            self.items.clear()

    def insert(self, _index: object, value: str) -> None:
        """Append one item to the listbox storage."""
        self.items.append(value)

    def size(self) -> int:
        """Return the number of stored listbox items."""
        return len(self.items)


class FakeRoot:
    """Headless root object implementing the small Tk API HostGUI uses."""

    def __init__(self) -> None:
        """Initialize headless scheduler state and root metadata."""
        self.pending: list[Callable[[], None]] = []
        self.scheduled: dict[str, Callable[[], None]] = {}
        self._next_id = 0
        self.destroyed = False
        self.window_title = ""
        self.protocol_handler: object | None = None

    def title(self, value: str) -> None:
        """Set the simulated window title."""
        self.window_title = value

    def protocol(self, _name: str, handler: object) -> None:
        """Store a protocol handler callback."""
        self.protocol_handler = handler

    def after(self, delay: int, callback: Callable[[], None]) -> str:
        """Schedule a callback and return a token identifier."""
        token = f"after-{self._next_id}"
        self._next_id += 1
        if delay == 0:
            self.pending.append(callback)
        else:
            self.scheduled[token] = callback
        return token

    def after_cancel(self, token: str) -> None:
        """Cancel a previously scheduled delayed callback."""
        self.scheduled.pop(token, None)

    def configure(self, **_kwargs: object) -> None:
        """Simulate root configuration as a no-op."""
        pass

    def destroy(self) -> None:
        """Mark the root as destroyed."""
        self.destroyed = True

    def run_pending(self) -> None:
        """Run and clear callbacks queued for immediate execution."""
        callbacks = list(self.pending)
        self.pending.clear()
        for callback in callbacks:
            callback()


@pytest.fixture(autouse=True)
def fake_tk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace tkinter widgets with lightweight fakes for headless tests."""
    monkeypatch.setattr(host_gui_module.tk, "StringVar", FakeStringVar)
    monkeypatch.setattr(host_gui_module.tk, "Frame", FakeFrame)
    monkeypatch.setattr(host_gui_module.tk, "LabelFrame", FakeFrame)
    monkeypatch.setattr(host_gui_module.tk, "Label", FakeWidget)
    monkeypatch.setattr(host_gui_module.tk, "Entry", FakeEntry)
    monkeypatch.setattr(host_gui_module.tk, "Button", FakeButton)
    monkeypatch.setattr(host_gui_module.tk, "Listbox", FakeListbox)
    monkeypatch.setattr(host_gui_module.tk, "END", "end")
    monkeypatch.setattr(host_gui_module.tk, "TclError", RuntimeError)


@pytest.fixture
def root() -> FakeRoot:
    """Provide a fresh headless root object."""
    return FakeRoot()


@pytest.fixture
def server() -> Generator[GameServer, None, None]:
    """Start a GameServer on an ephemeral port for host-GUI tests."""
    game_server = GameServer(host="127.0.0.1", port=0)
    game_server.start()
    yield game_server
    game_server.stop()


def _make_quiz(tmp_path: Path, time_limit: float = 0.2) -> Quiz:
    """Write and load a short quiz for host-GUI tests."""
    quiz_data = {
        "title": "Host GUI Quiz",
        "settings": {"pause_between_questions": 0},
        "questions": [
            {
                "text": "What is 2+2?",
                "type": "multiple_choice",
                "options": ["1", "2", "3", "4"],
                "answer": 3,
                "time_limit": time_limit,
            }
        ],
    }
    quiz_file = tmp_path / "host-gui-quiz.json"
    quiz_file.write_text(json.dumps(quiz_data))
    return load_quiz(str(quiz_file))


def _join_player(port: int, code: str, name: str) -> socket.socket:
    """Join one TCP player and return the still-open socket."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(("127.0.0.1", port))
    send_msg(sock, {"type": MsgType.JOIN, "session_code": code, "display_name": name})
    recv_msg(sock)
    return sock


class TestHostGUI:
    """Tests for start-control state and presenter-view updates."""

    def test_start_button_enables_only_at_two_players(
        self, root: FakeRoot, server: GameServer, tmp_path: Path
    ) -> None:
        """The host can start only after a quiz is loaded and two players join."""
        quiz = _make_quiz(tmp_path)
        gui = HostGUI(root, server=server, quiz=quiz)  # type: ignore[arg-type]
        assert gui.start_button.cget("state") == "disabled"

        alice = _join_player(server.port, server.session_code, "Alice")
        gui.refresh_lobby()
        assert gui.start_button.cget("state") == "disabled"

        bob = _join_player(server.port, server.session_code, "Bob")
        recv_msg(alice)
        gui.refresh_lobby()
        assert gui.start_button.cget("state") == "normal"

        alice.close()
        bob.close()
        gui.shutdown()

    def test_start_button_starts_game_only_once(
        self, root: FakeRoot, server: GameServer, tmp_path: Path
    ) -> None:
        """Repeated start attempts reuse the first launched game-loop thread."""
        quiz = _make_quiz(tmp_path, time_limit=0.1)
        gui = HostGUI(root, server=server, quiz=quiz)  # type: ignore[arg-type]
        alice = _join_player(server.port, server.session_code, "Alice")
        bob = _join_player(server.port, server.session_code, "Bob")
        recv_msg(alice)
        gui.refresh_lobby()

        gui.start_game()
        first_thread = gui._game_thread

        assert first_thread is not None
        assert gui.start_button.cget("state") == "disabled"

        gui.start_game()
        assert gui._game_thread is first_thread

        alice.close()
        bob.close()
        gui.shutdown()

    def test_presenter_callbacks_update_the_host_view(
        self, root: FakeRoot, tmp_path: Path
    ) -> None:
        """Observer callbacks update presenter labels and list widgets."""
        quiz = _make_quiz(tmp_path)
        gui = HostGUI(root, quiz=quiz)  # type: ignore[arg-type]

        gui.on_game_started(1)
        root.run_pending()
        assert gui.phase_var.get() == "Question"

        gui.on_question(
            0,
            1,
            "What is 2+2?",
            "multiple_choice",
            ["1", "2", "3", "4"],
            0.2,
        )
        root.run_pending()
        assert "What is 2+2?" in gui.question_var.get()
        assert gui.options_list.size() == 4

        gui.on_timer_tick(0, 0.1, 1, 2)
        root.run_pending()
        assert gui.countdown_var.get() == "Countdown: 0.1s (Q1)"
        assert gui.answered_var.get() == "Answered: 1 / 2"

        gui.on_question_result(
            3,
            [{"name": "Alice", "score": 900}, {"name": "Bob", "score": 0}],
            5,
            True,
            True,
        )
        root.run_pending()
        assert gui.phase_var.get() == "Results"
        assert gui.leaderboard_list.size() == 2

        gui.on_game_over(
            [
                {
                    "rank": 1,
                    "name": "Alice",
                    "score": 900,
                    "correct": 1,
                    "streak": 1,
                    "fastest_answer": 0.1,
                }
            ],
            1,
        )
        root.run_pending()
        assert gui.phase_var.get() == "Game Over"
        assert gui.leaderboard_list.size() == 1

        gui.shutdown()
