"""Global keyboard/mouse activity monitor for automatic logout."""

from PySide6.QtCore import QEvent, QObject, QTimer, Signal


class SessionTimeoutMonitor(QObject):
    timed_out = Signal()

    def __init__(self, timeout_minutes: int, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(timeout_minutes * 60 * 1000)
        self._timer.timeout.connect(self.timed_out)
        self._timer.start()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() in {
            QEvent.Type.KeyPress,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseMove,
            QEvent.Type.Wheel,
            QEvent.Type.TouchBegin,
        }:
            self._timer.start()
        return super().eventFilter(watched, event)
