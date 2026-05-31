"""UDP receive loop + DualSense writer. v1.2.2"""

import logging
import socket
import threading
import time
from dataclasses import dataclass, field

from dualsense.main import DualSense
from dualsense.triggers import off
from dualsense.audio import AudioHaptics
from GameParsers.forza_parser import DataPacket, FH6_PACKET_SIZE, parse
from GameParsers.parser import Parser
from Config import BrakeSettings, GearSettings, SurfaceSettings, ThrottleSettings, TachometerSettings

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
    tachometer: TachometerSettings = field(default_factory=TachometerSettings)

    # Status (read under .lock)
    ds_connected: bool = False
    receiving: bool = False
    last_addr: str = ""
    pkt_count: int = 0
    running: bool = True


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
        self._audio = AudioHaptics()
        # Parser keeps references - not copies - so TUI changes propagate live
        self._parser = Parser(state.throttle, state.brake, state.gear, state.surface, state.tachometer)
        self._thread = threading.Thread(target=self._run, daemon=True, name="hh-worker")

    def start(self):
        self._ds.open()
        self._audio.start()
        self._thread.start()
        log.info("Worker started - UDP %s:%d", UDP_HOST, self._port)

    def stop(self):
        self._state.running = False
        self._ds.set(_OFF, _OFF, 0, 0, 0)
        self._ds.close()
        self._audio.stop()

    def _run(self):
        OFF_PAIR = _OFF, _OFF, 0, 0, 0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        prev = None

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        sock.bind((UDP_HOST, self._port))
        sock.settimeout(0.5)

        log.info("Listening on %s:%d - waiting for FH6 packets", UDP_HOST, self._port)
        last_pkt = time.monotonic()

        try:
            while self._state.running:
                with self._state.lock:
                    if self._ds.connected:
                        self._ds.allow_steam_rumble = self._state.surface.allow_steam_rumble and not self._state.surface.enable_body_haptics
                        self._state.ds_connected = True
                    else:
                        self._state.ds_connected = False

                try:
                    data, addr = sock.recvfrom(512)
                except socket.timeout:
                    if time.monotonic() - last_pkt > 2.0:
                        with self._state.lock:
                            self._state.receiving = False
                    if prev != OFF_PAIR:
                        self._ds.set(*OFF_PAIR[:8])
                        self._audio.set_haptics(*OFF_PAIR[8:])
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
                data_out = self._parser.compute(pkt)
                if data_out != prev:
                    self._ds.set(*data_out[:8])
                    self._audio.set_haptics(*data_out[8:])
                    prev = data_out

                with self._state.lock:
                    self._state.receiving = True
                    self._state.last_addr = f"{addr[0]}:{addr[1]}"
                    self._state.pkt_count += 1

        except Exception:
            log.exception("Worker crashed")
        finally:
            sock.close()
