from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call

import httpx
from pyforgejo import (
    Organization,
    OrganizationPermissions,
    PushMirror,
    PyforgejoApi,
    Repository,
    ServerVersion,
    User,
)
from pyforgejo.core.api_error import ApiError as SdkApiError
from pyforgejo.errors.not_found_error import NotFoundError

from repo_bootstrap.errors import ApiError
from repo_bootstrap.forgejo import ForgejoClient


FIXTURES = Path(__file__).parent / "fixtures" / "forgejo-11.0.16"


def fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def sdk_model(model_type, name: str):
    return model_type.model_validate(fixture(name))


def mock_sdk() -> SimpleNamespace:
    return SimpleNamespace(
        user=Mock(),
        organization=Mock(),
        repository=Mock(),
        miscellaneous=Mock(),
    )


class ForgejoSdkBoundaryTests(unittest.TestCase):
    def test_rejects_unsafe_urls_and_empty_credentials_before_sdk_creation(self):
        for base_url, token in (
            ("http://forgejo.example.test", "token"),
            ("https://user:password@forgejo.example.test", "token"),
            ("https://forgejo.example.test?redirect=unsafe", "token"),
            ("https://forgejo.example.test", ""),
        ):
            with self.subTest(base_url=base_url, token_present=bool(token)):
                with self.assertRaises(ApiError):
                    ForgejoClient(base_url, token)

    def test_configures_secure_sdk_transport_for_custom_base_url(self):
        sdk = mock_sdk()
        factory = Mock(return_value=sdk)

        client = ForgejoClient(
            "https://forgejo.example.test/forge/", "secret-token", sdk_factory=factory
        )

        self.assertEqual(client.base_url, "https://forgejo.example.test/forge")
        factory.assert_called_once_with(
            base_url="https://forgejo.example.test/forge/api/v1",
            api_key="secret-token",
            timeout=30.0,
            follow_redirects=False,
        )

    def test_reads_version_user_repository_and_explicit_not_found(self):
        sdk = mock_sdk()
        sdk.miscellaneous.get_version.return_value = sdk_model(
            ServerVersion, "version.json"
        )
        sdk.user.get_current.return_value = sdk_model(User, "user.json")
        sdk.repository.repo_get.side_effect = [
            sdk_model(Repository, "repository.json"),
            NotFoundError(body={"message": "not found"}),
        ]
        client = ForgejoClient("https://forgejo.example.test", "token", sdk=sdk)

        self.assertEqual(client.version()["version"], "11.0.16")
        self.assertEqual(client.authenticated_user()["login"], "alice")
        repo = client.get_repo("acme", "sample")
        self.assertEqual(repo["full_name"], "acme/sample")
        self.assertTrue(repo["permissions"]["admin"])
        self.assertIsNone(client.get_repo("acme", "missing"))
        self.assertEqual(
            sdk.repository.repo_get.call_args_list,
            [
                call("acme", "sample"),
                call("acme", "missing"),
            ],
        )

    def test_maps_user_and_organization_repository_creation_permissions(self):
        sdk = mock_sdk()
        sdk.user.get_current.return_value = sdk_model(User, "user.json")
        sdk.organization.org_get.return_value = sdk_model(
            Organization, "organization.json"
        )
        sdk.organization.org_get_user_permissions.return_value = sdk_model(
            OrganizationPermissions, "organization-permissions.json"
        )
        client = ForgejoClient("https://forgejo.example.test", "token", sdk=sdk)

        self.assertEqual(
            client.get_owner_access("alice"),
            {"exists": True, "can_create": True, "can_admin": True},
        )
        self.assertEqual(
            client.get_owner_access("acme"),
            {"exists": True, "can_create": True, "can_admin": False},
        )
        sdk.organization.org_get_user_permissions.assert_called_once_with(
            "alice", "acme"
        )

    def test_creates_user_and_organization_repositories_through_sdk(self):
        sdk = mock_sdk()
        user = sdk_model(User, "user.json")
        repository = sdk_model(Repository, "repository.json")
        sdk.user.get_current.return_value = user
        sdk.repository.create_current_user_repo.return_value = repository
        sdk.organization.org_get.return_value = sdk_model(
            Organization, "organization.json"
        )
        sdk.organization.create_org_repo.return_value = repository
        client = ForgejoClient("https://forgejo.example.test", "token", sdk=sdk)

        client.create_repo(
            "alice", "sample", "A sample", private=True, default_branch="main"
        )
        client.create_repo(
            "acme", "sample", "A sample", private=True, default_branch="main"
        )

        expected = {
            "name": "sample",
            "description": "A sample",
            "private": True,
            "auto_init": True,
            "default_branch": "main",
        }
        sdk.repository.create_current_user_repo.assert_called_once_with(
            **expected, request_options={"max_retries": 0}
        )
        sdk.organization.create_org_repo.assert_called_once_with(
            "acme", **expected, request_options={"max_retries": 0}
        )

    def test_lists_repository_pages_and_current_user_organizations(self):
        sdk = mock_sdk()
        sdk.user.current_list_repos.return_value = [
            sdk_model(Repository, "repository.json")
        ]
        sdk.organization.org_list_current_user_orgs.return_value = [
            sdk_model(Organization, "organization.json")
        ]
        client = ForgejoClient("https://forgejo.example.test", "token", sdk=sdk)

        repos = client.list_repos(page=3, limit=25)
        organizations = client.list_user_orgs()

        self.assertEqual(repos[0]["full_name"], "acme/sample")
        self.assertEqual(organizations[0]["name"], "acme")
        sdk.user.current_list_repos.assert_called_once_with(page=3, limit=25)
        sdk.organization.org_list_current_user_orgs.assert_called_once_with()

    def test_normalizes_forgejo_11_push_mirror_and_enables_secure_creation(self):
        sdk = mock_sdk()
        mirror = sdk_model(PushMirror, "push-mirror.json")
        sdk.repository.repo_list_push_mirrors.return_value = [mirror]
        sdk.repository.repo_add_push_mirror.return_value = mirror
        client = ForgejoClient("https://forgejo.example.test", "token", sdk=sdk)

        listed = client.list_push_mirrors("acme", "sample")
        created = client.create_push_mirror(
            "acme",
            "sample",
            "git@github.com:gh-owner/sample.git",
            interval="",
        )

        for result in (listed[0], created):
            self.assertTrue(result["use_ssh"])
            self.assertTrue(result["sync_on_commit"])
            self.assertTrue(result["public_key"].startswith("ssh-ed25519 "))
            self.assertEqual(result["last_error"], "")
        sdk.repository.repo_add_push_mirror.assert_called_once_with(
            "acme",
            "sample",
            remote_address="git@github.com:gh-owner/sample.git",
            sync_on_commit=True,
            use_ssh=True,
            interval="8h",
            request_options={"max_retries": 0},
        )

    def test_syncs_all_push_mirrors_through_generated_endpoint(self):
        sdk = mock_sdk()
        client = ForgejoClient("https://forgejo.example.test", "token", sdk=sdk)

        client.sync_push_mirrors("acme", "sample")

        sdk.repository.repo_push_mirror_sync.assert_called_once_with(
            "acme", "sample", request_options={"max_retries": 0}
        )

    def test_redacts_sdk_error_body_and_token(self):
        sdk = mock_sdk()
        token = "top-secret-token"
        sdk.repository.repo_get.side_effect = SdkApiError(
            status_code=500,
            body={
                "message": (
                    "failed with top-secret-token at "
                    "https://person:password@example.test/private"
                )
            },
        )
        client = ForgejoClient("https://forgejo.example.test", token, sdk=sdk)

        with self.assertRaises(ApiError) as raised:
            client.get_repo("acme", "sample")

        message = str(raised.exception)
        self.assertIn("HTTP 500", message)
        self.assertIn("<redacted>", message)
        self.assertNotIn(token, message)
        self.assertNotIn("person:password", message)


