import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from kosui_forge.application.contracts import (
    DoctorCheck,
    DoctorRequest,
    OperationResult,
    OperationStatus,
)
from repo_bootstrap.cli import build_parser, confirm_plan, main
from repo_bootstrap.config import Config
from repo_bootstrap.errors import PartialFailure, SafetyError
from repo_bootstrap.preflight import CheckResult, PreflightReport


class ParserTests(unittest.TestCase):
    def test_batch_defaults_to_safe_dry_run_false_and_supports_filters(self):
        args = build_parser().parse_args(
            ["batch", "--dry-run", "--owner", "kosuiroom", "--repo", "alpha"]
        )

        self.assertEqual(args.command, "batch")
        self.assertTrue(args.dry_run)
        self.assertEqual(args.owner, "kosuiroom")
        self.assertEqual(args.repos, ["alpha"])
        self.assertFalse(hasattr(args, "public"))

    def test_exposes_configure_doctor_and_mirror_all_commands(self):
        parser = build_parser()

        configure = parser.parse_args(
            [
                "configure",
                "--forgejo-url",
                "https://forgejo.example.test",
                "--forgejo-owner",
                "owner",
                "--github-owner",
                "gh-owner",
                "--projects-root",
                "/srv/projects",
                "--ssh-alias",
                "forgejo-work",
                "--yes",
            ]
        )
        doctor = parser.parse_args(["doctor", "--name", "sample"])
        mirror_all = parser.parse_args(["mirror-all", "--dry-run", "--repo", "sample"])

        self.assertEqual(configure.command, "configure")
        self.assertTrue(configure.yes)
        self.assertEqual(doctor.name, "sample")
        self.assertEqual(mirror_all.repos, ["sample"])


class ConfirmationTests(unittest.TestCase):
    def test_private_plan_requires_confirmation_unless_yes_is_explicit(self):
        args = build_parser().parse_args(
            ["create", "sample", "--description", "A sample"]
        )

        with self.assertRaisesRegex(SafetyError, "not confirmed"):
            confirm_plan(args, ["create private repository"], input_fn=lambda _: "no")

        args = build_parser().parse_args(
            ["create", "sample", "--description", "A sample", "--yes"]
        )
        confirm_plan(args, ["create private repository"], input_fn=lambda _: "unused")

    def test_public_interactive_confirmation_requires_exact_repository_name(self):
        args = build_parser().parse_args(
            ["create", "sample", "--description", "A sample", "--public"]
        )
        responses = iter(["PUBLIC sample", "yes"])

        confirm_plan(
            args, ["create public repository"], input_fn=lambda _: next(responses)
        )

    def test_noninteractive_public_requires_yes_and_additional_acknowledgement(self):
        parser = build_parser()
        missing_ack = parser.parse_args(
            [
                "create",
                "sample",
                "--description",
                "A sample",
                "--public",
                "--yes",
            ]
        )

        with self.assertRaisesRegex(SafetyError, "--ack-public sample"):
            confirm_plan(missing_ack, ["create public repository"])

        accepted = parser.parse_args(
            [
                "create",
                "sample",
                "--description",
                "A sample",
                "--public",
                "--yes",
                "--ack-public",
                "sample",
            ]
        )
        confirm_plan(accepted, ["create public repository"])


