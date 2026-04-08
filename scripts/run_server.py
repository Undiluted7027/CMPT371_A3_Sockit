"""Run the BrainZap game server as a standalone process.

    python scripts/run_server.py [PORT]

PORT defaults to 5000. The server prints the session code and address on
startup. Press Ctrl+C to stop.

Example:
    python scripts/run_server.py
    python scripts/run_server.py 3000

"""

import logging
import sys
import threading

sys.path.insert(0, ".")

from src.server import DEFAULT_PORT, GameServer  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
)

port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
server = GameServer(port=port)
server.start()

print(f"\n  Session code : {server.session_code}")
print(f"  Listening on : 0.0.0.0:{server.port}  (UDP same port)")
print("  Press Ctrl+C to stop\n")

try:
    threading.Event().wait()
except KeyboardInterrupt:
    print("\nShutting down...")
    server.stop()
