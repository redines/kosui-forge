"""Functional Doctor dashboard backed by the shared application service."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from kosui_forge.application.contracts import (
    CancellationToken,
    DoctorCheck,
    DoctorRequest,
    OperationEvent,
    OperationResult,
    OperationStatus,
)

from .doctor_model import DoctorPresentationModel
from .worker import DoctorRunner, DoctorThread


class DoctorPage(QWidget):
    """Run and render read-only Doctor checks without blocking the GUI thread."""

    idle = Signal()
    run_state_changed = Signal(bool)

    def __init__(self, service: DoctorRunner, *, config_path: Path) -> None:
        super().__init__()
        self._service = service
        self._thread: DoctorThread | None = None
        self._cancellation: CancellationToken | None = None
        self._model = DoctorPresentationModel()
        self._running = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        heading = QLabel("Doctor")
        heading_font = heading.font()
        heading_font.setPointSize(heading_font.pointSize() + 6)
        heading_font.setBold(True)
        heading.setFont(heading_font)
        description = QLabel(
            "Check configuration, tools, provider access, and repository readiness. "
            "Doctor is read-only and changes no resources."
        )
        description.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(description)

        form = QFormLayout()
        self.config_path_edit = QLineEdit(str(config_path))
        self.config_path_edit.setAccessibleName("Doctor configuration path")
        self.config_path_edit.setPlaceholderText("Path to repo-bootstrap config.toml")
        form.addRow("Configuration", self.config_path_edit)
        self.repository_name_edit = QLineEdit()
        self.repository_name_edit.setAccessibleName("Optional repository name")
        self.repository_name_edit.setPlaceholderText(
            "Optional repository readiness check"
        )
        form.addRow("Repository", self.repository_name_edit)
        self.include_github_checkbox = QCheckBox("Include GitHub checks")
        self.include_github_checkbox.setAccessibleName("Include GitHub checks")
        self.include_github_checkbox.setChecked(True)
        form.addRow("", self.include_github_checkbox)
        layout.addLayout(form)

        actions = QHBoxLayout()
        self.run_button = QPushButton("Run Doctor")
        self.run_button.setAccessibleName("Run Doctor")
        self.run_button.setToolTip("Run read-only diagnostics")
        self.run_button.clicked.connect(self.start)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setAccessibleName("Cancel Doctor")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.request_cancellation)
        actions.addWidget(self.run_button)
        actions.addWidget(self.cancel_button)
        actions.addStretch(1)
        self.elapsed_label = QLabel("Elapsed: 0.00 s")
        self.elapsed_label.setAccessibleName("Doctor elapsed time")
        actions.addWidget(self.elapsed_label)
        layout.addLayout(actions)

        self.status_label = QLabel("Ready to run read-only diagnostics")
        self.status_label.setAccessibleName("Doctor status")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setAccessibleName("Doctor progress")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.checks_table = QTableWidget(0, 4)
        self.checks_table.setHorizontalHeaderLabels(
            ("Status", "Check", "Detail", "Remediation")
        )
        self.checks_table.setAccessibleName("Doctor check results")
        self.checks_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.checks_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.checks_table.setAlternatingRowColors(True)
        header = self.checks_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.checks_table, 2)

        diagnostics_header = QHBoxLayout()
        diagnostics_header.addWidget(QLabel("Redacted diagnostic"))
        diagnostics_header.addStretch(1)
        self.copy_button = QPushButton("Copy diagnostic")
        self.copy_button.setAccessibleName("Copy redacted Doctor diagnostic")
        self.copy_button.clicked.connect(self.copy_diagnostic)
        diagnostics_header.addWidget(self.copy_button)
        layout.addLayout(diagnostics_header)
        self.diagnostics = QTextEdit()
        self.diagnostics.setAccessibleName("Copy-safe redacted Doctor diagnostic")
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setPlaceholderText(
            "Typed, redacted Doctor progress and results will appear here."
        )
        layout.addWidget(self.diagnostics, 1)

        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(100)
        self._elapsed_timer.timeout.connect(self._update_elapsed)

    @property
    def is_running(self) -> bool:
        return self._running

    @Slot()
    def start(self) -> None:
        if self._running:
            return
        self._model = DoctorPresentationModel()
        self._cancellation = CancellationToken()
        self.checks_table.setRowCount(0)
        self.diagnostics.clear()
        self.status_label.setText("Starting Doctor…")
        self.progress_bar.setRange(0, 0)
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.config_path_edit.setEnabled(False)
        self.repository_name_edit.setEnabled(False)
        self.include_github_checkbox.setEnabled(False)
        self._running = True
        self.run_state_changed.emit(True)
        self._elapsed_timer.start()

        repository_name = self.repository_name_edit.text().strip() or None
        request = DoctorRequest(
            config_path=Path(self.config_path_edit.text()).expanduser(),
            repository_name=repository_name,
            include_github=self.include_github_checkbox.isChecked(),
        )
        thread = DoctorThread(self._service, request, self._cancellation, self)
        thread.progress.connect(self._on_progress)
        thread.completed.connect(self._on_completed)
        thread.failed.connect(self._on_unexpected_failure)
        thread.finished.connect(self._on_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        thread.start()

    @Slot()
    def request_cancellation(self) -> None:
        if not self._running or self._cancellation is None:
            return
        self._cancellation.request()
        self.cancel_button.setEnabled(False)
        self.status_label.setText(
            "Cancellation requested — waiting for the next read-only safe point…"
        )

    @Slot(object)
    def _on_progress(self, event: object) -> None:
        if not isinstance(event, OperationEvent):
            return
        self._model.record(event)
        self.status_label.setText(event.message)
        if event.check is not None:
            self._append_check(event.check)
        self.diagnostics.setPlainText(self._model.diagnostic_text())

    @Slot(object)
    def _on_completed(self, result: object) -> None:
        if not isinstance(result, OperationResult):
            self._on_unexpected_failure()
            return
        self._model.finish(result)
        self.checks_table.setRowCount(0)
        for check in result.checks:
            self._append_check(check)
        count = len(result.checks)
        self.progress_bar.setRange(0, max(1, count))
        self.progress_bar.setValue(count)
        elapsed = self._model.elapsed_seconds
        if result.status is OperationStatus.SUCCEEDED:
            self.status_label.setText(
                f"Doctor completed successfully — {count} checks in {elapsed:.2f} s"
            )
        elif result.status is OperationStatus.CANCELLED:
            self.status_label.setText(
                f"Doctor cancelled safely after {count} checks in {elapsed:.2f} s"
            )
        elif result.error is not None:
            self.status_label.setText(
                "Doctor could not complete. Check configuration, connectivity, and "
                "the redacted diagnostic below."
            )
        else:
            self.status_label.setText(
                f"Doctor completed with issues — review remediation for {count} checks."
            )
        self.diagnostics.setPlainText(self._model.diagnostic_text())
        self._elapsed_timer.stop()
        self._update_elapsed()

    @Slot()
    def _on_unexpected_failure(self) -> None:
        self._elapsed_timer.stop()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.status_label.setText(
            "Doctor could not complete because its service failed unexpectedly."
        )
        self.diagnostics.setPlainText(
            "Kosui Forge Doctor diagnostic\n"
            "Status: failed\n\n"
            "Unexpected Doctor service failure. Sensitive exception details were withheld."
        )

    @Slot()
    def _on_thread_finished(self) -> None:
        # Let QThread.finished unwind before releasing the final Python wrapper.
        QTimer.singleShot(0, self._finalize_run)

    @Slot()
    def _finalize_run(self) -> None:
        self._elapsed_timer.stop()
        self._thread = None
        self._cancellation = None
        self._running = False
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.config_path_edit.setEnabled(True)
        self.repository_name_edit.setEnabled(True)
        self.include_github_checkbox.setEnabled(True)
        self.run_state_changed.emit(False)
        self.idle.emit()

    def _append_check(self, check: DoctorCheck) -> None:
        row = self.checks_table.rowCount()
        self.checks_table.insertRow(row)
        values = (
            "PASS" if check.ok else "FAIL",
            check.name,
            check.detail,
            check.guidance or "—",
        )
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column == 0:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.checks_table.setItem(row, column, item)

    @Slot()
    def copy_diagnostic(self) -> None:
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(self.diagnostics.toPlainText())
        self.status_label.setText("Redacted diagnostic copied to the clipboard")

    @Slot()
    def _update_elapsed(self) -> None:
        self.elapsed_label.setText(f"Elapsed: {self._model.elapsed_seconds:.2f} s")
