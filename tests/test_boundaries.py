import unittest
from pathlib import Path

from kosui_forge.application.contracts import (
    DoctorCheck,
    DoctorRequest,
    OperationResult,
    OperationStatus,
)
from kosui_forge.application.doctor import DoctorService
from kosui_forge.domain.repository import is_ssh_public_key, validate_repo_name
from kosui_forge.infrastructure.cli import build_doctor_service
from kosui_forge.infrastructure.doctor import (
    build_doctor_service as compatibility_build_doctor_service,
)
from kosui_forge.presentation.cli.doctor import render_doctor_result
from repo_bootstrap.config import Config
from repo_bootstrap.preflight import CheckResult, PreflightReport
from repo_bootstrap.validation import (
    is_ssh_public_key as compatibility_is_ssh_public_key,
)
from repo_bootstrap.validation import (
    validate_repo_name as compatibility_validate_repo_name,
)


class DomainBoundaryTests(unittest.TestCase):
    def test_repository_validation_is_provider_neutral_and_compatibility_reexported(
        self,
    ):
        self.assertEqual(validate_repo_name("safe.name"), "safe.name")
        self.assertTrue(is_ssh_public_key("ssh-ed25519 public-key-data comment"))
        self.assertIs(compatibility_validate_repo_name, validate_repo_name)
        self.assertIs(compatibility_is_ssh_public_key, is_ssh_public_key)

        with self.assertRaises(ValueError):
            validate_repo_name("unsafe/name")


class CliPresentationBoundaryTests(unittest.TestCase):
    def test_doctor_renderer_formats_only_typed_application_results(self):
        result = OperationResult(
            operation_id="doctor-1",
            status=OperationStatus.FAILED,
            checks=(
                DoctorCheck("runtime", True, "ready"),
                DoctorCheck("forgejo-auth", False, "expired", "log in again"),
            ),
        )

        self.assertEqual(
            render_doctor_result(result),
            "[PASS] runtime: ready\n[FAIL] forgejo-auth: expired; log in again",
        )


class CliCompositionRootTests(unittest.TestCase):
    def test_foundation_doctor_root_remains_a_compatibility_import(self):
        self.assertIs(compatibility_build_doctor_service, build_doctor_service)

    def test_headless_root_wires_the_doctor_port_without_policy(self):
        config = Config(
            forgejo_url="https://forgejo.example.test",
            forgejo_owner="owner",
            github_owner="github-owner",
            projects_root=Path("/srv/projects"),
            ssh_alias="forgejo-work",
        )
        calls = []

        def preflight(config_value, forgejo, github, **kwargs):
            calls.append((config_value, forgejo, github, kwargs))
            return PreflightReport((CheckResult("all", True, "ready"),))

        service = build_doctor_service(
            environment={},
            config_loader=lambda _path: config,
            forgejo_factory=lambda url, token: (url, token),
            github_factory=lambda **kwargs: kwargs,
            preflight_runner=preflight,
        )

        self.assertIsInstance(service, DoctorService)
        result = service.run(DoctorRequest(config_path=Path("config.toml")))
        self.assertEqual(result.status, OperationStatus.SUCCEEDED)
        self.assertEqual(calls[0][0], config)
        self.assertEqual(
            calls[0][1],
            (config.forgejo_url, "kosui-forge-preflight-missing-credential"),
        )
        self.assertEqual(calls[0][2], {"host": config.github_host})


if __name__ == "__main__":
    unittest.main()
