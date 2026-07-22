import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from threading import Event
import time
import tomllib
import unittest

from PySide6.QtCore import Qt, QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from kosui_forge.application.contracts import (
    CancellationState,
    DoctorCheck,
    EventState,
    OperationEvent,
    OperationResult,
    OperationStatus,
)
from kosui_forge.application.doctor import DoctorService
from kosui_forge.ports.doctor import RawDoctorCheck, RawDoctorReport
from kosui_forge.presentation.desktop.main_window import MainWindow


class SuccessfulDoctorService:
    def __init__(self) -> None:
        self.worker_thread = None

    def run(self, request, *, progress=None, cancellation=None):
        self.worker_thread = QThread.currentThread()
        check = DoctorCheck("configuration", True, "Configuration is ready")
        if progress is not None:
            progress(
                OperationEvent(
                    request.operation_id,
                    0,
                    "doctor",
                    EventState.STARTED,
                    "Doctor started",
                    0,
                )
            )
            progress(
                OperationEvent(
                    request.operation_id,
                    1,
                    check.name,
                    EventState.CHECK_PASSED,
                    "configuration: Configuration is ready",
                    1,
                    check=check,
                )
            )
        return OperationResult(
            request.operation_id,
            OperationStatus.SUCCEEDED,
            checks=(check,),
        )


class RedactingFailurePort:
    secret = "test-desktop-secret-value"

    def redact(self, value):
        return str(value).replace(self.secret, "<redacted>")

    def run(self, *, started, reporter, **_kwargs):
        checks = (
            RawDoctorCheck("configuration", True, "Configuration is ready"),
            RawDoctorCheck(
                "forgejo-auth",
                False,
                f"Authentication failed for {self.secret}",
                f"Refresh the credential replacing {self.secret}",
            ),
        )
        started()
        for check in checks:
            reporter(check)
        return RawDoctorReport(checks, ok=False)


