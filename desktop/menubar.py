"""
Letterboxd Recommender — macOS menu bar app.

Manages bundled Redis + uvicorn + Celery as subprocesses.
The FastAPI app itself is run as a subprocess — this file is only the menu bar shell.
"""

import os
import sys
import time
import socket
import threading
import subprocess
import webbrowser
from pathlib import Path

import rumps

# ── Configuration ──────────────────────────────────────────────────────────────

APP_NAME = "Letterboxd Recommender"
APP_PORT = 8020
DATA_DIR = Path.home() / "Library" / "Application Support" / APP_NAME


def _resources_dir() -> Path:
    """Return the Resources directory: bundle path in production, repo root in dev."""
    resource_path = os.environ.get("RESOURCEPATH")
    if resource_path:
        return Path(resource_path)
    # Running from source: two levels up from this file (desktop/ → repo root)
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

        # Start background thread
        t = threading.Thread(target=self._start, daemon=True)
        t.start()

    # ── Subprocess management ──────────────────────────────────────────────────

    def _start(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        env = {
            **os.environ,
            "DATABASE_URL": f"sqlite:///{DATA_DIR}/letterboxd_rec.db",
            "REDIS_URL": "redis://127.0.0.1:6379/0",
            "PORT": str(APP_PORT),
            "PYTHONPATH": str(_app_dir()),
        }

        python = str(_python_exe())
        app_dir = str(_app_dir())

        # ── Redis ──────────────────────────────────────────────────────────────
        self._set_status("Starting Redis…")
        redis_bin = str(_redis_bin())
        redis_proc = subprocess.Popen(
            [redis_bin, "--port", "6379", "--daemonize", "no"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._procs.append(redis_proc)

        # Give Redis a moment
        for _ in range(20):
            if _port_open(6379):
                break
            time.sleep(0.25)
        else:
            self._set_status("Redis failed to start")
            return

        # ── uvicorn ────────────────────────────────────────────────────────────
        self._set_status("Starting web server…")
        web_proc = subprocess.Popen(
            [
                python, "-m", "uvicorn",
                "app.main:app",
                "--host", "127.0.0.1",
                "--port", str(APP_PORT),
                "--workers", "1",
            ],
            cwd=app_dir,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._procs.append(web_proc)

        # ── Celery worker ──────────────────────────────────────────────────────
        self._set_status("Starting task worker…")
        worker_proc = subprocess.Popen(
            [
                python, "-m", "celery",
                "-A", "app.tasks.celery_app",
                "worker",
                "--loglevel=error",
                "--concurrency=2",
            ],
            cwd=app_dir,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._procs.append(worker_proc)

        # ── Celery beat ────────────────────────────────────────────────────────
        beat_proc = subprocess.Popen(
            [
                python, "-m", "celery",
                "-A", "app.tasks.celery_app",
                "beat",
                "--loglevel=error",
                "--schedule", str(DATA_DIR / "celerybeat-schedule"),
            ],
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
                break
            time.sleep(0.5)
        else:
            self._set_status("Server failed to start")
            return

        self._ready = True
        self._set_status(f"Running on port {APP_PORT}")
        webbrowser.open(f"http://127.0.0.1:{APP_PORT}")

    def _stop(self):
        self._ready = False
        self._set_status("Shutting down…")
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
        self._status.title = text


if __name__ == "__main__":
    LetterboxdApp().run()
