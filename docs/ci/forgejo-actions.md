# Forgejo Actions CI and SonarQube

Kosui Forge keeps language-specific CI local and delegates only SonarQube issue synchronization to the shared `pontus-local/kosui-workflows` repository pinned at commit `ddafef6593bffc454668cfcb0035faf886fccd3f`.

## Workflow entry points

`.forgejo/workflows/ci.yml` runs on:

- pull requests for fast review feedback;
- pushes to `main` for the protected default-branch gate;
- version tags matching `v*` to build releasable artifacts without auto-publishing them;
- `workflow_dispatch` for manual reruns.

The workflow intentionally does not publish a release. Tag builds stop at verifiable artifacts so human review still controls release notes, signing, and publication.

## Runner labels

The workflow currently uses the explicit `ubuntu-latest` label for both the local verification job and the shared Sonar issue-synchronization job. If the registered self-hosted runner uses a different label, update both reviewed `runner`/`runs-on` values in `.forgejo/workflows/ci.yml` together after verifying that the runner provides:

- Python 3.11 support through `actions/setup-python`;
- Git, curl, unzip, and Bash;
- network reachability to Forgejo and SonarQube.

The repository-local `scripts/ci/run-sonar.sh` currently downloads the published Linux x64 `sonar-scanner-cli` archive, so the Sonar-enabled `verify` path must stay on a Linux x64 runner until that script is intentionally generalized and re-reviewed.

No PySide6 smoke test is wired into CI yet because the repository does not ship a production desktop shell. Add that only after the desktop entry point and reviewed runner support both exist.

## Required secrets and permissions

Repository or organization Actions secrets:

- `SONAR_HOST_URL`: base SonarQube URL.
- `SONAR_TOKEN`: token with Execute Analysis and Browse access for the Kosui Forge project.

The workflow keeps top-level permissions at `contents: read`. Only the default-branch `sync_sonar_issues` job receives `issues: write`, and it passes the default run token to the shared synchronization workflow as `FORGEJO_TOKEN` so Sonar-managed issues can be created and updated without introducing an extra long-lived repository secret. Pull-request verification never runs repository code with issue-write scope.

## Required checks

The `verify` job is the main CI gate. It:

1. installs the project plus CI-only tooling;
2. runs `pytest` with branch coverage and JUnit XML output;
3. runs Ruff lint and formatting checks;
4. runs `mypy src`;
5. runs `python -m compileall -q src tests`;
6. builds wheel and sdist artifacts;
7. creates a fresh smoke-test virtualenv, installs the built wheel, and verifies `repo-bootstrap --version`, `repo-bootstrap --help`, and `repo-bootstrap doctor --help`;
8. runs `git diff --check`;
9. writes checksums and build metadata into the artifact bundle.

The workflow uploads `coverage.xml`, `test-results/`, `dist/`, and `ci-artifacts/` as a downloadable artifact for every run. Those local outputs are ignored in Git, and `./scripts/ci/verify.sh` cleans them automatically on exit outside Forgejo Actions unless `CI_KEEP_ARTIFACTS=1` is set.

## SonarQube flow

The workflow keeps SonarQube scanning local to this repository because the shared repository intentionally does not own language-specific build or test commands.

`verify` runs `scripts/ci/run-sonar.sh` only when either:

- the event is a push to `main`; or
- the event is `workflow_dispatch` on `main` with `run_sonar=true`.

That script downloads `sonar-scanner-cli` version `7.2.0.5079`, verifies its published SHA-256 checksum, and waits for the blocking quality gate.

After a successful default-branch scan, the `sync_sonar_issues` job calls the reviewed shared reusable workflow:

`pontus-local/kosui-workflows/.forgejo/workflows/sonar-issues.yml@ddafef6593bffc454668cfcb0035faf886fccd3f`

with:

- `project_key: ${{ env.SONAR_PROJECT_KEY }}` (defaults to `kosui-forge` in the workflow env block)
- `source_ref: ddafef6593bffc454668cfcb0035faf886fccd3f`
- `allow_unpinned_source: false`

Pull-request and tag builds do not synchronize Sonar issues because the shared workflow is for completed default-branch analysis only.

## Local equivalents

From the repository root:

```sh
python3 -m venv .venv
. .venv/bin/activate
./scripts/ci/verify.sh
```

To run the SonarQube scanner locally after the verification script succeeds:

```sh
export SONAR_HOST_URL=https://sonarqube.example.test
export SONAR_TOKEN=replace-me
export SONAR_PROJECT_KEY=kosui-forge
./scripts/ci/run-sonar.sh
```

## Troubleshooting

- `actions/setup-python` cannot satisfy Python 3.11: update both explicit runner labels to a reviewed runner that can, or preinstall Python 3.11 there.
- SonarQube validation fails before the scan starts: confirm `SONAR_HOST_URL` includes `http://` or `https://` and that `SONAR_TOKEN` is defined.
- The quality gate fails: download the run artifacts, inspect `coverage.xml` and `test-results/pytest.xml`, then review the SonarQube project dashboard for the failing condition.
- `sync_sonar_issues` fails while `verify` passed: confirm the run happened on `main`, the shared repository is still reachable at the pinned commit, and the `sync_sonar_issues` job still has `issues: write` permission.
- Tag builds should never publish automatically. If a release process is later added, keep it in a separate reviewed workflow with explicit human publication control.
