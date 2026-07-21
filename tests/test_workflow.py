import tempfile
import unittest
from pathlib import Path

from repo_bootstrap.config import Config
from repo_bootstrap.errors import ApiError, PartialFailure, SafetyError
from repo_bootstrap.gitops import GitResult, clone_and_verify
from repo_bootstrap.mirror import ensure_github_mirror
from repo_bootstrap.workflow import RepositoryManager


class FakeForgejoMirror:
    def __init__(self, mirrors, created=None):
        self.mirror_results = list(mirrors)
        self.created_result = created
        self.created = []
        self.synced = []

    def list_push_mirrors(self, owner, name):
        return self.mirror_results.pop(0)

    def create_push_mirror(self, owner, name, remote, *, interval):
        self.created.append((owner, name, remote, interval))
        return self.created_result

    def sync_push_mirrors(self, owner, name):
        self.synced.append((owner, name))


class FailingMirrorCreationForgejo(FakeForgejoMirror):
    def create_push_mirror(self, owner, name, remote, *, interval):
        raise ApiError("Forgejo rejected mirror creation")


class FakeGitHubMirror:
    def __init__(self, repo=None, keys=None, *, actions_enabled=True):
        self.repo = repo
        self.keys = list(keys or [])
        self.actions_enabled = actions_enabled
        self.created = []
        self.disabled = []
        self.added = []

    def get_repo(self, owner, name):
        return self.repo

    def create_repo(self, owner, name, description, *, private):
        self.created.append((owner, name, description, private))
        self.repo = {
            "name": name,
            "full_name": f"{owner}/{name}",
            "owner": {"login": owner},
            "private": private,
            "description": description,
            "permissions": {"admin": True},
        }
        return self.repo

    def disable_actions(self, owner, name):
        self.disabled.append((owner, name))
        self.actions_enabled = False

    def get_actions_permissions(self, owner, name):
        return {"enabled": self.actions_enabled}

    def list_deploy_keys(self, owner, name):
        return self.keys

    def add_deploy_key(self, owner, name, title, public_key):
        self.added.append((owner, name, title, public_key))
        return {"title": title, "key": public_key, "read_only": False}


class FailingCreateGitHub(FakeGitHubMirror):
    def create_repo(self, owner, name, description, *, private):
        self.created.append((owner, name, description, private))
        raise ApiError("connection lost after GitHub create request")


class MalformedCreatedGitHub(FakeGitHubMirror):
    def create_repo(self, owner, name, description, *, private):
        self.created.append((owner, name, description, private))
        self.repo = {
            "name": name,
            "full_name": f"other/{name}",
            "owner": {"login": "other"},
            "private": private,
            "description": description,
            "permissions": {"admin": True},
        }
        return self.repo


class FailingActionsGitHub(FakeGitHubMirror):
    def disable_actions(self, owner, name):
        raise ApiError("GitHub rejected Actions update")


