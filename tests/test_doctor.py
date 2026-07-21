import tempfile
import unittest
from pathlib import Path

from repo_bootstrap.config import Config
from repo_bootstrap.doctor import Doctor
from repo_bootstrap.errors import ApiError
from repo_bootstrap.github import CommandResult


class FakeRunner:
    def __init__(self, overrides=None):
        self.overrides = overrides or {}
        self.calls = []

    def __call__(self, args, stdin):
        key = tuple(args)
        self.calls.append(key)
        if key in self.overrides:
            value = self.overrides[key]
            if isinstance(value, Exception):
                raise value
            return value
        outputs = {
            "git": "git version 2.45.1\n",
            "gh": "gh version 2.50.0\n",
            "ssh": "OpenSSH_9.7p1\n",
        }
        if args[:2] == ["ssh", "-G"]:
            return CommandResult(0, "hostname forgejo.example.test\nuser git\n", "")
        if args[:3] == ["gh", "auth", "status"]:
            return CommandResult(0, "authenticated\n", "")
        return CommandResult(0, outputs.get(args[0], ""), "")


class FakeForgejo:
    def __init__(self, *, error=None, repo=None, admin=True, version="11.0.1"):
        self.error = error
        self.repo = repo
        self.admin = admin
        self.version_value = version
        self.writes = []

    def get_version(self):
        if self.error:
            raise self.error
        return {"version": self.version_value}

    def authenticated_user(self):
        if self.error:
            raise self.error
        return {"login": "forgejo-user"}

    def get_org(self, owner):
        return {"username": owner}

    def list_user_orgs(self):
        return [{"username": "forgejo-owner"}]

    def get_repo(self, owner, name):
        if self.repo is None:
            return None
        result = dict(self.repo)
        result.setdefault("full_name", f"{owner}/{name}")
        result.setdefault("private", True)
        result.setdefault("description", "A sample")
        result.setdefault("permissions", {"admin": self.admin})
        return result

    def list_push_mirrors(self, owner, name):
        return []


class FakeGitHub:
    def __init__(self, *, repo=None, auth_error=None):
        self.repo = repo
        self.auth_error = auth_error

    def authenticated_user(self):
        if self.auth_error:
            raise self.auth_error
        return {"login": "github-user"}

    def get_owner(self, owner):
        return {"login": owner, "type": "Organization"}

    def get_membership(self, owner):
        return {"state": "active", "role": "admin"}

    def get_repo(self, owner, name):
        return self.repo

    def list_deploy_keys(self, owner, name):
        return []

    def get_actions_permissions(self, owner, name):
        return {"enabled": False}


class DoctorTests(unittest.TestCase):
    def config(self, root):
        return Config(
            forgejo_url="https://forgejo.example.test",
            forgejo_owner="forgejo-owner",
            github_owner="github-owner",
            projects_root=root,
            ssh_alias="forgejo-work",
            owner_map={"forgejo-owner": "github-owner"},
        )

    def test_healthy_preflight_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            forgejo = FakeForgejo()
            doctor = Doctor(
                self.config(Path(directory)),
                forgejo,
                FakeGitHub(),
                FakeRunner(),
                environ={"FORGEJO_TOKEN": "synthetic"},
                system="Linux",
            )
            report = doctor.run(
                name="sample",
                description="A sample",
                with_github=True,
                private=True,
            )

        self.assertTrue(report.ok, report.render())
        self.assertEqual(forgejo.writes, [])
        self.assertNotIn("synthetic", report.render())

    def test_missing_executable_has_platform_install_guidance(self):
        for system, guidance in (
            ("Linux", "package manager"),
            ("Darwin", "brew install"),
            ("Windows", "winget install"),
        ):
            with (
                self.subTest(system=system),
                tempfile.TemporaryDirectory() as directory,
            ):
                runner = FakeRunner(
                    {
                        ("gh", "--version"): ApiError("gh is not installed"),
                    }
                )
                report = Doctor(
                    self.config(Path(directory)),
                    FakeForgejo(),
                    FakeGitHub(),
                    runner,
                    environ={"FORGEJO_TOKEN": "synthetic"},
                    system=system,
                ).run(name="sample", description="A sample", with_github=True)

                self.assertFalse(report.ok)
                self.assertIn(guidance, report.render())

    def test_unauthenticated_gh_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner(
                {
                    (
                        "gh",
                        "auth",
                        "status",
                        "--hostname",
                        "github.com",
                    ): CommandResult(1, "", "authentication expired"),
                }
            )
            report = Doctor(
                self.config(Path(directory)),
                FakeForgejo(),
                FakeGitHub(auth_error=ApiError("unauthenticated")),
                runner,
                environ={"FORGEJO_TOKEN": "synthetic"},
                system="Linux",
            ).run(name="sample", description="A sample", with_github=True)

        self.assertFalse(report.ok)
        self.assertIn("gh-auth", report.render())

    def test_unreachable_forgejo_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Doctor(
                self.config(Path(directory)),
                FakeForgejo(error=ApiError("network unreachable")),
                FakeGitHub(),
                FakeRunner(),
                environ={"FORGEJO_TOKEN": "synthetic"},
                system="Linux",
            ).run(name="sample", description="A sample", with_github=True)

        self.assertFalse(report.ok)
        self.assertIn("network unreachable", report.render())

    def test_existing_repo_without_admin_permission_blocks_mirror(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Doctor(
                self.config(Path(directory)),
                FakeForgejo(repo={}, admin=False),
                FakeGitHub(),
                FakeRunner(),
                environ={"FORGEJO_TOKEN": "synthetic"},
                system="Linux",
            ).run(name="sample", description="A sample", with_github=True)

        self.assertFalse(report.ok)
        self.assertIn("push-mirror admin permission", report.render())

    def test_unsupported_forgejo_mirror_api_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Doctor(
                self.config(Path(directory)),
                FakeForgejo(version="8.0.0"),
                FakeGitHub(),
                FakeRunner(),
                environ={"FORGEJO_TOKEN": "synthetic"},
                system="Linux",
            ).run(name="sample", description="A sample", with_github=True)

        self.assertFalse(report.ok)
        self.assertIn("Forgejo 11", report.render())


if __name__ == "__main__":
    unittest.main()
