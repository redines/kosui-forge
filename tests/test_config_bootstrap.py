from dataclasses import replace
from pathlib import Path
import stat
import tempfile
import unittest

from repo_bootstrap.config import (
    Config,
    ConfigError,
    default_config_path,
    load_config,
    validate_config,
    write_config,
)


class PlatformConfigPathTests(unittest.TestCase):
    def test_linux_uses_xdg_config_home(self):
        path = default_config_path(
            system="Linux",
            environ={"XDG_CONFIG_HOME": "/tmp/xdg"},
            home=Path("/home/test"),
        )
        self.assertEqual(path, Path("/tmp/xdg/repo-bootstrap/config.toml"))

    def test_macos_uses_application_support(self):
        path = default_config_path(
            system="Darwin", environ={}, home=Path("/Users/test")
        )
        self.assertEqual(
            path,
            Path("/Users/test/Library/Application Support/repo-bootstrap/config.toml"),
        )

    def test_windows_uses_appdata(self):
        path = default_config_path(
            system="Windows",
            environ={"APPDATA": r"C:\Users\test\AppData\Roaming"},
            home=Path(r"C:\Users\test"),
        )
        self.assertEqual(
            path,
            Path(r"C:\Users\test\AppData\Roaming") / "repo-bootstrap/config.toml",
        )


class ConfigurationBootstrapTests(unittest.TestCase):
    def test_rejects_unsafe_identifiers_that_would_reach_command_arguments(self):
        base = Config(
            forgejo_url="https://forgejo.example.test",
            forgejo_owner="forgejo-owner",
            github_owner="github-owner",
            github_host="github.com",
            projects_root=Path("/srv/projects"),
            ssh_alias="forgejo-work",
        )
        unsafe = (
            ("github_host", "user:password@github.com"),
            ("ssh_alias", "-oProxyCommand=unsafe"),
            ("forgejo_token_env", "FORGEJO_TOKEN=unsafe"),
            ("forgejo_owner", "../owner"),
            ("github_owner", "owner/repository"),
        )

        for field, value in unsafe:
            with self.subTest(field=field):
                with self.assertRaises(ConfigError):
                    validate_config(replace(base, **{field: value}))

    def test_writes_non_secret_config_with_owner_policy_and_safe_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "config.toml"
            write_config(
                path,
                forgejo_url="https://forgejo.example.test",
                forgejo_owner="forgejo-owner",
                github_owner="github-owner",
                github_host="github.com",
                projects_root=Path(directory) / "projects",
                ssh_alias="forgejo-work",
                mirror_interval="8h0m0s",
            )
            text = path.read_text(encoding="utf-8")
            mode = stat.S_IMODE(path.stat().st_mode)
            config = load_config(path)

        self.assertEqual(mode, 0o600)
        self.assertIn('authentication_mode = "per-repository-deploy-key"', text)
        self.assertIn("sync_on_commit = true", text)
        self.assertIn('forgejo_token_env = "FORGEJO_TOKEN"', text)
        self.assertNotIn("synthetic-secret-value", text)
        self.assertNotIn("password", text.lower())
        self.assertEqual(config.github_host, "github.com")
        self.assertTrue(config.sync_on_commit)
        self.assertEqual(config.mirror_interval, "8h0m0s")

    def test_refuses_to_overwrite_existing_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text("existing", encoding="utf-8")
            with self.assertRaisesRegex(FileExistsError, "already exists"):
                write_config(
                    path,
                    forgejo_url="https://forgejo.example.test",
                    forgejo_owner="forgejo-owner",
                    github_owner="github-owner",
                    github_host="github.com",
                    projects_root=Path(directory),
                    ssh_alias="forgejo-work",
                    mirror_interval="8h0m0s",
                )

    def test_rejects_plaintext_http(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                """
forgejo_url = "http://forgejo.example.test"
forgejo_owner = "owner"
github_owner = "gh-owner"
projects_root = "/srv/projects"
ssh_alias = "forgejo-work"
""".strip(),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ConfigError, "HTTPS"):
                load_config(path)

    def test_rejects_forgejo_url_query_or_fragment(self):
        base = Config(
            forgejo_url="https://forgejo.example.test",
            forgejo_owner="owner",
            github_owner="gh-owner",
            projects_root=Path("/srv/projects"),
            ssh_alias="forgejo-work",
        )

        for url in (
            "https://forgejo.example.test?token=unsafe",
            "https://forgejo.example.test/#unsafe",
        ):
            with self.subTest(url=url), self.assertRaises(ConfigError):
                validate_config(replace(base, forgejo_url=url))

    def test_rejects_unsafe_authentication_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                """
forgejo_url = "https://forgejo.example.test"
forgejo_owner = "owner"
github_owner = "gh-owner"
projects_root = "/srv/projects"
ssh_alias = "forgejo-work"
authentication_mode = "shared-token"
""".strip(),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ConfigError, "deploy-key"):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
