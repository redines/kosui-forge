import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call

from repo_bootstrap.forgejo import ForgejoClient, HttpResponse, _urllib_transport
from repo_bootstrap.errors import ApiError
from repo_bootstrap.github import CommandResult, GitHubClient
from repo_bootstrap.redaction import redact


class RecordingTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        return self.responses.pop(0)


class ForgejoClientTests(unittest.TestCase):
    def test_create_repository_sends_private_payload_to_user_endpoint(self):
        transport = RecordingTransport(
            [
                HttpResponse(200, b'{"login":"owner"}'),
                HttpResponse(201, b'{"name":"sample","private":true}'),
            ]
        )
        client = ForgejoClient(
            "https://forgejo.example.test", "secret-token", transport
        )

        result = client.create_repo(
            "owner", "sample", "A sample", private=True, default_branch="main"
        )

        self.assertTrue(result["private"])
        method, url, headers, body = transport.calls[1]
        self.assertEqual(
            (method, url), ("POST", "https://forgejo.example.test/api/v1/user/repos")
        )
        self.assertEqual(
            json.loads(body),
            {
                "name": "sample",
                "description": "A sample",
                "private": True,
                "auto_init": True,
                "default_branch": "main",
            },
        )
        self.assertEqual(headers["Authorization"], "token secret-token")
        self.assertNotIn("secret-token", url)
        self.assertNotIn(b"secret-token", body)

    def test_create_repository_uses_organization_endpoint(self):
        transport = RecordingTransport(
            [
                HttpResponse(200, b'{"login":"authenticated-user"}'),
                HttpResponse(200, b'{"username":"owner"}'),
                HttpResponse(201, b'{"name":"sample","private":true}'),
            ]
        )
        client = ForgejoClient("https://forgejo.example.test", "token", transport)

        client.create_repo(
            "owner", "sample", "A sample", private=True, default_branch="main"
        )

        self.assertEqual(
            transport.calls[-1][1],
            "https://forgejo.example.test/api/v1/orgs/owner/repos",
        )

    def test_push_mirror_payload_enables_ssh_and_sync_on_commit(self):
        transport = RecordingTransport(
            [
                HttpResponse(
                    201, b'{"remote_address":"git@github.com:gh-owner/sample.git"}'
                )
            ]
        )
        client = ForgejoClient("https://forgejo.example.test", "token", transport)

        client.create_push_mirror(
            "owner",
            "sample",
            "git@github.com:gh-owner/sample.git",
            interval="8h",
        )

        method, url, _, body = transport.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(
            url, "https://forgejo.example.test/api/v1/repos/owner/sample/push_mirrors"
        )
        self.assertEqual(
            json.loads(body),
            {
                "remote_address": "git@github.com:gh-owner/sample.git",
                "sync_on_commit": True,
                "use_ssh": True,
                "interval": "8h",
            },
        )

    def test_api_error_redacts_token_and_url_password(self):
        token = "top-secret-token"
        transport = RecordingTransport(
            [
                HttpResponse(
                    500,
                    b'{"message":"failed with top-secret-token at https://u:p@example.test/x"}',
                )
            ]
        )
        client = ForgejoClient("https://forgejo.example.test", token, transport)

        with self.assertRaises(Exception) as raised:
            client.get_repo("owner", "sample")

        message = str(raised.exception)
        self.assertNotIn(token, message)
        self.assertNotIn("u:p", message)
        self.assertIn("<redacted>", message)


class RecordingRunner:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, args, stdin):
        self.calls.append((list(args), stdin))
        return self.results.pop(0)


class SDKResponse:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


class SDKStatusError(Exception):
    def __init__(self, status_code, message="SDK request failed"):
        super().__init__(message)
        self.response = SimpleNamespace(status_code=status_code)


def github_sdk():
    return SimpleNamespace(
        rest=SimpleNamespace(
            users=SimpleNamespace(
                get_authenticated=Mock(),
                get_by_username=Mock(),
            ),
            orgs=SimpleNamespace(
                get=Mock(),
                get_membership_for_authenticated_user=Mock(),
            ),
            repos=SimpleNamespace(
                get=Mock(),
                create_for_authenticated_user=Mock(),
                create_in_org=Mock(),
                list_deploy_keys=Mock(),
                create_deploy_key=Mock(),
            ),
            actions=SimpleNamespace(
                set_github_actions_permissions_repository=Mock(),
                get_github_actions_permissions_repository=Mock(),
            ),
        )
    )


