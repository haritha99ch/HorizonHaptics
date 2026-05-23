"""UDP receive loop + DualSense writer. v1.2.2"""

import logging
import socket
import threading
import time
from dataclasses import dataclass, field

from dualsense.main import DualSense
from dualsense.triggers import off
from GameParsers.forza_parser import DataPacket, FH6_PACKET_SIZE, parse
from GameParsers.parser import Parser
from Config import BrakeSettings, GearSettings, SurfaceSettings, ThrottleSettings

log = logging.getLogger("hh.worker")

UDP_HOST = "0.0.0.0"
UDP_PORT = 5300
_OFF = off()


@dataclass
class State:
    """Thread-safe snapshot shared between Worker and the TUI.

    Settings (throttle / brake) are intentionally NOT lock-protected: the TUI
    writes simple scalar attributes while the worker reads them each frame.
    Python's GIL makes those assignments atomic for ints/floats/enums.
    Everything else (ds_connected, receiving, ...) is read under .lock.
    """
    lock: threading.Lock = field(default_factory=threading.Lock)

    # Live settings - modified by the TUI, read by the Parser each frame
    throttle: ThrottleSettings = field(default_factory=ThrottleSettings)
    brake: BrakeSettings = field(default_factory=BrakeSettings)
    gear: GearSettings = field(default_factory=GearSettings)
    surface: SurfaceSettings = field(default_factory=SurfaceSettings)

    # Status (read under .lock)
    ds_connected: bool = False
    receiving: bool = False
    last_addr: str = ""
    pkt_count: int = 0


class Worker:
    """Runs the UDP receive loop and DualSense writes in a background thread.

    Detects FH6 packets by length (324 bytes), parses them, computes effects
    via Parser, pushes them to the DualSense.

    The Parser holds references to state.throttle and state.brake, so any
    setting the TUI changes is picked up on the very next frame.
    """

    def __init__(self, state: State, port: int = UDP_PORT):
        self._state = state
        self._port = port
        self._ds = DualSense()
        # Parser keeps references - not copies - so TUI changes propagate live
        self._parser = Parser(state.throttle, state.brake, state.gear, state.surface)
        self._thread = threading.Thread(target=self._run, daemon=True, name="hh-worker")

    def start(self):
        self._ds.open()
        self._thread.start()
        log.info("Worker started - UDP %s:%d", UDP_HOST, self._port)

    def stop(self):
        self._ds.set(_OFF, _OFF)
        self._ds.close()

    def _run(self):
        OFF_PAIR = _OFF, _OFF
        prev = None

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        sock.bind((UDP_HOST, self._port))
        sock.settimeout(0.5)

        log.info("Listening on %s:%d - waiting for FH6 packets", UDP_HOST, self._port)
        last_pkt = time.monotonic()

        try:
            while True:
                with self._state.lock:
                    self._state.ds_connected = self._ds.connected

                try:
                    data, addr = sock.recvfrom(512)
                except socket.timeout:
                    if time.monotonic() - last_pkt > 2.0:
                        with self._state.lock:
                            self._state.receiving = False
                    if prev != OFF_PAIR:
                        self._ds.set(*OFF_PAIR)
                        prev = OFF_PAIR
                    continue

                if len(data) != FH6_PACKET_SIZE:
                    continue

                last_pkt = time.monotonic()

                try:
                    pkt = parse(data)
                except Exception as exc:
                    log.debug("Parse error: %s", exc)
                    continue

                self._ds.allow_steam_rumble = self._state.surface.allow_steam_rumble
                pair = self._parser.compute(pkt) if pkt.is_race_on else OFF_PAIR
                if pair != prev:
                    self._ds.set(*pair)
                    prev = pair

                with self._state.lock:
                    self._state.receiving = True
                    self._state.last_addr = f"{addr[0]}:{addr[1]}"
                    self._state.pkt_count += 1

        except Exception:
            log.exception("Worker crashed")
        finally:
            sock.close()