class MainExitCodeTests(unittest.TestCase):
    def test_public_yes_without_ack_fails_before_configuration_access(self):
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            code = main(
                [
                    "create",
                    "sample",
                    "--description",
                    "A sample",
                    "--public",
                    "--yes",
                ],
                environ={},
            )

        self.assertEqual(code, 2)
        self.assertIn("--ack-public sample", stderr.getvalue())

    def test_failed_preflight_runs_no_create_operation(self):
        config = Config(
            forgejo_url="https://forgejo.example.test",
            forgejo_owner="owner",
            github_owner="gh-owner",
            projects_root=Path("/srv/projects"),
            ssh_alias="forgejo-work",
        )
        report = PreflightReport(
            (
                CheckResult(
                    "gh-auth", False, "authentication expired", "run gh auth login"
                ),
            )
        )
        stderr = io.StringIO()

        with (
            patch("repo_bootstrap.cli.load_config", return_value=config),
            patch("repo_bootstrap.cli.ForgejoClient"),
            patch("repo_bootstrap.cli.GitHubClient"),
            patch("repo_bootstrap.cli.run_preflight", return_value=report),
            patch("repo_bootstrap.cli.RepositoryManager") as manager_type,
            redirect_stderr(stderr),
        ):
            code = main(
                [
                    "create",
                    "sample",
                    "--description",
                    "A sample",
                    "--yes",
                ],
                environ={"FORGEJO_TOKEN": "secret-value"},
            )

        self.assertEqual(code, 2)
        manager_type.assert_not_called()
        self.assertIn("preflight", stderr.getvalue().lower())

    def test_doctor_is_read_only_and_reports_all_checks(self):
        config = Config(
            forgejo_url="https://forgejo.example.test",
            forgejo_owner="owner",
            github_owner="gh-owner",
            projects_root=Path("/srv/projects"),
            ssh_alias="forgejo-work",
        )
        report = PreflightReport((CheckResult("all", True, "ready", ""),))
        stdout = io.StringIO()

        with (
            patch("repo_bootstrap.cli.load_config", return_value=config),
            patch("repo_bootstrap.cli.ForgejoClient"),
            patch("repo_bootstrap.cli.GitHubClient"),
            patch("repo_bootstrap.cli.run_preflight", return_value=report),
            patch("repo_bootstrap.cli.RepositoryManager") as manager_type,
            redirect_stdout(stdout),
        ):
            code = main(
                ["doctor", "--name", "sample", "--description", "A sample"],
                environ={"FORGEJO_TOKEN": "secret-value"},
            )

        self.assertEqual(code, 0)
        manager_type.assert_not_called()
        self.assertIn("PASS", stdout.getvalue())

    def test_doctor_cli_renders_the_application_service_without_output_drift(self):
        config = Config(
            forgejo_url="https://forgejo.example.test",
            forgejo_owner="owner",
            github_owner="gh-owner",
            projects_root=Path("/srv/projects"),
            ssh_alias="forgejo-work",
        )
        result = OperationResult(
            operation_id="doctor-1",
            status=OperationStatus.SUCCEEDED,
            checks=(DoctorCheck("all", True, "ready"),),
        )
        stdout = io.StringIO()

        with (
            patch("repo_bootstrap.cli.load_config", return_value=config),
            patch("repo_bootstrap.cli.ForgejoClient"),
            patch("repo_bootstrap.cli.GitHubClient"),
            patch("repo_bootstrap.cli.build_doctor_service") as service_factory,
            redirect_stdout(stdout),
        ):
            service_factory.return_value.run.return_value = result
            code = main(
                ["doctor", "--name", "sample", "--description", "A sample"],
                environ={"FORGEJO_TOKEN": "secret-value"},
            )

        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue(), "[PASS] all: ready\n")
        request = service_factory.return_value.run.call_args.args[0]
        self.assertIsInstance(request, DoctorRequest)
        self.assertEqual(request.repository_name, "sample")

    def test_doctor_preflight_exception_preserves_compatibility_diagnostic(self):
        config = Config(
            forgejo_url="https://forgejo.example.test",
            forgejo_owner="owner",
            github_owner="gh-owner",
            projects_root=Path("/srv/projects"),
            ssh_alias="forgejo-work",
        )

        for exception in (
            OSError("preflight failed for secret-value"),
            ValueError("preflight failed for secret-value"),
        ):
            with self.subTest(exception=type(exception).__name__):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    patch("repo_bootstrap.cli.load_config", return_value=config),
                    patch("repo_bootstrap.cli.ForgejoClient"),
                    patch("repo_bootstrap.cli.GitHubClient"),
                    patch("repo_bootstrap.cli.run_preflight", side_effect=exception),
                    redirect_stdout(stdout),
                    redirect_stderr(stderr),
                ):
                    code = main(
                        ["doctor"],
                        environ={"FORGEJO_TOKEN": "secret-value"},
                    )

                self.assertEqual(code, 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(
                    stderr.getvalue(),
                    "error: preflight failed for <redacted>\n",
                )

    def test_doctor_reports_missing_token_through_read_only_preflight(self):
        config = Config(
            forgejo_url="https://forgejo.example.test",
            forgejo_owner="owner",
            github_owner="gh-owner",
            projects_root=Path("/srv/projects"),
            ssh_alias="forgejo-work",
        )
        report = PreflightReport(
            (
                CheckResult(
                    "forgejo-credential",
                    False,
                    "credential source FORGEJO_TOKEN is missing",
                    "load FORGEJO_TOKEN",
                ),
            )
        )
        stdout = io.StringIO()

        with (
            patch("repo_bootstrap.cli.load_config", return_value=config),
            patch("repo_bootstrap.cli.ForgejoClient") as forgejo_type,
            patch("repo_bootstrap.cli.GitHubClient"),
            patch("repo_bootstrap.cli.run_preflight", return_value=report) as preflight,
            redirect_stdout(stdout),
        ):
            code = main(["doctor"], environ={})

        self.assertEqual(code, 2)
        self.assertIn("forgejo-credential", stdout.getvalue())
        self.assertFalse(preflight.call_args.kwargs["token_present"])
        forgejo_type.assert_called_once()

    def test_missing_forgejo_token_fails_preflight_without_create_operation(self):
        config = Config(
            forgejo_url="https://forgejo.example.test",
            forgejo_owner="owner",
            github_owner="gh-owner",
            projects_root=Path("/srv/projects"),
            ssh_alias="forgejo-work",
        )
        report = PreflightReport(
            (
                CheckResult(
                    "forgejo-credential",
                    False,
                    "credential source FORGEJO_TOKEN is missing",
                    "load FORGEJO_TOKEN",
                ),
            )
        )
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            patch("repo_bootstrap.cli.load_config", return_value=config),
            patch("repo_bootstrap.cli.ForgejoClient"),
            patch("repo_bootstrap.cli.GitHubClient"),
            patch("repo_bootstrap.cli.run_preflight", return_value=report) as preflight,
            patch("repo_bootstrap.cli.RepositoryManager") as manager_type,
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = main(
                [
                    "create",
                    "sample",
                    "--description",
                    "A sample",
                    "--dry-run",
                ],
                environ={},
            )

        self.assertEqual(code, 2)
        self.assertIn("FORGEJO_TOKEN", stdout.getvalue())
        self.assertFalse(preflight.call_args.kwargs["token_present"])
        manager_type.assert_not_called()
        self.assertNotIn("secret-value", stdout.getvalue() + stderr.getvalue())

    def test_post_confirmation_failure_is_recorded_in_resume_journal(self):
        with TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text(
                """
forgejo_url = "https://forgejo.example.test"
forgejo_owner = "owner"
github_owner = "gh-owner"
projects_root = "/srv/projects"
ssh_alias = "forgejo-work"
""".strip(),
                encoding="utf-8",
            )
            passed = PreflightReport((CheckResult("all", True, "ready", ""),))
            stderr = io.StringIO()
            with (
                patch("repo_bootstrap.cli.run_preflight", return_value=passed),
                patch("repo_bootstrap.cli.StageJournal") as journal_type,
                patch("repo_bootstrap.cli.RepositoryManager") as manager_type,
                redirect_stderr(stderr),
            ):
                manager_type.return_value.create.side_effect = SafetyError(
                    "post-confirmation collision"
                )
                code = main(
                    [
                        "--config",
                        str(config),
                        "create",
                        "sample",
                        "--description",
                        "A sample",
                        "--yes",
                    ],
                    environ={"FORGEJO_TOKEN": "secret-value"},
                )

        self.assertEqual(code, 2)
        journal_type.return_value.record.assert_called()
        self.assertIn("resume journal", stderr.getvalue())

    def test_partial_failure_has_distinct_exit_code(self):
        with TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text(
                """
forgejo_url = "https://forgejo.example.test"
forgejo_owner = "owner"
github_owner = "gh-owner"
projects_root = "/srv/projects"
ssh_alias = "forgejo-work"
""".strip(),
                encoding="utf-8",
            )
            stderr = io.StringIO()
            passed = PreflightReport((CheckResult("all", True, "ready", ""),))
            with (
                patch("repo_bootstrap.cli.run_preflight", return_value=passed),
                patch("repo_bootstrap.cli.StageJournal"),
                patch("repo_bootstrap.cli.RepositoryManager") as manager_type,
            ):
                manager_type.return_value.create.side_effect = PartialFailure(
                    "created Forgejo repository; rerun after recovery"
                )
                with redirect_stderr(stderr):
                    code = main(
                        [
                            "--config",
                            str(config),
                            "create",
                            "sample",
                            "--description",
                            "A sample",
                            "--yes",
                        ],
                        environ={"FORGEJO_TOKEN": "secret-value"},
                    )

        self.assertEqual(code, 3)
        self.assertIn("partial failure", stderr.getvalue().lower())
        self.assertNotIn("secret-value", stderr.getvalue())

    def test_batch_blockers_return_nonzero_after_reporting_summary(self):
        with TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text(
                """
forgejo_url = "https://forgejo.example.test"
forgejo_owner = "owner"
github_owner = "gh-owner"
projects_root = "/srv/projects"
ssh_alias = "forgejo-work"
""".strip(),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            passed = PreflightReport((CheckResult("all", True, "ready", ""),))
            with (
                patch("repo_bootstrap.cli.run_preflight", return_value=passed),
                patch("repo_bootstrap.cli.RepositoryManager") as manager_type,
            ):
                result = manager_type.return_value.batch.return_value
                result.examined = 2
                result.ready = 0
                result.would_change = 1
                result.changed = 0
                result.skipped = 0
                result.blockers = ("owner/locked: permission denied",)
                with redirect_stdout(stdout):
                    code = main(
                        ["--config", str(config), "batch", "--dry-run"],
                        environ={"FORGEJO_TOKEN": "secret-value"},
                    )

        self.assertEqual(code, 4)
        self.assertIn("blockers=1", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
