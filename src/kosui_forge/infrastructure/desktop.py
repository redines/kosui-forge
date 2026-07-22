"""Desktop composition root and installed GUI entry point."""

from __future__ import annotations

from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import sys

from PySide6.QtWidgets import QApplication

from repo_bootstrap.config import default_config_path

from kosui_forge.presentation.desktop.main_window import MainWindow
from kosui_forge.presentation.desktop.worker import DoctorRunner

from .cli import build_doctor_service


def build_main_window(
    *,
    doctor_service: DoctorRunner | None = None,
    config_path: Path | None = None,
) -> MainWindow:
    """Construct the real desktop graph without embedding workflow policy."""
    service = doctor_service if doctor_service is not None else build_doctor_service()
    path = config_path if config_path is not None else default_config_path()
    return MainWindow(service, config_path=path)


def create_application(arguments: Sequence[str] | None = None) -> QApplication:
    """Create or return the process QApplication with native Qt 6 defaults."""
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    application = QApplication(list(arguments) if arguments is not None else sys.argv)
    application.setApplicationName("Kosui Forge")
    application.setApplicationDisplayName("Kosui Forge")
    try:
        application.setApplicationVersion(version("kosui-forge"))
    except PackageNotFoundError:
        pass
    application.setOrganizationName("Kosui Forge")
    application.setQuitOnLastWindowClosed(True)
    # Qt 6 enables high-DPI scaling and follows the system palette by default.
    return application


def main() -> int:
    """Launch the Kosui Forge native desktop application."""
    application = create_application()
    window = build_main_window()
    window.show()
    return application.exec()