class GitHubClientTests(unittest.TestCase):
    def test_uses_gh_token_as_sdk_credential_for_enterprise_host(self):
        token = "synthetic-token-value"
        runner = RecordingRunner([CommandResult(0, token + "\n", "")])
        sdk = github_sdk()
        enterprise_rest = sdk.rest
        enterprise_rest.users.get_authenticated.return_value = SDKResponse(
            {"login": "owner"}
        )
        sdk.rest = Mock(return_value=enterprise_rest)
        factory_calls = []

        client = GitHubClient(
            runner,
            host="github.enterprise.test",
            sdk_factory=lambda credential, base_url: (
                factory_calls.append((credential, base_url)) or sdk
            ),
        )

        self.assertEqual(client.authenticated_user(), {"login": "owner"})
        self.assertEqual(
            runner.calls,
            [
                (
                    [
                        "gh",
                        "auth",
                        "token",
                        "--hostname",
                        "github.enterprise.test",
                    ],
                    None,
                )
            ],
        )
        self.assertEqual(
            factory_calls, [(token, "https://github.enterprise.test/api/v3")]
        )
        sdk.rest.assert_called_once_with("2022-11-28")
        self.assertNotIn(token, repr(client))

    def test_gh_token_failure_is_fail_closed_and_redacted(self):
        token = "synthetic-token-value"
        runner = RecordingRunner(
            [CommandResult(1, "", f"authentication failed for token {token}")]
        )

        with self.assertRaises(ApiError) as raised:
            GitHubClient(runner).authenticated_user()

        self.assertNotIn(token, str(raised.exception))

    def test_allow_404_only_accepts_explicit_sdk_status(self):
        missing_sdk = github_sdk()
        missing_sdk.rest.repos.get.side_effect = SDKStatusError(404)
        ambiguous_sdk = github_sdk()
        ambiguous_sdk.rest.repos.get.side_effect = SDKStatusError(
            500, "Not Found while authorization status is unknown"
        )

        self.assertIsNone(GitHubClient(sdk=missing_sdk).get_repo("gh-owner", "sample"))
        with self.assertRaises(ApiError):
            GitHubClient(sdk=ambiguous_sdk).get_repo("gh-owner", "sample")

    def test_owner_access_uses_organization_membership_and_policy(self):
        sdk = github_sdk()
        sdk.rest.users.get_authenticated.return_value = SDKResponse(
            {"login": "someone-else"}
        )
        sdk.rest.users.get_by_username.return_value = SDKResponse(
            {"login": "gh-owner", "type": "Organization"}
        )
        sdk.rest.orgs.get.return_value = SDKResponse(
            {"login": "gh-owner", "members_can_create_repositories": True}
        )
        sdk.rest.orgs.get_membership_for_authenticated_user.return_value = SDKResponse(
            {"state": "active", "role": "member"}
        )

        access = GitHubClient(sdk=sdk).get_owner_access("gh-owner")

        self.assertEqual(
            access, {"exists": True, "can_create": True, "can_admin": False}
        )
        sdk.rest.orgs.get.assert_called_once_with(org="gh-owner")

    def test_create_repository_uses_private_api_payload(self):
        sdk = github_sdk()
        sdk.rest.users.get_authenticated.return_value = SDKResponse(
            {"login": "gh-owner"}
        )
        sdk.rest.repos.create_for_authenticated_user.return_value = SDKResponse(
            {"name": "sample", "private": True}
        )
        client = GitHubClient(sdk=sdk)

        result = client.create_repo("gh-owner", "sample", "A sample", private=True)

        self.assertTrue(result["private"])
        sdk.rest.repos.create_for_authenticated_user.assert_called_once_with(
            name="sample", description="A sample", private=True
        )

    def test_create_organization_repository_uses_sdk_org_operation(self):
        sdk = github_sdk()
        sdk.rest.users.get_authenticated.return_value = SDKResponse(
            {"login": "someone-else"}
        )
        sdk.rest.repos.create_in_org.return_value = SDKResponse(
            {"name": "sample", "private": True}
        )
        client = GitHubClient(sdk=sdk)

        client.create_repo("gh-owner", "sample", "A sample", private=True)

        sdk.rest.repos.create_in_org.assert_called_once_with(
            "gh-owner", name="sample", description="A sample", private=True
        )

    def test_deploy_key_listing_paginates_at_sdk_boundary(self):
        sdk = github_sdk()
        first_page = [{"id": index} for index in range(100)]
        sdk.rest.repos.list_deploy_keys.side_effect = [
            SDKResponse(first_page),
            SDKResponse([{"id": 100}]),
        ]

        keys = GitHubClient(sdk=sdk).list_deploy_keys("gh-owner", "sample")

        self.assertEqual(len(keys), 101)
        self.assertEqual(
            sdk.rest.repos.list_deploy_keys.call_args_list,
            [
                call("gh-owner", "sample", per_page=100, page=1),
                call("gh-owner", "sample", per_page=100, page=2),
            ],
        )

    def test_registers_writable_deploy_key(self):
        sdk = github_sdk()
        sdk.rest.repos.create_deploy_key.return_value = SDKResponse({"id": 123})
        client = GitHubClient(sdk=sdk)

        result = client.add_deploy_key(
            "gh-owner", "sample", "Forgejo mirror: owner/sample", "ssh-ed25519 AAAAtest"
        )

        self.assertEqual(result, {"id": 123})
        sdk.rest.repos.create_deploy_key.assert_called_once_with(
            "gh-owner",
            "sample",
            title="Forgejo mirror: owner/sample",
            key="ssh-ed25519 AAAAtest",
            read_only=False,
        )

    def test_disables_actions_for_mirror_repository(self):
        sdk = github_sdk()
        sdk.rest.actions.set_github_actions_permissions_repository.return_value = (
            SDKResponse(None)
        )
        client = GitHubClient(sdk=sdk)

        client.disable_actions("gh-owner", "sample")

        sdk.rest.actions.set_github_actions_permissions_repository.assert_called_once_with(
            "gh-owner", "sample", enabled=False
        )

    def test_reads_actions_permissions_for_verification(self):
        sdk = github_sdk()
        sdk.rest.actions.get_github_actions_permissions_repository.return_value = (
            SDKResponse({"enabled": False})
        )
        client = GitHubClient(sdk=sdk)

        result = client.get_actions_permissions("gh-owner", "sample")

        self.assertIs(result["enabled"], False)
        sdk.rest.actions.get_github_actions_permissions_repository.assert_called_once_with(
            "gh-owner", "sample"
        )

    def test_malformed_sdk_response_fails_closed(self):
        sdk = github_sdk()
        sdk.rest.users.get_authenticated.return_value = SDKResponse(
            [{"login": "owner"}]
        )

        with self.assertRaises(ApiError):
            GitHubClient(sdk=sdk).authenticated_user()


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


class RedirectSafetyTests(unittest.TestCase):
    def test_transport_does_not_follow_redirect_with_authorization(self):
        observed_headers = []

        class Target(BaseHTTPRequestHandler):
            def do_GET(self):
                observed_headers.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()

            def log_message(self, format, *args):
                return

        target = ThreadingHTTPServer(("127.0.0.1", 0), Target)

        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header(
                    "Location",
                    f"http://127.0.0.1:{target.server_port}/capture",
                )
                self.end_headers()

            def log_message(self, format, *args):
                return

        redirect = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        threads = [
            threading.Thread(target=server.serve_forever, daemon=True)
            for server in (target, redirect)
        ]
        for thread in threads:
            thread.start()
        try:
            response = _urllib_transport(
                "GET",
                f"http://127.0.0.1:{redirect.server_port}/start",
                {"Authorization": "token synthetic-secret"},
                None,
            )
        finally:
            redirect.shutdown()
            target.shutdown()
            redirect.server_close()
            target.server_close()

        self.assertEqual(response.status, 302)
        self.assertEqual(observed_headers, [])


if __name__ == "__main__":
    unittest.main()
