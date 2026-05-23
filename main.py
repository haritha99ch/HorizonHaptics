"""HorizonHaptics v1.2.2 - entry point.

Usage:
  uv run main.py               # Qt GUI (default)
  uv run main.py --no-gui      # headless console mode
  uv run main.py --port 5301   # custom UDP port
  uv run main.py --light       # light theme instead of dark

FH6 setup:
  Settings -> HUD and gameplay -> Data Out -> ON
  IP: this machine's IP,  Port: 5300
"""

import argparse
import logging

import preferences
from worker import State, Worker

log = logging.getLogger("hh")


def _make_state() -> State:
    state = State()
    preferences.load(state.throttle, state.brake, state.gear, state.surface)
    return state


def run_gui(port: int, dark: bool = True):
    """Qt desktop GUI mode."""
    from ui import run_qt
    state = _make_state()
    worker = Worker(state, port=port)
    worker.start()
    run_qt(state, worker, dark=dark, port=port)


def run_headless(port: int, debug: bool):
    """Headless mode - logs to console, blocks until Ctrl+C."""
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    state = _make_state()
    worker = Worker(state, port=port)
    worker.start()
    try:
        worker._thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        worker.stop()
        log.info("Stopped.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="HorizonHaptics - FH6 DualSense adaptive triggers")
    p.add_argument("--port",   type=int, default=5300, help="UDP port (default: 5300)")
    p.add_argument("--no-gui", action="store_true",    help="Headless mode, console logs")
    p.add_argument("--light",  action="store_true",    help="Light theme (default: dark)")
    p.add_argument("--debug",  action="store_true",    help="Verbose logging")
    args = p.parse_args()

    if args.no_gui:
        run_headless(args.port, args.debug)
    else:
        run_gui(args.port, dark=not args.light)
