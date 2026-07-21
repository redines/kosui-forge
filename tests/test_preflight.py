import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from repo_bootstrap.config import Config
from repo_bootstrap.preflight import ToolResult, run_preflight


class RecordingCommands:
    def __init__(self, overrides=None):
        self.overrides = overrides or {}
        self.calls = []

    def __call__(self, args):
        command = tuple(args)
        self.calls.append(command)
        for prefix, result in self.overrides.items():
            if command[: len(prefix)] == prefix:
                return result
        defaults = {
            ("git", "--version"): ToolResult(0, "git version 2.45.2\n", ""),
            ("gh", "--version"): ToolResult(0, "gh version 2.52.0 (test)\n", ""),
            ("ssh", "-V"): ToolResult(0, "", "OpenSSH_9.7p1, LibreSSL 3.3.6\n"),
            ("gh", "auth", "status"): ToolResult(0, "authenticated\n", ""),
            ("ssh", "-G"): ToolResult(
                0,
                "hostname forgejo.example.test\nuser git\nport 22\n",
                "",
            ),
        }
        for prefix, result in defaults.items():
            if command[: len(prefix)] == prefix:
                return result
        raise AssertionError(f"unexpected command: {command}")


class FakeForgejo:
    def __init__(self):
        self.repo = None
        self.mirrors = []
        self.version_value = {"version": "11.0.4"}
        self.owner_access = {"exists": True, "can_create": True, "can_admin": True}
        self.calls = []
        self.error = None

    def version(self):
        self.calls.append("version")
        if self.error:
            raise self.error
        return self.version_value

    def authenticated_user(self):
        self.calls.append("authenticated_user")
        return {"login": "forgejo-owner"}

    def get_owner_access(self, owner):
        self.calls.append(("get_owner_access", owner))
        return self.owner_access

    def get_repo(self, owner, name):
        self.calls.append(("get_repo", owner, name))
        return self.repo

    def list_push_mirrors(self, owner, name):
        self.calls.append(("list_push_mirrors", owner, name))
        return self.mirrors


class FakeGitHub:
    def __init__(self):
        self.repo = None
        self.keys = []
        self.owner_access = {"exists": True, "can_create": True, "can_admin": True}
        self.calls = []

    def authenticated_user(self):
        self.calls.append("authenticated_user")
        return {"login": "github-owner"}

    def get_owner_access(self, owner):
        self.calls.append(("get_owner_access", owner))
        return self.owner_access

    def get_repo(self, owner, name):
        self.calls.append(("get_repo", owner, name))
        return self.repo

    def list_deploy_keys(self, owner, name):
        self.calls.append(("list_deploy_keys", owner, name))
        return self.keys

    def get_actions_permissions(self, owner, name):
        self.calls.append(("get_actions_permissions", owner, name))
        return {"enabled": False}


def make_config(root):
    return Config(
        forgejo_url="https://forgejo.example.test",
        forgejo_owner="forgejo-owner",
        github_owner="github-owner",
        projects_root=Path(root),
        ssh_alias="forgejo-work",
        github_host="github.com",
    )


