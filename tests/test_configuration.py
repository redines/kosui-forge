import json
import os
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from repo_bootstrap.config import Config, load_config, write_config
from repo_bootstrap.journal import StageJournal
from repo_bootstrap.paths import default_config_path, default_state_dir


class PlatformPathTests(unittest.TestCase):
    def test_linux_uses_xdg_directories(self):
        home = Path("/home/tester")
        environment = {
            "XDG_CONFIG_HOME": "/tmp/config",
            "XDG_STATE_HOME": "/tmp/state",
        }

        self.assertEqual(
            default_config_path("linux", environment, home),
            Path("/tmp/config/repo-bootstrap/config.toml"),
        )
        self.assertEqual(
            default_state_dir("linux", environment, home),
            Path("/tmp/state/repo-bootstrap"),
        )

    def test_macos_uses_application_support(self):
        home = Path("/Users/tester")

        self.assertEqual(
            default_config_path("darwin", {}, home),
            home / "Library/Application Support/repo-bootstrap/config.toml",
        )
        self.assertEqual(
            default_state_dir("darwin", {}, home),
            home / "Library/Application Support/repo-bootstrap/state",
        )

    def test_windows_uses_appdata_and_localappdata(self):
        environment = {
            "APPDATA": r"C:\Users\tester\AppData\Roaming",
            "LOCALAPPDATA": r"C:\Users\tester\AppData\Local",
        }
        home = Path(r"C:\Users\tester")

        self.assertEqual(
            default_config_path("win32", environment, home),
            Path(environment["APPDATA"]) / "repo-bootstrap/config.toml",
        )
        self.assertEqual(
            default_state_dir("win32", environment, home),
            Path(environment["LOCALAPPDATA"]) / "repo-bootstrap/state",
        )


class ConfigWriterTests(unittest.TestCase):
    def config(self, projects_root):
        return Config(
            forgejo_url="https://forgejo.example.test",
            forgejo_owner="forgejo-owner",
            github_owner="github-owner",
            projects_root=Path(projects_root),
            ssh_alias="forgejo-work",
            github_host="github.com",
            mirror_interval="8h",
            authentication_mode="per-repository-deploy-key",
        )

    def test_writes_only_non_secret_global_policy_with_restricted_permissions(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "config.toml"

            write_config(path, self.config(Path(directory) / "projects"))

            text = path.read_text(encoding="utf-8")
            mode = stat.S_IMODE(path.stat().st_mode)
            loaded = load_config(path)

        self.assertEqual(mode, 0o600)
        self.assertIn('privacy_policy = "private"', text)
        self.assertIn("sync_on_commit = true", text)
        self.assertIn('authentication_mode = "per-repository-deploy-key"', text)
        self.assertNotIn("TOKEN=", text.upper())
        self.assertNotIn("password", text.lower())
        self.assertEqual(loaded.authentication_mode, "per-repository-deploy-key")

    def test_refuses_to_overwrite_existing_config_without_force(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text("sentinel", encoding="utf-8")

            with self.assertRaisesRegex(FileExistsError, "already exists"):
                write_config(path, self.config(directory))

            self.assertEqual(path.read_text(encoding="utf-8"), "sentinel")

    def test_force_replaces_config_without_leaving_permissive_mode(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text("old", encoding="utf-8")
            os.chmod(path, 0o644)

            write_config(path, self.config(directory), force=True)

            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertNotEqual(path.read_text(encoding="utf-8"), "old")


class StageJournalTests(unittest.TestCase):
    def test_records_resume_safe_stage_state_without_secrets(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            journal = StageJournal(
                path,
                command="create",
                forgejo_repository="forgejo-owner/sample",
                github_repository="github-owner/sample",
            )

            journal.start(["create private Forgejo repository", "configure mirror"])
            journal.record("forgejo_repo", "created", preexisting=False)
            journal.record(
                "clone", "failed", detail="token=super-secret permission denied"
            )

            document = json.loads(path.read_text(encoding="utf-8"))
            mode = stat.S_IMODE(path.stat().st_mode)

        self.assertEqual(mode, 0o600)
        self.assertEqual(document["stages"]["forgejo_repo"]["state"], "created")
        self.assertFalse(document["stages"]["forgejo_repo"]["preexisting"])
        self.assertNotIn("super-secret", json.dumps(document))
        self.assertIn("<redacted>", json.dumps(document))
        self.assertIn("rerun", document["recovery"])
        self.assertIn("pre-existing", document["recovery"])

    def test_journal_file_name_is_sanitized_and_within_state_directory(self):
        with TemporaryDirectory() as directory:
            state_dir = Path(directory)

            path = StageJournal.path_for(state_dir, "forgejo-owner", "safe-repo")

        self.assertEqual(path.parent, state_dir)
        self.assertEqual(path.name, "forgejo-owner--safe-repo.json")


if __name__ == "__main__":
    unittest.main()
