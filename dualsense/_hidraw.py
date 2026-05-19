"""Direct /dev/hidraw shim - Linux fallback for the libusb-built hidapi wheel
that can't claim the gamepad interface owned by the hid-playstation driver."""
import glob
import os


def enumerate(vendor_id: int = 0, product_id: int = 0) -> list[dict]:
    out = []
    for node in sorted(glob.glob("/dev/hidraw*")):
        try:
            with open(f"/sys/class/hidraw/{os.path.basename(node)}/device/uevent") as f:
                hid_id = next(l for l in f if l.startswith("HID_ID=")).strip()[7:]
            bus, vid, pid = (int(p, 16) for p in hid_id.split(":"))
        except (OSError, StopIteration, ValueError):
            continue
        if (vendor_id and vid != vendor_id) or (product_id and pid != product_id):
            continue
        out.append({"path": node.encode(), "product_id": pid, "bus_type": bus})
    return out


class device:
    _fd = -1

    def open_path(self, path):
        self._fd = os.open(path.decode(), os.O_RDWR | os.O_NONBLOCK)

    def set_nonblocking(self, _nb): pass

    def write(self, data):
        return os.write(self._fd, bytes(data))

    def read(self, size, timeout_ms=0):
        try:
            return os.read(self._fd, size)
        except BlockingIOError:
            return b""

    def close(self):
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1