class PreflightTests(unittest.TestCase):
    def run_check(
        self,
        root,
        *,
        forgejo=None,
        github=None,
        commands=None,
        which=None,
        platform_name="linux",
        name="sample",
        description="A sample",
        with_github=True,
    ):
        return run_preflight(
            make_config(root),
            forgejo or FakeForgejo(),
            github or FakeGitHub(),
            token_present=True,
            name=name,
            description=description,
            private=True,
            with_github=with_github,
            command_runner=commands or RecordingCommands(),
            which=which or (lambda executable: f"/usr/bin/{executable}"),
            platform_name=platform_name,
        )

    def test_complete_preflight_reports_actionable_passes_without_writes(self):
        with TemporaryDirectory() as directory:
            forgejo = FakeForgejo()
            github = FakeGitHub()
            commands = RecordingCommands()

            report = self.run_check(
                directory, forgejo=forgejo, github=github, commands=commands
            )

        self.assertTrue(report.ok, report.render())
        self.assertIn("PASS", report.render())
        self.assertIn("gh-auth", report.render())
        self.assertIn("forgejo-mirror-api", report.render())
        self.assertNotIn("create_repo", forgejo.calls)
        self.assertFalse(
            any(
                call[0].startswith("create")
                for call in github.calls
                if isinstance(call, tuple)
            )
        )
        self.assertTrue(all(call[0] in {"git", "gh", "ssh"} for call in commands.calls))

    def test_missing_executables_fail_with_windows_install_guidance(self):
        with TemporaryDirectory() as directory:
            report = self.run_check(
                directory,
                which=lambda _executable: None,
                platform_name="win32",
            )

        self.assertFalse(report.ok)
        rendered = report.render()
        self.assertIn("FAIL", rendered)
        self.assertIn("winget", rendered)
        self.assertIn("git", rendered)
        self.assertIn("gh", rendered)
        self.assertIn("ssh", rendered)

    def test_stale_gh_authentication_fails_closed(self):
        commands = RecordingCommands(
            {
                ("gh", "auth", "status"): ToolResult(
                    1, "", "authentication token expired"
                )
            }
        )
        with TemporaryDirectory() as directory:
            report = self.run_check(directory, commands=commands)

        self.assertFalse(report.ok)
        self.assertIn("gh auth login", report.render())

    def test_unreachable_forgejo_is_redacted_and_fails(self):
        forgejo = FakeForgejo()
        forgejo.error = RuntimeError(
            "Authorization: token ultra-secret https://u:p@forgejo.example.test"
        )
        with TemporaryDirectory() as directory:
            report = self.run_check(directory, forgejo=forgejo)

        self.assertFalse(report.ok)
        rendered = report.render()
        self.assertNotIn("ultra-secret", rendered)
        self.assertNotIn("u:p", rendered)
        self.assertIn("<redacted>", rendered)

    def test_insufficient_forgejo_owner_permission_fails(self):
        forgejo = FakeForgejo()
        forgejo.owner_access = {
            "exists": True,
            "can_create": False,
            "can_admin": False,
        }
        with TemporaryDirectory() as directory:
            report = self.run_check(directory, forgejo=forgejo)

        self.assertFalse(report.ok)
        self.assertIn("repository-create permission", report.render())
        self.assertIn("push-mirror admin permission", report.render())

    def test_unsupported_forgejo_mirror_api_fails(self):
        forgejo = FakeForgejo()
        forgejo.version_value = {"version": "10.0.0"}
        with TemporaryDirectory() as directory:
            report = self.run_check(directory, forgejo=forgejo)

        self.assertFalse(report.ok)
        self.assertIn("Forgejo 11", report.render())

    def test_insufficient_github_owner_permission_fails(self):
        github = FakeGitHub()
        github.owner_access = {
            "exists": True,
            "can_create": False,
            "can_admin": False,
        }
        with TemporaryDirectory() as directory:
            report = self.run_check(directory, github=github)

        self.assertFalse(report.ok)
        self.assertIn("GitHub owner", report.render())

    def test_public_github_collision_fails_before_reuse(self):
        github = FakeGitHub()
        github.repo = {
            "name": "sample",
            "full_name": "github-owner/sample",
            "private": False,
            "description": "A sample",
            "permissions": {"admin": True},
        }
        with TemporaryDirectory() as directory:
            report = self.run_check(directory, github=github)

        self.assertFalse(report.ok)
        self.assertIn("visibility collision", report.render())

    def test_unsafe_local_destination_collision_fails(self):
        with TemporaryDirectory() as directory:
            (Path(directory) / "sample").write_text("not a directory", encoding="utf-8")
            report = self.run_check(directory)

        self.assertFalse(report.ok)
        self.assertIn("local path collision", report.render())

    def test_mirror_without_ssh_and_deploy_key_mismatch_fail(self):
        address = "git@github.com:github-owner/sample.git"
        public_key = "ssh-ed25519 AAAAnew"
        forgejo = FakeForgejo()
        forgejo.repo = {
            "name": "sample",
            "full_name": "forgejo-owner/sample",
            "private": True,
            "description": "A sample",
            "permissions": {"admin": True},
        }
        forgejo.mirrors = [
            {
                "remote_address": address,
                "use_ssh": False,
                "sync_on_commit": True,
                "public_key": public_key,
                "last_error": "",
            }
        ]
        github = FakeGitHub()
        github.repo = {
            "name": "sample",
            "full_name": "github-owner/sample",
            "private": True,
            "description": "A sample",
            "permissions": {"admin": True},
        }
        github.keys = [
            {
                "title": "Forgejo mirror: forgejo-owner/sample",
                "key": "ssh-ed25519 AAAAold",
                "read_only": False,
            }
        ]
        with TemporaryDirectory() as directory:
            report = self.run_check(directory, forgejo=forgejo, github=github)

        self.assertFalse(report.ok)
        self.assertIn("use_ssh=true", report.render())
        self.assertIn("deploy-key collision", report.render())

    def test_invalid_mirror_public_key_fails_before_github_creation(self):
        forgejo = FakeForgejo()
        forgejo.repo = {
            "name": "sample",
            "full_name": "forgejo-owner/sample",
            "owner": {"login": "forgejo-owner"},
            "private": True,
            "description": "A sample",
            "permissions": {"admin": True},
        }
        forgejo.mirrors = [
            {
                "remote_address": "git@github.com:github-owner/sample.git",
                "use_ssh": True,
                "sync_on_commit": True,
                "public_key": "not-an-ssh-public-key",
                "last_error": "",
            }
        ]

        with TemporaryDirectory() as directory:
            report = self.run_check(directory, forgejo=forgejo)

        self.assertFalse(report.ok)
        self.assertIn("public key", report.render())

    def test_missing_repository_identity_metadata_fails_closed(self):
        forgejo = FakeForgejo()
        forgejo.repo = {
            "name": "sample",
            "private": True,
            "description": "A sample",
            "permissions": {"admin": True},
        }
        github = FakeGitHub()
        github.repo = {
            "name": "sample",
            "private": True,
            "description": "A sample",
            "permissions": {"admin": True},
        }

        with TemporaryDirectory() as directory:
            report = self.run_check(directory, forgejo=forgejo, github=github)

        self.assertFalse(report.ok)
        self.assertIn("ownership", report.render().lower())

    def test_stale_titled_deploy_key_without_mirror_fails_before_writes(self):
        github = FakeGitHub()
        github.repo = {
            "name": "sample",
            "full_name": "github-owner/sample",
            "owner": {"login": "github-owner"},
            "private": True,
            "description": "A sample",
            "permissions": {"admin": True},
        }
        github.keys = [
            {
                "title": "Forgejo mirror: forgejo-owner/sample",
                "key": "ssh-ed25519 AAAAstale",
                "read_only": False,
            }
        ]

        with TemporaryDirectory() as directory:
            report = self.run_check(directory, github=github)

        self.assertFalse(report.ok)
        self.assertIn("deploy-key collision", report.render())

    def test_projects_root_can_be_safely_created_without_preflight_creating_it(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "nested" / "projects"
            report = self.run_check(root)

            self.assertTrue(report.ok, report.render())
            self.assertFalse(root.exists())

    def test_required_token_is_reported_by_name_without_value(self):
        with TemporaryDirectory() as directory:
            report = run_preflight(
                make_config(directory),
                FakeForgejo(),
                FakeGitHub(),
                token_present=False,
                name="sample",
                description="A sample",
                private=True,
                with_github=True,
                command_runner=RecordingCommands(),
                which=lambda executable: f"/usr/bin/{executable}",
            )

        self.assertFalse(report.ok)
        self.assertIn("FORGEJO_TOKEN", report.render())


if __name__ == "__main__":
    unittest.main()
