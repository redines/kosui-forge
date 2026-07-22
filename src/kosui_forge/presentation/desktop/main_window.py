"""Kosui Forge native desktop application shell."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .doctor_page import DoctorPage
from .worker import DoctorRunner


class MainWindow(QMainWindow):
    """Native, responsive shell whose real first workflow is Doctor."""

    close_requested = Signal()

    def __init__(self, doctor_service: DoctorRunner, *, config_path: Path) -> None:
        super().__init__()
        self._close_pending = False
        self.setWindowTitle("Kosui Forge")
        self.setAccessibleName("Kosui Forge main window")
        self.resize(1120, 720)
        self.setMinimumSize(760, 520)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_sidebar())

        self.pages = QStackedWidget()
        self.pages.setAccessibleName("Workspace pages")
        self.overview_page = self._placeholder_page(
            "Welcome to Kosui Forge",
            "Manage local, Forgejo, and GitHub repositories from one safe desktop workspace.",
        )
        self.repositories_page = self._placeholder_page(
            "Repositories",
            "Your repository catalog will appear here in a future reviewed slice.",
        )
        self.doctor_page = DoctorPage(doctor_service, config_path=config_path)
        self.pages.addWidget(self.overview_page)
        self.pages.addWidget(self.repositories_page)
        self.pages.addWidget(self.doctor_page)
        splitter.addWidget(self.pages)
        splitter.setSizes([260, 860])
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)

        status_bar = QStatusBar()
        status_bar.setAccessibleName("Application status")
        status_bar.showMessage("Ready — local workflows remain available while offline")
        self.setStatusBar(status_bar)

        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        self.doctor_page.idle.connect(self._finish_pending_close)
        self.doctor_page.run_state_changed.connect(self._show_run_state)

        self._doctor_shortcut = QShortcut(QKeySequence("Ctrl+D"), self)
        self._doctor_shortcut.activated.connect(self.show_doctor)
        QWidget.setTabOrder(self.navigation, self.doctor_page.config_path_edit)
        QWidget.setTabOrder(
            self.doctor_page.config_path_edit, self.doctor_page.repository_name_edit
        )
        QWidget.setTabOrder(
            self.doctor_page.repository_name_edit, self.doctor_page.run_button
        )

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setMinimumWidth(210)
        sidebar.setMaximumWidth(360)
        sidebar.setAccessibleName("Repository and navigation sidebar")
        layout = QVBoxLayout(sidebar)

        product = QLabel("Kosui Forge")
        product.setAccessibleName("Kosui Forge")
        font = product.font()
        font.setPointSize(font.pointSize() + 4)
        font.setBold(True)
        product.setFont(font)
        layout.addWidget(product)

        self.navigation = QListWidget()
        self.navigation.setAccessibleName("Primary navigation")
        self.navigation.setToolTip("Switch pages (Ctrl+D opens Doctor)")
        for label in ("Overview", "Repositories", "Doctor"):
            QListWidgetItem(label, self.navigation)
        self.navigation.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.navigation.setFixedHeight(
            self.navigation.sizeHintForRow(0) * self.navigation.count() + 8
        )
        layout.addWidget(self.navigation)

        repository_heading = QLabel("Repository catalog")
        repository_heading.setAccessibleName("Repository catalog")
        heading_font = repository_heading.font()
        heading_font.setBold(True)
        repository_heading.setFont(heading_font)
        layout.addWidget(repository_heading)

        empty_catalog = QLabel(
            "No repositories loaded yet.\nCatalog support is coming next."
        )
        empty_catalog.setWordWrap(True)
        empty_catalog.setAccessibleName("Repository catalog is empty")
        layout.addWidget(empty_catalog)
        layout.addStretch(1)

        account = QLabel("Accounts\nNot connected • Offline-ready")
        account.setWordWrap(True)
        account.setAccessibleName("Account status: not connected, offline-ready")
        layout.addWidget(account)
        return sidebar

    @staticmethod
    def _placeholder_page(title: str, detail: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 32, 32, 32)
        heading = QLabel(title)
        font = heading.font()
        font.setPointSize(font.pointSize() + 6)
        font.setBold(True)
        heading.setFont(font)
        detail_label = QLabel(detail)
        detail_label.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(detail_label)
        layout.addStretch(1)
        return page

    @Slot()
    def show_doctor(self) -> None:
        self.navigation.setCurrentRow(2)
        self.doctor_page.run_button.setFocus(Qt.FocusReason.ShortcutFocusReason)

    @Slot(bool)
    def _show_run_state(self, running: bool) -> None:
        if running:
            self.statusBar().showMessage("Doctor is running a read-only diagnostic")
        else:
            self.statusBar().showMessage(
                "Ready — local workflows remain available while offline"
            )

    @Slot()
    def _finish_pending_close(self) -> None:
        if self._close_pending:
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.doctor_page.is_running:
            self._close_pending = True
            self.doctor_page.request_cancellation()
            self.statusBar().showMessage(
                "Closing after Doctor reaches a read-only cancellation point…"
            )
            event.ignore()
            return
        event.accept()
