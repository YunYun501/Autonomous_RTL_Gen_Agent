"""Esc-to-terminate support.

Watches the console for an Esc keypress in a background thread and signals
cancellation. The controller checks this flag between steps and aborts the current
task cleanly (status ABORTED). Windows-only via msvcrt; on other platforms it is a
no-op and Ctrl-C remains the interrupt path.

Reading is paused during interactive clarification prompts so the watcher never
steals keystrokes from prompt_toolkit.
"""

from __future__ import annotations

import threading

try:
    import msvcrt

    _HAS_MSVCRT = True
except ImportError:  # non-Windows
    msvcrt = None
    _HAS_MSVCRT = False

ESC = "\x1b"


class CancelWatcher:
    def __init__(self, poll: float = 0.05):
        self._cancel = threading.Event()
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._poll = poll
        self._thread: threading.Thread | None = None

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    @property
    def available(self) -> bool:
        return _HAS_MSVCRT

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def __enter__(self) -> "CancelWatcher":
        if _HAS_MSVCRT:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.3)
        self._drain()

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._paused.is_set():
                self._stop.wait(self._poll)
                continue
            if msvcrt.kbhit():
                try:
                    ch = msvcrt.getwch()
                except Exception:  # noqa: BLE001
                    ch = ""
                if ch == ESC:
                    self._cancel.set()
                    return
            else:
                self._stop.wait(self._poll)

    def _drain(self) -> None:
        if not _HAS_MSVCRT:
            return
        try:
            while msvcrt.kbhit():
                msvcrt.getwch()
        except Exception:  # noqa: BLE001
            pass