class CooperativeDoctorService:
    def __init__(self, *, wait_for_release=False):
        self.started = Event()
        self.release = Event()
        self.wait_for_release = wait_for_release
        self.cancellation_seen = False

    def run(self, request, *, progress=None, cancellation=None):
        if progress is not None:
            progress(
                OperationEvent(
                    request.operation_id,
                    0,
                    "doctor",
                    EventState.STARTED,
                    "Doctor started",
                    0,
                )
            )
        self.started.set()
        while cancellation is not None and not cancellation.requested:
            time.sleep(0.005)
        self.cancellation_seen = True
        if self.wait_for_release:
            self.release.wait(timeout=2)
        if cancellation is not None:
            cancellation.mark_honored()
        return OperationResult(
            request.operation_id,
            OperationStatus.CANCELLED,
            cancellation_state=CancellationState.HONORED,
        )


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def wait_until(self, predicate, timeout_ms=2000):
        deadline = time.monotonic() + timeout_ms / 1000
        while not predicate() and time.monotonic() < deadline:
            self.application.processEvents()
            QTest.qWait(10)
        self.assertTrue(predicate())

    def test_launch_navigation_and_doctor_success_run_off_the_ui_thread(self):
        service = SuccessfulDoctorService()
        window = MainWindow(service, config_path=Path("/tmp/kosui-forge.toml"))
        self.addCleanup(window.close)
        window.show()

        self.assertEqual(window.windowTitle(), "Kosui Forge")
        self.assertEqual(window.navigation.accessibleName(), "Primary navigation")
        self.assertTrue(window.overview_page.isVisible())

        window.show_doctor()
        self.assertTrue(window.doctor_page.isVisible())
        QTest.mouseClick(window.doctor_page.run_button, Qt.MouseButton.LeftButton)
        self.wait_until(lambda: not window.doctor_page.is_running)

        self.assertIsNot(service.worker_thread, self.application.thread())
        self.assertEqual(window.doctor_page.checks_table.rowCount(), 1)
        self.assertIn("completed", window.doctor_page.status_label.text().lower())
        self.assertIn(
            "Configuration is ready", window.doctor_page.diagnostics.toPlainText()
        )

    def test_failed_checks_render_ordered_copy_safe_remediation(self):
        window = MainWindow(
            DoctorService(RedactingFailurePort()),
            config_path=Path("/tmp/kosui-forge.toml"),
        )
        self.addCleanup(window.close)
        window.show()
        window.show_doctor()

        QTest.mouseClick(window.doctor_page.run_button, Qt.MouseButton.LeftButton)
        self.wait_until(lambda: not window.doctor_page.is_running)

        diagnostic = window.doctor_page.diagnostics.toPlainText()
        self.assertNotIn(RedactingFailurePort.secret, diagnostic)
        self.assertIn("<redacted>", diagnostic)
        self.assertIn("Remediation: Refresh the credential", diagnostic)
        self.assertLess(
            diagnostic.index("000 started"),
            diagnostic.index("001 check-passed"),
        )
        self.assertLess(
            diagnostic.index("001 check-passed"),
            diagnostic.index("002 check-failed"),
        )
        self.assertIn("issues", window.doctor_page.status_label.text().lower())

        QTest.mouseClick(window.doctor_page.copy_button, Qt.MouseButton.LeftButton)
        copied = self.application.clipboard().text()
        self.assertEqual(copied, diagnostic)
        self.assertNotIn(RedactingFailurePort.secret, copied)

    def test_cancel_requests_cooperative_service_safe_point(self):
        service = CooperativeDoctorService()
        window = MainWindow(service, config_path=Path("/tmp/kosui-forge.toml"))
        self.addCleanup(window.close)
        window.show()
        window.show_doctor()
        QTest.mouseClick(window.doctor_page.run_button, Qt.MouseButton.LeftButton)
        self.wait_until(service.started.is_set)

        QTest.mouseClick(window.doctor_page.cancel_button, Qt.MouseButton.LeftButton)
        self.wait_until(lambda: not window.doctor_page.is_running)

        self.assertTrue(service.cancellation_seen)
        self.assertIn(
            "cancelled safely", window.doctor_page.status_label.text().lower()
        )
        self.assertIn("Status: cancelled", window.doctor_page.diagnostics.toPlainText())

    def test_keyboard_shortcut_focuses_doctor_and_close_waits_for_worker(self):
        service = CooperativeDoctorService(wait_for_release=True)
        window = MainWindow(service, config_path=Path("/tmp/kosui-forge.toml"))
        self.addCleanup(window.close)
        window.show()
        window.activateWindow()
        window.navigation.setFocus()
        QTest.qWait(10)

        QTest.keyClick(
            window.navigation,
            Qt.Key.Key_D,
            Qt.KeyboardModifier.ControlModifier,
        )
        self.application.processEvents()
        self.assertEqual(window.navigation.currentRow(), 2)
        self.assertTrue(window.doctor_page.run_button.hasFocus())

        QTest.mouseClick(window.doctor_page.run_button, Qt.MouseButton.LeftButton)
        self.wait_until(service.started.is_set)
        window.close()
        self.wait_until(lambda: service.cancellation_seen)
        self.assertTrue(window.isVisible())

        service.release.set()
        self.wait_until(lambda: not window.isVisible())
        self.assertFalse(window.doctor_page.is_running)

    def test_desktop_entry_point_and_constrained_qt_dependency_are_packaged(self):
        with Path("pyproject.toml").open("rb") as stream:
            metadata = tomllib.load(stream)

        self.assertEqual(
            metadata["project"]["gui-scripts"]["kosui-forge"],
            "kosui_forge.infrastructure.desktop:main",
        )
        pyside_dependencies = [
            dependency
            for dependency in metadata["project"]["dependencies"]
            if dependency.startswith("PySide6")
        ]
        self.assertEqual(pyside_dependencies, ["PySide6>=6.8,<6.12"])

    def test_desktop_composition_root_builds_the_real_doctor_shell(self):
        from kosui_forge.infrastructure.desktop import build_main_window

        window = build_main_window(
            doctor_service=SuccessfulDoctorService(),
            config_path=Path("/tmp/kosui-forge.toml"),
        )
        self.addCleanup(window.close)

        self.assertIsInstance(window, MainWindow)
        self.assertEqual(
            window.doctor_page.config_path_edit.text(), "/tmp/kosui-forge.toml"
        )


if __name__ == "__main__":
    unittest.main()
