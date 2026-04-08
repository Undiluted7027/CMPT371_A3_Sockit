"""Manual smoke test for host_gui.py.

Launches the host GUI with a temporary quiz already loaded so the host can:
1. verify the session code/address are visible
2. join two player clients externally
3. confirm Start Game enables at 2+ players
4. click Start Game and watch presenter updates through game over

Usage:
    .venv/bin/python scripts/smoke_test_host_gui.py
"""

from __future__ import annotations

import json
import sys
import tempfile

sys.path.insert(0, ".")

from src.host_gui import launch_host_app  # noqa: E402


def main() -> None:
    """Create a temporary quiz and launch the host GUI."""
    quiz_data = {
        "title": "Host GUI Smoke Quiz",
        "settings": {"pause_between_questions": 1},
        "questions": [
            {
                "text": "What is 2 + 2?",
                "type": "multiple_choice",
                "options": ["1", "2", "3", "4"],
                "answer": 3,
                "time_limit": 5,
            },
            {
                "text": "Capital of France?",
                "type": "multiple_choice",
                "options": ["Berlin", "Madrid", "Paris", "Rome"],
                "answer": 2,
                "time_limit": 5,
            },
        ],
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as handle:
        json.dump(quiz_data, handle)
        quiz_path = handle.name

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5001

    print("\nSmoke test - Host GUI\n")
    print("1. Confirm the host window shows the quiz title and session metadata.")
    print("2. Join two players from separate terminals or player clients.")
    print("3. Confirm Start Game enables once both players are present.")
    print("4. Click Start Game and verify the presenter view updates live.\n")
    launch_host_app(quiz_path, port=port)


if __name__ == "__main__":
    main()
