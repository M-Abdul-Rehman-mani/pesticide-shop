"""Reusable Qt thread-pool workers for database and document work."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

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
            self.signals.failed.emit(exc)
        else:
            self.signals.succeeded.emit(result)
        finally:
            self.signals.finished.emit()


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
    QThreadPool.globalInstance().start(worker)
    return worker
