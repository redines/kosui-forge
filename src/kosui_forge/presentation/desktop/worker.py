"""Qt worker boundary for the read-only Doctor service."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from PySide6.QtCore import QObject, QThread, Signal

from kosui_forge.application.contracts import (
    CancellationToken,
    DoctorRequest,
    OperationEvent,
    OperationResult,
)


class DoctorRunner(Protocol):
    """Application-service shape consumed by the desktop presentation."""

    def run(
        self,
        request: DoctorRequest,
        *,
        progress: Callable[[OperationEvent], None] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> OperationResult: ...


class DoctorThread(QThread):
    """Invoke Doctor in a managed thread and relay immutable contracts to the GUI."""

    progress = Signal(object)
    completed = Signal(object)
    failed = Signal()

    def __init__(
        self,
        service: DoctorRunner,
        request: DoctorRequest,
        cancellation: CancellationToken,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._request = request
        self._cancellation = cancellation

    def run(self) -> None:
        try:
            result = self._service.run(
                self._request,
                progress=self._report_progress,
                cancellation=self._cancellation,
            )
        except Exception:
            # Unexpected adapter exceptions may contain credentials. The application
            # service normally maps them to a redacted result, so fail closed here.
            self.failed.emit()
            return
        self.completed.emit(result)

    def _report_progress(self, event: OperationEvent) -> None:
        self.progress.emit(event)
