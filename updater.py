"""Auto-update: check GitHub releases, download zip, patch in place, restart. v1.2.2"""

import json
import logging
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

log = logging.getLogger("hh.updater")

_REPO = "haritha99ch/HorizonHaptics"
_API_URL = f"https://api.github.com/repos/{_REPO}/releases/latest"
_STAGING = Path.home() / ".config" / "horizonhaptics" / "update"
_APP_DIR = Path(__file__).parent.resolve()

# Helper script written to a temp file and launched detached after the app exits.
# It waits, extracts the zip over the app directory (skipping .venv), then relaunches.
_HELPER = '''\
import shutil, subprocess, sys, time, zipfile
from pathlib import Path

zip_path, app_dir, launcher = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]

time.sleep(3)

with zipfile.ZipFile(zip_path) as zf:
    for member in zf.namelist():
        rel = Path(member)
        if not rel.parts or str(rel).startswith(".venv"):
            continue
        dest = app_dir / rel
        if member.endswith("/"):
            dest.mkdir(parents=True, exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)

Path(zip_path).unlink(missing_ok=True)

if sys.platform == "win32":
    subprocess.Popen([launcher], shell=True, cwd=str(app_dir),
                     creationflags=subprocess.CREATE_NEW_CONSOLE)
else:
    subprocess.Popen([launcher], cwd=str(app_dir))

Path(__file__).unlink(missing_ok=True)
'''


def _parse_version(tag: str) -> tuple:
    return tuple(int(x) for x in tag.lstrip("v").split(".") if x.isdigit())


def check_for_update() -> tuple[str, str] | None:
    """Return (tag, download_url) if a newer release exists, else None."""
    from version import __version__
    try:
        req = urllib.request.Request(_API_URL, headers={"User-Agent": "HorizonHaptics"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        tag = data.get("tag_name", "")
        if not tag or _parse_version(tag) <= _parse_version(__version__):
            return None
        for asset in data.get("assets", []):
            if asset.get("name", "").endswith(".zip"):
                return tag, asset["browser_download_url"]
    except Exception as exc:
        log.debug("Update check failed: %s", exc)
    return None


def download_update(url: str, tag: str, progress_cb=None) -> Path | None:
    """Download zip to staging dir. progress_cb(pct: float) called during transfer."""
    try:
        _STAGING.mkdir(parents=True, exist_ok=True)
        dest = _STAGING / f"HorizonHaptics-{tag}.zip"
        req = urllib.request.Request(url, headers={"User-Agent": "HorizonHaptics"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length", 0) or 0)
            downloaded = 0
            with open(dest, "wb") as fh:
                while True:
                    buf = resp.read(65536)
                    if not buf:
                        break
                    fh.write(buf)
                    downloaded += len(buf)
                    if progress_cb and total:
                        progress_cb(downloaded / total * 100)
        return dest
    except Exception as exc:
        log.error("Download failed: %s", exc)
        return None


def apply_and_restart(zip_path: Path) -> None:
    """Write helper script, launch it detached. Caller must quit the app after this."""
    helper = tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, prefix="hh_update_"
    )
    helper.write(_HELPER)
    helper.close()

    launcher = str(_APP_DIR / ("run.bat" if sys.platform == "win32" else "run.sh"))
    args = [sys.executable, helper.name, str(zip_path), str(_APP_DIR), launcher]

    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(args, creationflags=flags, close_fds=True)
    else:
        subprocess.Popen(args, start_new_session=True, close_fds=True)

    log.info("Update helper launched - restarting.")


def start_check(callback) -> None:
    """Check for updates in background. callback(tag, url) called if update found."""
    def _run():
        result = check_for_update()
        if result:
            callback(*result)
    threading.Thread(target=_run, daemon=True, name="hh-update-check").start()
