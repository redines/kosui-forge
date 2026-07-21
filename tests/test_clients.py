import json
import unittest

from repo_bootstrap.errors import ApiError
from repo_bootstrap.github import CommandResult, GitHubClient
from repo_bootstrap.redaction import redact


class RecordingRunner:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, args, stdin):
        self.calls.append((list(args), stdin))
        return self.results.pop(0)


class GitHubClientTests(unittest.TestCase):
    def test_allow_404_only_accepts_explicit_http_status(self):
        missing = GitHubClient(
            RecordingRunner([CommandResult(1, "", "gh: Not Found (HTTP 404)")])
        )
        ambiguous = GitHubClient(
            RecordingRunner(
                [
                    CommandResult(
                        1, "", "Not Found while authorization status is unknown"
                    )
                ]
            )
        )

        self.assertIsNone(missing.get_repo("gh-owner", "sample"))
        with self.assertRaises(ApiError):
            ambiguous.get_repo("gh-owner", "sample")

    def test_create_repository_uses_private_api_payload(self):
        runner = RecordingRunner(
            [
                CommandResult(0, '{"login":"gh-owner"}', ""),
                CommandResult(0, '{"name":"sample","private":true}', ""),
            ]
        )
        client = GitHubClient(runner)

        result = client.create_repo("gh-owner", "sample", "A sample", private=True)

        self.assertTrue(result["private"])
        args, stdin = runner.calls[1]
        self.assertEqual(
            args,
            [
                "gh",
                "api",
                "--hostname",
                "github.com",
                "--method",
                "POST",
                "/user/repos",
                "--input",
                "-",
            ],
        )
        self.assertEqual(
            json.loads(stdin),
            {"name": "sample", "description": "A sample", "private": True},
        )

    def test_create_organization_repository_uses_org_endpoint(self):
        runner = RecordingRunner(
            [
                CommandResult(0, '{"login":"someone-else"}', ""),
                CommandResult(0, '{"name":"sample","private":true}', ""),
            ]
        )
        client = GitHubClient(runner)

        client.create_repo("gh-owner", "sample", "A sample", private=True)

        self.assertIn("/orgs/gh-owner/repos", runner.calls[1][0])

    def test_registers_writable_deploy_key(self):
        runner = RecordingRunner([CommandResult(0, '{"id":123}', "")])
        client = GitHubClient(runner)

        client.add_deploy_key(
            "gh-owner", "sample", "Forgejo mirror: owner/sample", "ssh-ed25519 AAAAtest"
        )

        args, stdin = runner.calls[0]
        self.assertIn("/repos/gh-owner/sample/keys", args)
        self.assertEqual(
            json.loads(stdin),
            {
                "title": "Forgejo mirror: owner/sample",
                "key": "ssh-ed25519 AAAAtest",
                "read_only": False,
            },
        )

    def test_disables_actions_for_mirror_repository(self):
        runner = RecordingRunner([CommandResult(0, "", "")])
        client = GitHubClient(runner)

        client.disable_actions("gh-owner", "sample")

        args, stdin = runner.calls[0]
        self.assertEqual(
            args,
            [
                "gh",
                "api",
                "--hostname",
                "github.com",
                "--method",
                "PUT",
                "/repos/gh-owner/sample/actions/permissions",
                "--input",
                "-",
            ],
        )
        self.assertEqual(json.loads(stdin), {"enabled": False})

    def test_reads_actions_permissions_for_verification(self):
        runner = RecordingRunner([CommandResult(0, '{"enabled":false}', "")])
        client = GitHubClient(runner)

        result = client.get_actions_permissions("gh-owner", "sample")

        self.assertIs(result["enabled"], False)
        self.assertIn("/repos/gh-owner/sample/actions/permissions", runner.calls[0][0])
        self.assertIn("GET", runner.calls[0][0])


class RedactionTests(unittest.TestCase):
    def test_redacts_known_secrets_bearer_tokens_and_url_credentials(self):
        value = (
            "token abc123456789; Authorization: Bearer zyx987654321; "
            "https://person:password@example.test/path"
        )

        result = redact(value, secrets=("abc123456789",))

        self.assertNotIn("abc123456789", result)
        self.assertNotIn("zyx987654321", result)
        self.assertNotIn("person:password", result)
        self.assertGreaterEqual(result.count("<redacted>"), 3)


if __name__ == "__main__":
    unittest.main()