class ForgejoElevenContractTests(unittest.TestCase):
    def make_client(self):
        requests: list[httpx.Request] = []
        repo = fixture("repository.json")
        routes = {
            ("GET", "/forge/api/v1/version"): (200, fixture("version.json")),
            ("GET", "/forge/api/v1/user"): (200, fixture("user.json")),
            ("GET", "/forge/api/v1/orgs/acme"): (
                200,
                fixture("organization.json"),
            ),
            ("GET", "/forge/api/v1/users/alice/orgs/acme/permissions"): (
                200,
                fixture("organization-permissions.json"),
            ),
            ("GET", "/forge/api/v1/repos/acme/sample"): (200, repo),
            ("GET", "/forge/api/v1/repos/acme/missing"): (
                404,
                {"message": "target not found"},
            ),
            ("GET", "/forge/api/v1/user/repos"): (200, [repo]),
            ("POST", "/forge/api/v1/user/repos"): (201, repo),
            ("POST", "/forge/api/v1/orgs/acme/repos"): (201, repo),
            ("GET", "/forge/api/v1/repos/acme/sample/push_mirrors"): (
                200,
                [fixture("push-mirror.json")],
            ),
            ("POST", "/forge/api/v1/repos/acme/sample/push_mirrors"): (
                200,
                fixture("push-mirror.json"),
            ),
            ("POST", "/forge/api/v1/repos/acme/sample/push_mirrors-sync"): (
                200,
                None,
            ),
        }

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            key = (request.method, request.url.path)
            status, payload = routes[key]
            return httpx.Response(status, json=payload, request=request)

        http_client = httpx.Client(
            transport=httpx.MockTransport(handler),
            timeout=30.0,
            follow_redirects=False,
        )
        sdk = PyforgejoApi(
            base_url="https://forgejo.example.test/forge/api/v1",
            api_key="contract-token",
            timeout=30.0,
            httpx_client=http_client,
        )
        return (
            ForgejoClient(
                "https://forgejo.example.test/forge", "contract-token", sdk=sdk
            ),
            requests,
            http_client,
        )

    def test_forgejo_11_response_codes_schemas_paths_and_payloads(self):
        client, requests, http_client = self.make_client()
        try:
            self.assertEqual(client.version()["version"], "11.0.16")
            self.assertEqual(client.authenticated_user()["login"], "alice")
            self.assertEqual(
                client.get_owner_access("acme"),
                {"exists": True, "can_create": True, "can_admin": False},
            )
            self.assertEqual(client.get_repo("acme", "sample")["name"], "sample")
            self.assertIsNone(client.get_repo("acme", "missing"))
            self.assertEqual(client.list_repos(page=2, limit=50)[0]["private"], True)
            client.create_repo(
                "alice", "sample", "A sample", private=True, default_branch="main"
            )
            client.create_repo(
                "acme", "sample", "A sample", private=True, default_branch="main"
            )
            mirror = client.create_push_mirror(
                "acme",
                "sample",
                "git@github.com:gh-owner/sample.git",
                interval="8h",
            )
            self.assertTrue(mirror["use_ssh"])
            self.assertEqual(
                client.list_push_mirrors("acme", "sample")[0]["last_error"], ""
            )
            client.sync_push_mirrors("acme", "sample")
        finally:
            http_client.close()

        self.assertTrue(
            all(request.url.path.startswith("/forge/api/v1/") for request in requests)
        )
        self.assertTrue(
            all(request.headers["authorization"] == "token contract-token" for request in requests)
        )
        list_request = next(
            request
            for request in requests
            if request.method == "GET" and request.url.path.endswith("/user/repos")
        )
        self.assertEqual(list_request.url.params["page"], "2")
        self.assertEqual(list_request.url.params["limit"], "50")
        mirror_request = next(
            request
            for request in requests
            if request.method == "POST" and request.url.path.endswith("/push_mirrors")
        )
        self.assertEqual(
            json.loads(mirror_request.content),
            {
                "interval": "8h",
                "remote_address": "git@github.com:gh-owner/sample.git",
                "sync_on_commit": True,
                "use_ssh": True,
            },
        )

    def test_sdk_does_not_follow_redirects_or_forward_credentials(self):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.host == "forgejo.example.test":
                return httpx.Response(
                    302,
                    headers={"location": "https://attacker.example.test/capture"},
                    request=request,
                )
            return httpx.Response(200, json=fixture("version.json"), request=request)

        http_client = httpx.Client(
            transport=httpx.MockTransport(handler),
            timeout=30.0,
            follow_redirects=False,
        )
        sdk = PyforgejoApi(
            base_url="https://forgejo.example.test/api/v1",
            api_key="redirect-secret",
            timeout=30.0,
            httpx_client=http_client,
        )
        client = ForgejoClient(
            "https://forgejo.example.test", "redirect-secret", sdk=sdk
        )
        try:
            with self.assertRaises(ApiError):
                client.version()
        finally:
            http_client.close()

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.host, "forgejo.example.test")


if __name__ == "__main__":
    unittest.main()
