import unittest
from pathlib import Path

from repo_bootstrap.config import Config
from repo_bootstrap.errors import ApiError
from repo_bootstrap.workflow import RepositoryManager


class DiscoveringForgejo:
    def __init__(self, pages, mirrors=None, errors=None):
        self.pages = pages
        self.mirrors = mirrors or {}
        self.errors = errors or {}
        self.page_calls = []

    def list_repos(self, *, page, limit):
        self.page_calls.append((page, limit))
        return self.pages.get(page, [])

    def list_push_mirrors(self, owner, name):
        full_name = f"{owner}/{name}"
        if full_name in self.errors:
            raise ApiError(self.errors[full_name])
        return self.mirrors.get(full_name, [])


class InspectingGitHub:
    def __init__(self, repos=None, keys=None, actions=None):
        self.repos = repos or {}
        self.keys = keys or {}
        self.actions = actions or {}
        self.writes = []

    def get_repo(self, owner, name):
        return self.repos.get(f"{owner}/{name}")

    def list_deploy_keys(self, owner, name):
        return self.keys.get(f"{owner}/{name}", [])

    def get_actions_permissions(self, owner, name):
        return {"enabled": self.actions.get(f"{owner}/{name}", False)}


class BatchTests(unittest.TestCase):
    def config(self):
        return Config(
            forgejo_url="https://forgejo.example.test",
            forgejo_owner="kosuiroom",
            github_owner="gh-owner",
            projects_root=Path("/srv/projects"),
            ssh_alias="forgejo-work",
            owner_map={"kosuiroom": "gh-owner"},
            skip_repositories=("kosuiroom/powerstep",),
        )

    def test_dry_run_discovers_pages_and_reports_missing_mirror(self):
        forgejo = DiscoveringForgejo(
            {
                1: [
                    {
                        "name": "alpha",
                        "full_name": "kosuiroom/alpha",
                        "owner": {"login": "kosuiroom"},
                        "description": "Alpha",
                        "private": True,
                        "permissions": {"admin": True},
                    }
                ],
                2: [
                    {
                        "name": "beta",
                        "full_name": "kosuiroom/beta",
                        "owner": {"login": "kosuiroom"},
                        "description": "Beta",
                        "private": True,
                        "permissions": {"admin": True},
                    }
                ],
                3: [],
            }
        )
        messages = []
        manager = RepositoryManager(
            self.config(),
            forgejo,
            github=InspectingGitHub(),
            git_runner=None,
            reporter=messages.append,
        )

        result = manager.batch(dry_run=True)

        self.assertEqual(result.examined, 2)
        self.assertEqual(result.would_change, 2)
        self.assertEqual(result.blockers, ())
        self.assertEqual(forgejo.page_calls, [(1, 50), (2, 50), (3, 50)])
        self.assertTrue(
            any("would create or complete private GitHub" in item for item in messages)
        )

    def test_incomplete_forgejo_identity_visibility_or_admin_metadata_is_blocked(self):
        forgejo = DiscoveringForgejo(
            {
                1: [
                    {
                        "name": "alpha",
                        "full_name": "kosuiroom/alpha",
                        "owner": {"login": "kosuiroom"},
                        "description": "Alpha",
                    }
                ],
                2: [],
            }
        )
        manager = RepositoryManager(
            self.config(),
            forgejo,
            github=InspectingGitHub(),
            git_runner=None,
            reporter=lambda _: None,
        )

        result = manager.batch(dry_run=True)

        self.assertEqual(result.would_change, 0)
        self.assertTrue(any("metadata" in item for item in result.blockers))

    def test_existing_clean_mirror_is_ready(self):
        address = "git@github.com:gh-owner/alpha.git"
        key = "ssh-ed25519 AAAAbatch"
        forgejo = DiscoveringForgejo(
            {
                1: [
                    {
                        "name": "alpha",
                        "full_name": "kosuiroom/alpha",
                        "owner": {"login": "kosuiroom"},
                        "description": "Alpha",
                        "private": True,
                        "permissions": {"admin": True},
                    }
                ],
                2: [],
            },
            mirrors={
                "kosuiroom/alpha": [
                    {
                        "remote_address": address,
                        "use_ssh": True,
                        "sync_on_commit": True,
                        "public_key": key,
                        "last_error": "",
                    }
                ]
            },
        )
        manager = RepositoryManager(
            self.config(),
            forgejo,
            github=InspectingGitHub(
                repos={
                    "gh-owner/alpha": {
                        "name": "alpha",
                        "full_name": "gh-owner/alpha",
                        "private": True,
                        "description": "Alpha",
                        "permissions": {"admin": True},
                    }
                },
                keys={
                    "gh-owner/alpha": [
                        {
                            "title": "Forgejo mirror: kosuiroom/alpha",
                            "key": key,
                            "read_only": False,
                        }
                    ]
                },
            ),
            git_runner=None,
            reporter=lambda _: None,
        )

        result = manager.batch(dry_run=True)

        self.assertEqual(result.ready, 1)
        self.assertEqual(result.would_change, 0)

    def test_unmapped_owner_and_permission_error_are_reported_as_blockers(self):
        forgejo = DiscoveringForgejo(
            {
                1: [
                    {
                        "name": "foreign",
                        "full_name": "pontus-local/foreign",
                        "owner": {"login": "pontus-local"},
                        "private": True,
                        "permissions": {"admin": True},
                    },
                    {
                        "name": "locked",
                        "full_name": "kosuiroom/locked",
                        "owner": {"login": "kosuiroom"},
                        "private": True,
                        "permissions": {"admin": True},
                    },
                ],
                2: [],
            },
            errors={"kosuiroom/locked": "HTTP 403 permission denied"},
        )
        manager = RepositoryManager(
            self.config(),
            forgejo,
            github=InspectingGitHub(),
            git_runner=None,
            reporter=lambda _: None,
        )

        result = manager.batch(dry_run=True)

        self.assertEqual(len(result.blockers), 2)
        self.assertTrue(
            any("no GitHub owner mapping" in item for item in result.blockers)
        )
        self.assertTrue(any("permission denied" in item for item in result.blockers))

    def test_forgejo_permission_blocker_is_reported_before_owner_mapping(self):
        forgejo = DiscoveringForgejo(
            {
                1: [
                    {
                        "name": "gaming-pc-deployc",
                        "full_name": "pontus-local/gaming-pc-deployc",
                        "owner": {"login": "pontus-local"},
                        "private": True,
                        "permissions": {"admin": True},
                    }
                ],
                2: [],
            },
            errors={"pontus-local/gaming-pc-deployc": "HTTP 403 permission denied"},
        )
        manager = RepositoryManager(
            self.config(),
            forgejo,
            github=InspectingGitHub(),
            git_runner=None,
            reporter=lambda _: None,
        )

        result = manager.batch(dry_run=True)

        self.assertEqual(len(result.blockers), 1)
        self.assertIn("permission denied", result.blockers[0])
        self.assertNotIn("owner mapping", result.blockers[0])

    def test_configured_repository_is_skipped_without_reading_or_altering_mirror(self):
        forgejo = DiscoveringForgejo(
            {
                1: [
                    {
                        "name": "powerstep",
                        "full_name": "kosuiroom/powerstep",
                        "owner": {"login": "kosuiroom"},
                    }
                ],
                2: [],
            },
            errors={"kosuiroom/powerstep": "must not be called"},
        )
        messages = []
        manager = RepositoryManager(
            self.config(),
            forgejo,
            github=InspectingGitHub(),
            git_runner=None,
            reporter=messages.append,
        )

        result = manager.batch(dry_run=True)

        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.blockers, ())
        self.assertTrue(any("configured skip" in item for item in messages))

    def test_incomplete_github_identity_or_admin_metadata_is_blocked(self):
        forgejo = DiscoveringForgejo(
            {
                1: [
                    {
                        "name": "alpha",
                        "full_name": "kosuiroom/alpha",
                        "owner": {"login": "kosuiroom"},
                        "private": True,
                        "description": "Alpha",
                        "permissions": {"admin": True},
                    }
                ],
                2: [],
            }
        )
        github = InspectingGitHub(
            repos={
                "gh-owner/alpha": {
                    "name": "alpha",
                    "private": True,
                    "description": "Alpha",
                }
            }
        )
        manager = RepositoryManager(
            self.config(),
            forgejo,
            github=github,
            git_runner=None,
            reporter=lambda _: None,
        )

        result = manager.batch(dry_run=True)

        self.assertEqual(result.would_change, 0)
        self.assertTrue(any("ownership collision" in item for item in result.blockers))
        self.assertEqual(github.writes, [])

    def test_dry_run_rejects_public_github_collision(self):
        forgejo = DiscoveringForgejo(
            {
                1: [
                    {
                        "name": "alpha",
                        "full_name": "kosuiroom/alpha",
                        "owner": {"login": "kosuiroom"},
                        "private": True,
                        "description": "Alpha",
                        "permissions": {"admin": True},
                    }
                ],
                2: [],
            }
        )
        github = InspectingGitHub(
            repos={
                "gh-owner/alpha": {
                    "name": "alpha",
                    "full_name": "gh-owner/alpha",
                    "private": False,
                    "description": "Alpha",
                    "permissions": {"admin": True},
                }
            }
        )
        manager = RepositoryManager(
            self.config(),
            forgejo,
            github=github,
            git_runner=None,
            reporter=lambda _: None,
        )

        result = manager.batch(dry_run=True)

        self.assertEqual(result.would_change, 0)
        self.assertTrue(any("visibility collision" in item for item in result.blockers))
        self.assertEqual(github.writes, [])

    def test_missing_use_ssh_or_last_error_is_not_reported_ready(self):
        address = "git@github.com:gh-owner/alpha.git"
        forgejo = DiscoveringForgejo(
            {
                1: [
                    {
                        "name": "alpha",
                        "full_name": "kosuiroom/alpha",
                        "owner": {"login": "kosuiroom"},
                        "private": True,
                        "description": "Alpha",
                        "permissions": {"admin": True},
                    }
                ],
                2: [],
            },
            mirrors={
                "kosuiroom/alpha": [
                    {
                        "remote_address": address,
                        "sync_on_commit": True,
                        "public_key": "ssh-ed25519 AAAAbatch",
                    }
                ]
            },
        )
        manager = RepositoryManager(
            self.config(),
            forgejo,
            github=InspectingGitHub(),
            git_runner=None,
            reporter=lambda _: None,
        )

        result = manager.batch(dry_run=True)

        self.assertEqual(result.ready, 0)
        self.assertTrue(any("use_ssh" in item for item in result.blockers))
        self.assertTrue(any("last_error" in item for item in result.blockers))

    def test_unmatched_explicit_repository_filter_is_a_blocker(self):
        forgejo = DiscoveringForgejo({1: [], 2: []})
        manager = RepositoryManager(
            self.config(),
            forgejo,
            github=InspectingGitHub(),
            git_runner=None,
            reporter=lambda _: None,
        )

        result = manager.batch(dry_run=True, repository_filters={"missing"})

        self.assertTrue(
            any("filter" in item and "missing" in item for item in result.blockers)
        )


if __name__ == "__main__":
    unittest.main()
