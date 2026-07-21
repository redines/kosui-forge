import unittest
from pathlib import Path

from kosui_forge.adapters.doctor import RepoBootstrapDoctorAdapter
from kosui_forge.application.contracts import DoctorRequest, OperationStatus
from kosui_forge.application.doctor import DoctorService
from repo_bootstrap.config import Config
from repo_bootstrap.preflight import CheckResult, PreflightReport


class DoctorAdapterTests(unittest.TestCase):
    def test_adapter_composes_real_preflight_without_copying_policy(self):
        config = Config(
            forgejo_url="https://forgejo.example.test",
            forgejo_owner="forgejo-owner",
            github_owner="github-owner",
            projects_root=Path("/srv/test-redaction-sentinel-value"),
            ssh_alias="forgejo-work",
        )
        calls = []

        def preflight(config_value, forgejo, github, **kwargs):
            calls.append((config_value, forgejo, github, kwargs))
            check = CheckResult(
                "forgejo-auth",
                True,
                "authenticated with test-redaction-sentinel-value",
            )
            kwargs["reporter"](check)
            return PreflightReport((check,))

        adapter = RepoBootstrapDoctorAdapter(
            environment={"FORGEJO_TOKEN": "test-redaction-sentinel-value"},
            config_loader=lambda _path: config,
            forgejo_factory=lambda _url, _token: "forgejo-client",
            github_factory=lambda *, host: "github-client",
            preflight_runner=preflight,
        )
        events = []

        result = DoctorService(adapter).run(
            DoctorRequest(config_path=Path("/tmp/config.toml")),
            progress=events.append,
        )

        self.assertEqual(result.status, OperationStatus.SUCCEEDED)
        self.assertEqual(calls[0][1:3], ("forgejo-client", "github-client"))
        self.assertTrue(calls[0][3]["private"])
        self.assertFalse(calls[0][3]["cancellation_requested"]())
        self.assertNotIn("test-redaction-sentinel-value", repr((events, result)))
        self.assertIn("<redacted>", repr((events, result)))


if __name__ == "__main__":
    unittest.main()
