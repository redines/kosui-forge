import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest

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
