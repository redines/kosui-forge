import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from repo_bootstrap.cli import build_parser, requested_visibility
from repo_bootstrap.config import Config, ConfigError, load_config
from repo_bootstrap.errors import SafetyError
from repo_bootstrap.validation import validate_repo_name


class PrivateDefaultTests(unittest.TestCase):
    def setUp(self):
        self.parser = build_parser()

    def test_create_defaults_to_private(self):
        args = self.parser.parse_args(["create", "sample", "--description", "A sample"])

        self.assertFalse(args.public)
        self.assertEqual(requested_visibility(args), "private")

    def test_public_is_only_an_intent_until_interactive_plan_confirmation(self):
        args = self.parser.parse_args(
            ["create", "sample", "--description", "A sample", "--public"]
        )

        self.assertEqual(requested_visibility(args), "public")

    def test_public_rejects_mismatched_confirmation(self):
        args = self.parser.parse_args(
            [
                "create",
                "sample",
                "--description",
                "A sample",
                "--public",
                "--confirm-public",
                "other",
            ]
        )

        with self.assertRaisesRegex(SafetyError, "does not match"):
            requested_visibility(args)

    def test_public_accepts_exact_confirmation(self):
        args = self.parser.parse_args(
            [
                "create",
                "sample",
                "--description",
                "A sample",
                "--public",
                "--confirm-public",
                "sample",
            ]
        )

        self.assertEqual(requested_visibility(args), "public")

    def test_confirmation_without_public_is_rejected(self):
        args = self.parser.parse_args(
            [
                "create",
                "sample",
                "--description",
                "A sample",
                "--confirm-public",
                "sample",
            ]
        )

        with self.assertRaisesRegex(SafetyError, "only valid with --public"):
            requested_visibility(args)


class ConfigurationTests(unittest.TestCase):
    def test_loads_non_secret_configuration(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                """
forgejo_url = "https://forgejo.example.test"
forgejo_owner = "forgejo-owner"
github_owner = "github-owner"
projects_root = "/srv/projects"
ssh_alias = "forgejo-work"
default_branch = "main"

[owner_map]
forgejo-owner = "github-owner"
""".strip(),
                encoding="utf-8",
            )

            config = load_config(path)

        self.assertEqual(config.forgejo_owner, "forgejo-owner")
        self.assertEqual(config.github_owner_for("forgejo-owner"), "github-owner")
        self.assertEqual(config.projects_root, Path("/srv/projects"))
        self.assertEqual(config.ssh_alias, "forgejo-work")

    def test_public_config_cannot_override_private_default(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                """
forgejo_url = "https://forgejo.example.test"
forgejo_owner = "forgejo-owner"
github_owner = "github-owner"
projects_root = "/srv/projects"
ssh_alias = "forgejo-work"
public = true
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConfigError, "unsupported.*public"):
                load_config(path)

    def test_config_rejects_embedded_credentials(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                """
forgejo_url = "https://user:password@forgejo.example.test"
forgejo_owner = "forgejo-owner"
github_owner = "github-owner"
projects_root = "/srv/projects"
ssh_alias = "forgejo-work"
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConfigError, "credentials"):
                load_config(path)


class RepositoryNameTests(unittest.TestCase):
    def test_accepts_safe_repository_names(self):
        for name in ("sample", "sample-repo", "sample_repo", "repo.example"):
            with self.subTest(name=name):
                self.assertEqual(validate_repo_name(name), name)

    def test_rejects_unsafe_repository_names(self):
        for name in ("", ".", "..", "-leading", "a/b", "has space", "name.git"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    validate_repo_name(name)


class ConfigDefaultsTests(unittest.TestCase):
    def test_dataclass_has_no_visibility_default(self):
        config = Config(
            forgejo_url="https://forgejo.example.test",
            forgejo_owner="forgejo-owner",
            github_owner="github-owner",
            projects_root=Path("/srv/projects"),
            ssh_alias="forgejo-work",
        )

        self.assertFalse(hasattr(config, "public"))
        self.assertFalse(hasattr(config, "private"))


if __name__ == "__main__":
    unittest.main()
