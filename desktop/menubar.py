"""
Letterboxd Recommender — macOS menu bar app.

Manages bundled Redis + uvicorn + Celery as subprocesses.
Logs to ~/Library/Logs/Letterboxd Recommender/app.log
"""

import os
import sys
import time
import socket
import logging
import threading
import subprocess
import webbrowser
from pathlib import Path

import rumps

# ── Logging ────────────────────────────────────────────────────────────────────

APP_NAME = "Letterboxd Recommender"
LOG_DIR = Path.home() / "Library" / "Logs" / APP_NAME
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "app.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────

APP_PORT = 8020
DATA_DIR = Path.home() / "Library" / "Application Support" / APP_NAME


def _resources_dir() -> Path:
    """Return the Resources directory: bundle path in production, script parent in dev."""
    resource_path = os.environ.get("RESOURCEPATH")
    if resource_path:
        return Path(resource_path)
    return Path(__file__).parent.parent


def _python_exe() -> Path:
    return _resources_dir() / "python" / "bin" / "python3"


def _redis_bin() -> Path:
    return _resources_dir() / "bin" / "redis-server"


def _app_dir() -> Path:
    """Directory that contains the 'app' package (i.e. PYTHONPATH root)."""
    return _resources_dir() / "src"


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


# ── Menu bar app ───────────────────────────────────────────────────────────────

class LetterboxdApp(rumps.App):
    def __init__(self):
        self._procs: list[subprocess.Popen] = []
        self._ready = False
        self._status = rumps.MenuItem("Starting…")
        self._open_btn = rumps.MenuItem("Open Recommender", callback=self._open_browser)

        super().__init__(
            name=APP_NAME,
            title="🎬",
            menu=[
                self._open_btn,
                None,  # separator
                self._status,
                None,  # separator
                rumps.MenuItem("Quit", callback=self._quit),
            ],
            quit_button=None,
        )

        log.info("App initialised — starting services in background thread")
        t = threading.Thread(target=self._start, daemon=True)
        t.start()

    # ── Subprocess management ──────────────────────────────────────────────────

    def _start(self):
        try:
            self._start_services()
        except Exception:
            log.exception("Fatal error starting services")
            self._set_status("Failed to start — see log")

    def _start_services(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        log.info("Data dir: %s", DATA_DIR)
        log.info("Resources dir: %s", _resources_dir())
        log.info("Python: %s", _python_exe())
        log.info("Redis: %s", _redis_bin())
        log.info("App src: %s", _app_dir())

        env = {
            **os.environ,
            "DATABASE_URL": f"sqlite:///{DATA_DIR}/letterboxd_rec.db",
            "REDIS_URL": "redis://127.0.0.1:6379/0",
            "PORT": str(APP_PORT),
            "PYTHONPATH": str(_app_dir()),
        }

        python = str(_python_exe())
        app_dir = str(_app_dir())

        # ── Validate paths ─────────────────────────────────────────────────────
        if not _python_exe().exists():
            raise FileNotFoundError(f"Bundled Python not found: {_python_exe()}")
        if not _redis_bin().exists():
            raise FileNotFoundError(f"Bundled redis-server not found: {_redis_bin()}")
        if not _app_dir().exists():
            raise FileNotFoundError(f"App source not found: {_app_dir()}")

        # ── Redis ──────────────────────────────────────────────────────────────
        self._set_status("Starting Redis…")
        log.info("Starting Redis")
        redis_proc = subprocess.Popen(
            [str(_redis_bin()), "--port", "6379", "--daemonize", "no"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self._procs.append(redis_proc)

        for _ in range(20):
            if _port_open(6379):
                log.info("Redis ready")
                break
            time.sleep(0.25)
        else:
            out, _ = redis_proc.communicate(timeout=2)
            log.error("Redis failed to start. Output: %s", out.decode(errors="replace"))
            self._set_status("Redis failed to start — see log")
            return

        # ── uvicorn ────────────────────────────────────────────────────────────
        self._set_status("Starting web server…")
        log.info("Starting uvicorn on port %s", APP_PORT)
        web_proc = subprocess.Popen(
            [python, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(APP_PORT), "--workers", "1"],
            cwd=app_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self._procs.append(web_proc)

        # ── Celery worker ──────────────────────────────────────────────────────
        self._set_status("Starting task worker…")
        log.info("Starting Celery worker")
        worker_proc = subprocess.Popen(
            [python, "-m", "celery", "-A", "app.tasks.celery_app",
             "worker", "--loglevel=error", "--concurrency=2"],
            cwd=app_dir,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._procs.append(worker_proc)

        # ── Celery beat ────────────────────────────────────────────────────────
        log.info("Starting Celery beat")
        beat_proc = subprocess.Popen(
            [python, "-m", "celery", "-A", "app.tasks.celery_app",
             "beat", "--loglevel=error",
             "--schedule", str(DATA_DIR / "celerybeat-schedule")],
            cwd=app_dir,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._procs.append(beat_proc)

        # ── Wait for web server ────────────────────────────────────────────────
        self._set_status("Waiting for server…")
        for _ in range(60):
            if _port_open(APP_PORT):
                log.info("Web server ready on port %s", APP_PORT)
                break
            # Check if uvicorn crashed early
            if web_proc.poll() is not None:
                out = web_proc.stdout.read() if web_proc.stdout else b""
                log.error("uvicorn exited early. Output:\n%s", out.decode(errors="replace"))
                self._set_status("Server crashed — see log")
                return
            time.sleep(0.5)
        else:
            log.error("Timed out waiting for server on port %s", APP_PORT)
            self._set_status("Server timed out — see log")
            return

        self._ready = True
        self._set_status(f"Running — port {APP_PORT}")
        webbrowser.open(f"http://127.0.0.1:{APP_PORT}")

    def _stop(self):
        self._ready = False
        self._set_status("Shutting down…")
        log.info("Shutting down %d subprocesses", len(self._procs))
        for proc in reversed(self._procs):
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._procs.clear()

    # ── Menu callbacks ─────────────────────────────────────────────────────────

    def _open_browser(self, _=None):
        if self._ready:
            webbrowser.open(f"http://127.0.0.1:{APP_PORT}")

    def _quit(self, _):
        self._stop()
        rumps.quit_application()

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _set_status(self, text: str):
        log.info("Status: %s", text)
        self._status.title = text


if __name__ == "__main__":
    log.info("=== Letterboxd Recommender starting ===")
    log.info("Resources: %s", _resources_dir())
    try:
        LetterboxdApp().run()
    except Exception:
        log.exception("Unhandled exception in main")
        sys.exit(1)