class MirrorWorkflowTests(unittest.TestCase):
    def test_creates_private_github_repo_writable_key_and_verified_mirror(self):
        address = "git@github.com:gh-owner/sample.git"
        key = "ssh-ed25519 AAAAtest"
        created_mirror = {
            "remote_address": address,
            "public_key": key,
            "use_ssh": True,
            "sync_on_commit": True,
        }
        verified_mirror = {**created_mirror, "last_error": ""}
        forgejo = FakeForgejoMirror([[], [verified_mirror]], created=created_mirror)
        github = FakeGitHubMirror()

        result = ensure_github_mirror(
            forgejo,
            github,
            forgejo_owner="owner",
            github_owner="gh-owner",
            name="sample",
            description="A sample",
            private=True,
            sleeper=lambda _: None,
        )

        self.assertEqual(github.created, [("gh-owner", "sample", "A sample", True)])
        self.assertEqual(github.disabled, [("gh-owner", "sample")])
        self.assertEqual(
            github.added,
            [("gh-owner", "sample", "Forgejo mirror: owner/sample", key)],
        )
        self.assertEqual(forgejo.created, [("owner", "sample", address, "8h")])
        self.assertEqual(forgejo.synced, [("owner", "sample")])
        self.assertEqual(result["github_actions"], "disabled")
        self.assertEqual(result["mirror"], "verified")

    def test_github_create_request_failure_is_partial_with_ambiguous_outcome(self):
        forgejo = FakeForgejoMirror([])
        github = FailingCreateGitHub()

        with self.assertRaisesRegex(PartialFailure, "create request"):
            ensure_github_mirror(
                forgejo,
                github,
                forgejo_owner="owner",
                github_owner="gh-owner",
                name="sample",
                description="A sample",
                private=True,
            )

        self.assertEqual(len(github.created), 1)
        self.assertEqual(forgejo.created, [])

    def test_created_github_repository_identity_mismatch_is_partial_before_mirror(self):
        forgejo = FakeForgejoMirror([[]])
        github = MalformedCreatedGitHub()

        with self.assertRaisesRegex(PartialFailure, "identity collision"):
            ensure_github_mirror(
                forgejo,
                github,
                forgejo_owner="owner",
                github_owner="gh-owner",
                name="sample",
                description="A sample",
                private=True,
            )

        self.assertEqual(len(github.created), 1)
        self.assertEqual(forgejo.created, [])

    def test_existing_repo_mirror_and_key_are_not_duplicated(self):
        address = "git@github.com:gh-owner/sample.git"
        key = "ssh-ed25519 AAAAtest"
        mirror = {
            "remote_address": address,
            "public_key": key,
            "use_ssh": True,
            "sync_on_commit": True,
            "last_error": "",
        }
        forgejo = FakeForgejoMirror([[mirror], [mirror]])
        github = FakeGitHubMirror(
            repo={
                "name": "sample",
                "full_name": "gh-owner/sample",
                "owner": {"login": "gh-owner"},
                "private": True,
                "description": "A sample",
                "permissions": {"admin": True},
            },
            keys=[
                {
                    "title": "Forgejo mirror: owner/sample",
                    "key": key,
                    "read_only": False,
                }
            ],
        )

        result = ensure_github_mirror(
            forgejo,
            github,
            forgejo_owner="owner",
            github_owner="gh-owner",
            name="sample",
            description="A sample",
            private=True,
            sleeper=lambda _: None,
        )

        self.assertEqual(github.created, [])
        self.assertEqual(github.added, [])
        self.assertEqual(forgejo.created, [])
        self.assertEqual(result["github_repo"], "existing")
        self.assertEqual(result["deploy_key"], "existing")

    def test_visibility_collision_stops_without_overwriting(self):
        forgejo = FakeForgejoMirror([])
        github = FakeGitHubMirror(
            repo={
                "name": "sample",
                "full_name": "gh-owner/sample",
                "owner": {"login": "gh-owner"},
                "private": False,
                "description": "A sample",
                "permissions": {"admin": True},
            }
        )

        with self.assertRaisesRegex(SafetyError, "visibility collision"):
            ensure_github_mirror(
                forgejo,
                github,
                forgejo_owner="owner",
                github_owner="gh-owner",
                name="sample",
                description="A sample",
                private=True,
            )

        self.assertEqual(github.created, [])
        self.assertEqual(forgejo.created, [])

    def test_existing_key_title_with_different_key_stops(self):
        address = "git@github.com:gh-owner/sample.git"
        mirror = {
            "remote_address": address,
            "public_key": "ssh-ed25519 AAAAnew",
            "use_ssh": True,
            "sync_on_commit": True,
        }
        forgejo = FakeForgejoMirror([[mirror]])
        github = FakeGitHubMirror(
            repo={
                "name": "sample",
                "full_name": "gh-owner/sample",
                "owner": {"login": "gh-owner"},
                "private": True,
                "description": "A sample",
                "permissions": {"admin": True},
            },
            keys=[
                {
                    "title": "Forgejo mirror: owner/sample",
                    "key": "ssh-ed25519 AAAAold",
                    "read_only": False,
                }
            ],
        )

        with self.assertRaisesRegex(SafetyError, "deploy-key collision"):
            ensure_github_mirror(
                forgejo,
                github,
                forgejo_owner="owner",
                github_owner="gh-owner",
                name="sample",
                description="A sample",
                private=True,
            )

    def test_partial_failure_reports_mirror_error_and_recovery(self):
        address = "git@github.com:gh-owner/sample.git"
        key = "ssh-ed25519 AAAAtest"
        mirror = {
            "remote_address": address,
            "public_key": key,
            "use_ssh": True,
            "sync_on_commit": True,
            "last_error": "Permission denied (publickey)",
        }
        forgejo = FakeForgejoMirror([[mirror], [mirror], [mirror]])
        github = FakeGitHubMirror(
            repo={
                "name": "sample",
                "full_name": "gh-owner/sample",
                "owner": {"login": "gh-owner"},
                "private": True,
                "description": "A sample",
                "permissions": {"admin": True},
            },
            keys=[
                {
                    "title": "Forgejo mirror: owner/sample",
                    "key": key,
                    "read_only": False,
                }
            ],
        )

        with self.assertRaises(PartialFailure) as raised:
            ensure_github_mirror(
                forgejo,
                github,
                forgejo_owner="owner",
                github_owner="gh-owner",
                name="sample",
                description="A sample",
                private=True,
                verify_attempts=2,
                sleeper=lambda _: None,
            )

        self.assertIn("Permission denied", str(raised.exception))
        self.assertIn("rerun", str(raised.exception))

    def test_verification_rejects_status_that_omits_last_error(self):
        address = "git@github.com:gh-owner/sample.git"
        mirror = {
            "remote_address": address,
            "public_key": "ssh-ed25519 AAAAtest",
            "use_ssh": True,
            "sync_on_commit": True,
        }
        forgejo = FakeForgejoMirror([[], [mirror]], created=mirror)
        github = FakeGitHubMirror()

        with self.assertRaisesRegex(PartialFailure, "omitted last_error"):
            ensure_github_mirror(
                forgejo,
                github,
                forgejo_owner="owner",
                github_owner="gh-owner",
                name="sample",
                description="A sample",
                private=True,
                verify_attempts=1,
                sleeper=lambda _: None,
            )

    def test_github_repository_creation_then_mirror_failure_is_partial(self):
        forgejo = FailingMirrorCreationForgejo([[]])
        github = FakeGitHubMirror()

        with self.assertRaises(PartialFailure) as raised:
            ensure_github_mirror(
                forgejo,
                github,
                forgejo_owner="owner",
                github_owner="gh-owner",
                name="sample",
                description="A sample",
                private=True,
            )

        self.assertIn("GitHub repository", str(raised.exception))
        self.assertIn("retained", str(raised.exception))

    def test_existing_resources_actions_request_failure_is_partial(self):
        address = "git@github.com:gh-owner/sample.git"
        key = "ssh-ed25519 AAAAtest"
        mirror = {
            "remote_address": address,
            "public_key": key,
            "use_ssh": True,
            "sync_on_commit": True,
            "last_error": "",
        }
        forgejo = FakeForgejoMirror([[mirror]])
        github = FailingActionsGitHub(
            repo={
                "name": "sample",
                "full_name": "gh-owner/sample",
                "owner": {"login": "gh-owner"},
                "private": True,
                "description": "A sample",
                "permissions": {"admin": True},
            },
            keys=[
                {
                    "title": "Forgejo mirror: owner/sample",
                    "key": key,
                    "read_only": False,
                }
            ],
        )

        with self.assertRaisesRegex(PartialFailure, "ambiguous"):
            ensure_github_mirror(
                forgejo,
                github,
                forgejo_owner="owner",
                github_owner="gh-owner",
                name="sample",
                description="A sample",
                private=True,
            )

    def test_deploy_key_creation_then_actions_failure_is_partial(self):
        address = "git@github.com:gh-owner/sample.git"
        key = "ssh-ed25519 AAAAtest"
        mirror = {
            "remote_address": address,
            "public_key": key,
            "use_ssh": True,
            "sync_on_commit": True,
        }
        forgejo = FakeForgejoMirror([[mirror]])
        github = FailingActionsGitHub(
            repo={
                "name": "sample",
                "full_name": "gh-owner/sample",
                "owner": {"login": "gh-owner"},
                "private": True,
                "description": "A sample",
                "permissions": {"admin": True},
            }
        )

        with self.assertRaises(PartialFailure) as raised:
            ensure_github_mirror(
                forgejo,
                github,
                forgejo_owner="owner",
                github_owner="gh-owner",
                name="sample",
                description="A sample",
                private=True,
            )

        self.assertEqual(len(github.added), 1)
        self.assertIn("deploy key", str(raised.exception))
        self.assertIn("retained", str(raised.exception))


