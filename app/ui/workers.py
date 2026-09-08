"""Reusable Qt thread-pool workers for database and document work."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, SignalInstance, Slot
from shiboken6 import isValid

logger = logging.getLogger(__name__)


class WorkerSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal()


class FunctionWorker(QRunnable):
    def __init__(self, function: Callable[[], Any]) -> None:
        super().__init__()
        self._function = function
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self._function()
        except Exception as exc:
            logger.error("Background operation failed\n%s", traceback.format_exc())
            self._emit(self.signals.failed, exc)
        else:
            self._emit(self.signals.succeeded, result)
        finally:
            self._emit(self.signals.finished)

    def _emit(self, signal: SignalInstance, *arguments: Any) -> None:
        """Deliver a result unless the screen waiting for it has been closed.

        Logging out or navigating away destroys a screen while its query may still
        be running; emitting into the deleted receiver then raises from a pool
        thread, where nothing can handle it.
        """

        if not isValid(self.signals):
            return
        try:
            signal.emit(*arguments)
        except RuntimeError:
            # The receiver was destroyed between the check and the emit.
            logger.debug("Dropped a background result for a closed screen")


#: Every worker that is still running. ``QThreadPool`` does not own the Python
#: object, so a screen that starts two operations in a row -- overwriting its
#: single ``self._worker`` attribute -- would let the first one be collected and
#: silently lose its result. Workers stay referenced here until they finish.
_ACTIVE_WORKERS: set[FunctionWorker] = set()


def active_worker_count() -> int:
    """Return how many workers are still in flight; used by tests."""

    return len(_ACTIVE_WORKERS)


def start_worker(
    function: Callable[[], Any],
    *,
    succeeded: Callable[[Any], object],
    failed: Callable[[Exception], object],
    finished: Callable[[], object] | None = None,
) -> FunctionWorker:
    worker = FunctionWorker(function)
    worker.signals.succeeded.connect(succeeded)
    worker.signals.failed.connect(failed)
    if finished:
        worker.signals.finished.connect(finished)
    _ACTIVE_WORKERS.add(worker)
    worker.signals.finished.connect(lambda: _ACTIVE_WORKERS.discard(worker))
    QThreadPool.globalInstance().start(worker)
    return worker
