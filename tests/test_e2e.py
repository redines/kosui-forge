import json
import os
from pathlib import Path
import subprocess
import time
import unittest

from repo_bootstrap.cli import main
from repo_bootstrap.config import load_config


@unittest.skipUnless(
    os.environ.get("REPO_BOOTSTRAP_E2E") == "1",
    "set REPO_BOOTSTRAP_E2E=1 to run the disposable-repository E2E test",
)
class PrivateMirrorEndToEndTest(unittest.TestCase):
    def test_private_mirror_receives_branch_and_tag(self):
        config_path_text = os.environ.get("REPO_BOOTSTRAP_E2E_CONFIG")
        name = os.environ.get("REPO_BOOTSTRAP_E2E_REPO")
        self.assertTrue(config_path_text, "REPO_BOOTSTRAP_E2E_CONFIG is required")
        self.assertTrue(name, "REPO_BOOTSTRAP_E2E_REPO is required")
        self.assertTrue(
            name.startswith("repo-bootstrap-e2e-"),
            "E2E repository name must start with repo-bootstrap-e2e-",
        )
        config_path = Path(config_path_text)
        config = load_config(config_path)
        self.github_host = config.github_host
        github_owner = config.github_owner_for(config.forgejo_owner)
        self.assertIsNotNone(github_owner)

        code = main(
            [
                "--config",
                str(config_path),
                "create",
                name,
                "--description",
                "Disposable repo-bootstrap private mirror E2E",
                "--github",
                "--yes",
            ]
        )
        self.assertEqual(code, 0)

        github_repo = self._gh_json(f"/repos/{github_owner}/{name}")
        self.assertIs(github_repo["private"], True)
        actions = self._gh_json(f"/repos/{github_owner}/{name}/actions/permissions")
        self.assertIs(actions["enabled"], False)

        clone = config.projects_root / name
        suffix = str(int(time.time()))
        branch = f"e2e-branch-{suffix}"
        tag = f"e2e-tag-{suffix}"
        marker = clone / f"{suffix}.txt"
        marker.write_text("repo-bootstrap mirror E2E\n", encoding="utf-8")
        self._git(clone, "add", marker.name)
        self._git(clone, "commit", "-m", f"test: add mirror marker {suffix}")
        self._git(clone, "branch", branch)
        self._git(clone, "tag", tag)
        local_sha = self._git(clone, "rev-parse", "HEAD").strip()
        self._git(
            clone,
            "push",
            "origin",
            f"HEAD:refs/heads/{branch}",
            f"refs/tags/{tag}",
        )

        branch_sha = self._wait_for_ref(github_owner, name, f"heads/{branch}")
        tag_sha = self._wait_for_ref(github_owner, name, f"tags/{tag}")
        self.assertEqual(branch_sha, local_sha)
        self.assertEqual(tag_sha, local_sha)

        print(
            "E2E resources retained for explicit inspection. Cleanup is never automatic; "
            "follow the README checklist only after approval."
        )

    def _wait_for_ref(self, owner, name, ref):
        deadline = time.monotonic() + 60
        last_error = None
        while time.monotonic() < deadline:
            result = subprocess.run(
                [
                    "gh",
                    "api",
                    "--hostname",
                    self.github_host,
                    f"/repos/{owner}/{name}/git/ref/{ref}",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode == 0:
                return json.loads(result.stdout)["object"]["sha"]
            last_error = result.stderr.strip()
            time.sleep(2)
        self.fail(f"GitHub ref {ref} did not arrive: {last_error}")

    @staticmethod
    def _git(cwd, *args):
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=True,
        ).stdout

    def _gh(self, *args):
        return subprocess.run(
            ["gh", "api", "--hostname", self.github_host, *args],
            text=True,
            capture_output=True,
            check=True,
        ).stdout

    def _gh_json(self, endpoint):
        return json.loads(self._gh(endpoint))


if __name__ == "__main__":
    unittest.main()