class FakeGitRunner:
    def __init__(self, remote, branch="main"):
        self.remote = remote
        self.branch = branch
        self.calls = []

    def __call__(self, args, cwd):
        self.calls.append((list(args), cwd))
        if args[:2] == ["git", "clone"]:
            Path(args[-1]).mkdir(parents=True)
            (Path(args[-1]) / ".git").mkdir()
            return GitResult(0, "", "")
        if "remote" in args and "get-url" in args:
            return GitResult(0, self.remote + "\n", "")
        if "branch" in args:
            return GitResult(0, self.branch + "\n", "")
        return GitResult(0, "", "")


class CloneTests(unittest.TestCase):
    def test_clones_from_configured_ssh_alias_and_verifies_origin_and_branch(self):
        remote = "ssh://git@forgejo-work/owner/sample.git"
        runner = FakeGitRunner(remote)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample"

            state = clone_and_verify(
                path,
                ssh_alias="forgejo-work",
                owner="owner",
                name="sample",
                default_branch="main",
                runner=runner,
            )

        self.assertEqual(state, "created")
        clone_args = runner.calls[0][0]
        self.assertEqual(
            clone_args, ["git", "clone", "--origin", "origin", remote, str(path)]
        )

    def test_existing_clone_with_wrong_origin_is_rejected(self):
        expected = "ssh://git@forgejo-work/owner/sample.git"
        runner = FakeGitRunner("ssh://git@forgejo-work/other/sample.git")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample"
            path.mkdir()
            (path / ".git").mkdir()

            with self.assertRaisesRegex(SafetyError, "origin collision"):
                clone_and_verify(
                    path,
                    ssh_alias="forgejo-work",
                    owner="owner",
                    name="sample",
                    default_branch="main",
                    runner=runner,
                )

        self.assertNotEqual(expected, runner.remote)


