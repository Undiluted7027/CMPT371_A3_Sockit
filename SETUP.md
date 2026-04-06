# Setup

## 1. Prerequisites (Fresh Environment)

To run this project, you need:

* WSL2 on Windows, macOS, or Linux (with Wayland) — any OS with `tkinter` support
* **Python 3.10** or higher
* **Tkinter** (ships with most Python installers; see [Tkinter Troubleshooting](#tkinter-troubleshooting) if missing)
* No external pip installations are required to run the app — only standard library modules are used (`socket`, `threading`, `json`, `sys`, `argparse`, `uuid`, `time`)
* If you wish to run tests, install `pytest` via `requirements.txt` (see [Running Tests](#running-tests))
* (Optional) VS Code or any terminal emulator

---

## 2. Step-by-Step Run Guide

### Step 1: Clone the repository

```bash
git clone <repo-url>
cd <repo-folder>
```

### Step 2: Set up the environment

```bash
# On Linux / WSL2 / macOS
chmod +x setup.sh
./setup.sh
source .venv/bin/activate
```

The script will:
- Verify Python 3.10+ is installed
- Create a virtual environment at `.venv/`
- Install dependencies from `requirements.txt`
- Warn you if `tkinter` is not detected (see below)

> [!IMPORTANT]
> **Tkinter installation**
> If the script does not detect tkinter, it **will not install it automatically**. It will print a warning with the exact install command for your OS. For the most reliable experience, install tkinter before proceeding. Refer to [Tkinter Troubleshooting](#tkinter-troubleshooting) or the [tkinter documentation](https://docs.python.org/3/library/tkinter.html).


Check [Makefile](./Makefile) for short commands to ease development.

### Step 3: Start the server (host)

The host launches the game server and loads a quiz file.

```bash
python main.py --host --quiz quizzes/sample.json
```

- The server will print a **session code** on startup — share this with players.
- The host GUI will open showing the lobby and a **Start Game** button (enabled once 2+ players have joined).

### Step 4: Connect as a player

Each player runs the following on their machine (or in a separate terminal):

```bash
python main.py
```

- Enter the **server IP address**, the **session code**, and a **display name** in the GUI.
- Wait in the lobby until the host starts the game.

> **Running locally (same machine):** Use `127.0.0.1` as the server IP. Open multiple terminals and run `python main.py` in each to simulate multiple players.

> **Running on a LAN:** Players use the host machine's local IP (e.g. `192.168.x.x`). Find it with `ip addr` (Linux/WSL) or `ipconfig` (Windows) or `ifconfig` (macOS).

### Step 5: Play

Once the host clicks **Start Game**:
1. Questions appear one at a time with a countdown timer.
2. Players select an answer — faster correct answers score more points.
3. After each question, the correct answer and leaderboard are shown.
4. At the end, final rankings and stats are displayed.

---

## Running Tests

Install test dependencies first:

```bash
pip install -r requirements.txt
```

Then run:

```bash
pytest
```

---

## Quiz File Format

Quiz files are plain JSON. A sample is provided at `quizzes/sample.json`.

```json
{
  "title": "Sample Quiz",
  "description": "A short example quiz",
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

| Field | Description |
|---|---|
| `type` | `"multiple_choice"` (4 options) or `"true_false"` (2 options) |
| `answer` | Integer index into `options` (0-based) |
| `time_limit` | Seconds for this question; overrides `default_time_limit` if set |

---

## Tkinter Troubleshooting

Tkinter ships with Python on Windows and macOS. On Linux/WSL2 it may need a separate install:

| Platform | Command |
|---|---|
| Debian / Ubuntu / WSL2 | `sudo apt-get install python3-tk` |
| Fedora / RHEL | `sudo dnf install python3-tkinter` |
| macOS (Homebrew) | `brew install python-tk` |
| WSL1 | Not supported — WSL1 has no display server. Use WSL2 or run natively. |
| WSL2 on Windows 10 | Requires an X server (e.g. [VcXsrv](https://sourceforge.net/projects/vcxsrv/)). Set `export DISPLAY=:0` before running. |
| WSL2 on Windows 11 | Works out of the box via WSLg. |

After installing tkinter, re-run `./setup.sh` to confirm it is detected.
