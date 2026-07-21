import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
import sys

from kosui_forge.application.contracts import (
    CancellationState,
    CancellationToken,
    DoctorCheck,
    DoctorRequest,
    EventState,
    OperationResult,
    OperationStatus,
    ResourceLink,
)
from kosui_forge.application.doctor import DoctorService
from kosui_forge.ports.doctor import (
    RawDoctorCheck,
    RawDoctorReport,
    RawResourceLink,
)


class FakeDoctorPort:
    def __init__(self, checks, *, sentinel="test-redaction-sentinel-value"):
        self.checks = tuple(checks)
        self.sentinel = sentinel

    def redact(self, value):
        return str(value).replace(self.sentinel, "<redacted>")

    def run(
        self,
        *,
        config_path,
        repository_name,
        description,
        include_github,
        started,
        reporter,
        cancellation_requested,
    ):
        started()
        emitted = []
        for check in self.checks:
            emitted.append(check)
            reporter(check)
            if cancellation_requested():
                return RawDoctorReport(tuple(emitted), ok=False, cancelled=True)
        return RawDoctorReport(
            tuple(emitted),
            ok=bool(emitted) and all(check.ok for check in emitted),
            links=(
                RawResourceLink("Forgejo", "https://forgejo.example.test"),
                RawResourceLink("Projects root", f"/srv/{self.sentinel}"),
            ),
        )


class ApplicationContractTests(unittest.TestCase):
    def test_cancellation_token_has_monotonic_thread_safe_states(self):
        token = CancellationToken()

        self.assertEqual(token.state, CancellationState.NOT_REQUESTED)
        self.assertEqual(token.request(), CancellationState.REQUESTED)
        self.assertEqual(token.request(), CancellationState.REQUESTED)
        self.assertEqual(token.mark_honored(), CancellationState.HONORED)
        self.assertEqual(token.request(), CancellationState.HONORED)

    def test_result_contract_is_typed_and_immutable(self):
        result = OperationResult(
            operation_id="doctor-1",
            status=OperationStatus.SUCCEEDED,
            checks=(DoctorCheck("runtime", True, "ready"),),
            links=(ResourceLink("Forgejo", "https://forgejo.example.test"),),
        )

        self.assertIsInstance(result.checks[0], DoctorCheck)
        self.assertIsInstance(result.links[0], ResourceLink)
        with self.assertRaises(FrozenInstanceError):
            result.status = OperationStatus.FAILED  # type: ignore[misc]


class DoctorServiceTests(unittest.TestCase):
    def test_run_returns_redacted_structured_result_and_ordered_progress(self):
        checks = (
            RawDoctorCheck("runtime", True, "ready"),
            RawDoctorCheck(
                "forgejo-auth",
                False,
                "request exposed test-redaction-sentinel-value",
                "replace authorization value test-redaction-sentinel-value",
            ),
        )
        events = []
        request = DoctorRequest(
            config_path=Path("/tmp/config.toml"),
            repository_name="sample",
            description="A sample",
        )
        result = DoctorService(FakeDoctorPort(checks)).run(
            request, progress=events.append
        )

        self.assertEqual(result.status, OperationStatus.FAILED)
        self.assertEqual([event.sequence for event in events], list(range(4)))
        self.assertEqual(
            [event.state for event in events],
            [
                EventState.STARTED,
                EventState.CHECK_PASSED,
                EventState.CHECK_FAILED,
                EventState.FAILED,
            ],
        )
        self.assertEqual(
            [check.name for check in result.checks], ["runtime", "forgejo-auth"]
        )
        self.assertTrue(all(isinstance(check, DoctorCheck) for check in result.checks))
        self.assertTrue(all(isinstance(link, ResourceLink) for link in result.links))
        self.assertIsNotNone(result.recovery)
        boundary_text = repr((events, result))
        self.assertNotIn("test-redaction-sentinel-value", boundary_text)
        self.assertIn("<redacted>", boundary_text)

    def test_application_layer_has_no_concrete_compatibility_imports(self):
        module = sys.modules[DoctorService.__module__]

        concrete_modules = {
            value.__name__
            for value in module.__dict__.values()
            if hasattr(value, "__name__")
            and isinstance(value.__name__, str)
            and value.__name__.startswith("repo_bootstrap")
        }

        self.assertEqual(concrete_modules, set())

    def test_cancellation_is_honored_after_a_read_only_check(self):
        token = CancellationToken()
        check = RawDoctorCheck("runtime", True, "ready")
        events = []

        def collect(event):
            events.append(event)
            if event.state is EventState.CHECK_PASSED:
                token.request()

        result = DoctorService(FakeDoctorPort((check,))).run(
            DoctorRequest(config_path=Path("/tmp/config.toml")),
            progress=collect,
            cancellation=token,
        )

        self.assertEqual(result.status, OperationStatus.CANCELLED)
        self.assertEqual(result.cancellation_state, CancellationState.HONORED)
        self.assertEqual(events[-1].state, EventState.CANCELLED)
        self.assertEqual(result.checks, (DoctorCheck("runtime", True, "ready", ""),))
        self.assertIn("read-only", result.recovery.summary)


if __name__ == "__main__":
    unittest.main()