class FakeForgejoRepositories:
    def __init__(self, repo, *, created_repo=None):
        self.repo = repo
        self.created_repo = created_repo
        self.created = []

    def get_repo(self, owner, name):
        return self.repo

    def create_repo(self, *args, **kwargs):
        self.created.append((args, kwargs))
        return self.created_repo if self.created_repo is not None else self.repo


class FailingCreateForgejo(FakeForgejoRepositories):
    def create_repo(self, *args, **kwargs):
        self.created.append((args, kwargs))
        raise ApiError("connection lost after Forgejo create request")


class RecordingJournal:
    def __init__(self):
        self.records = []

    def record(self, stage, state, **kwargs):
        self.records.append((stage, state, kwargs))


class ManagerTests(unittest.TestCase):
    def test_forgejo_create_request_failure_is_partial_and_journaled_as_ambiguous(self):
        config = Config(
            forgejo_url="https://forgejo.example.test",
            forgejo_owner="owner",
            github_owner="gh-owner",
            projects_root=Path("/srv/projects"),
            ssh_alias="forgejo-work",
        )
        journal = RecordingJournal()
        manager = RepositoryManager(
            config,
            FailingCreateForgejo(None),
            github=None,
            git_runner=None,
            reporter=lambda _: None,
            journal=journal,
        )

        with self.assertRaisesRegex(PartialFailure, "create request"):
            manager.create(
                "sample",
                "A sample",
                private=True,
                with_github=False,
                dry_run=False,
            )

        self.assertEqual(journal.records[-1][0:2], ("forgejo_repo", "failed"))

    def test_created_forgejo_repo_verification_failure_is_partial_and_journaled(self):
        config = Config(
            forgejo_url="https://forgejo.example.test",
            forgejo_owner="owner",
            github_owner="gh-owner",
            projects_root=Path("/srv/projects"),
            ssh_alias="forgejo-work",
        )
        forgejo = FakeForgejoRepositories(
            None,
            created_repo={
                "name": "sample",
                "private": False,
                "description": "A sample",
                "default_branch": "main",
            },
        )
        journal = RecordingJournal()
        manager = RepositoryManager(
            config,
            forgejo,
            github=None,
            git_runner=None,
            reporter=lambda _: None,
            journal=journal,
        )

        with self.assertRaisesRegex(PartialFailure, "was created"):
            manager.create(
                "sample",
                "A sample",
                private=True,
                with_github=False,
                dry_run=False,
            )

        created_record = next(
            record
            for record in journal.records
            if record[0:2] == ("forgejo_repo", "created")
        )
        self.assertFalse(created_record[2]["preexisting"])

    def test_created_forgejo_repo_identity_mismatch_is_partial(self):
        config = Config(
            forgejo_url="https://forgejo.example.test",
            forgejo_owner="owner",
            github_owner="gh-owner",
            projects_root=Path("/srv/projects"),
            ssh_alias="forgejo-work",
        )
        forgejo = FakeForgejoRepositories(
            None,
            created_repo={
                "name": "sample",
                "full_name": "other/sample",
                "owner": {"login": "other"},
                "private": True,
                "description": "A sample",
                "default_branch": "main",
            },
        )
        manager = RepositoryManager(
            config,
            forgejo,
            github=None,
            git_runner=None,
            reporter=lambda _: None,
        )

        with self.assertRaisesRegex(PartialFailure, "identity collision"):
            manager.create(
                "sample",
                "A sample",
                private=True,
                with_github=False,
                dry_run=False,
            )

    def test_new_clone_left_after_verification_error_is_partial_and_journaled(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(
                forgejo_url="https://forgejo.example.test",
                forgejo_owner="owner",
                github_owner="gh-owner",
                projects_root=Path(directory),
                ssh_alias="forgejo-work",
            )
            forgejo = FakeForgejoRepositories(
                {
                    "name": "sample",
                    "full_name": "owner/sample",
                    "owner": {"login": "owner"},
                    "private": True,
                    "description": "A sample",
                    "default_branch": "main",
                }
            )
            journal = RecordingJournal()
            manager = RepositoryManager(
                config,
                forgejo,
                github=None,
                git_runner=FakeGitRunner(
                    "ssh://git@forgejo-work/owner/sample.git", branch="wrong"
                ),
                reporter=lambda _: None,
                journal=journal,
            )

            with self.assertRaisesRegex(PartialFailure, "clone verification failed"):
                manager.create(
                    "sample",
                    "A sample",
                    private=True,
                    with_github=False,
                    dry_run=False,
                )

            self.assertTrue((Path(directory) / "sample" / ".git").is_dir())
        self.assertEqual(journal.records[-1][0:2], ("clone", "failed"))

    def test_dry_run_reads_existing_metadata_without_writes(self):
        config = Config(
            forgejo_url="https://forgejo.example.test",
            forgejo_owner="owner",
            github_owner="gh-owner",
            projects_root=Path("/srv/projects"),
            ssh_alias="forgejo-work",
        )
        forgejo = FakeForgejoRepositories(
            {
                "name": "sample",
                "full_name": "owner/sample",
                "owner": {"login": "owner"},
                "private": True,
                "description": "A sample",
                "default_branch": "main",
            }
        )
        messages = []
        manager = RepositoryManager(
            config,
            forgejo,
            github=None,
            git_runner=None,
            reporter=messages.append,
        )

        result = manager.create(
            "sample",
            "A sample",
            private=True,
            with_github=False,
            dry_run=True,
        )

        self.assertEqual(forgejo.created, [])
        self.assertEqual(result["forgejo_repo"], "existing")
        self.assertTrue(any("DRY-RUN" in message for message in messages))
        self.assertTrue(any("private" in message for message in messages))


if __name__ == "__main__":
    unittest.main()
