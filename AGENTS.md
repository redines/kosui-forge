# Kosui Forge Agent Instructions

These instructions apply to the entire repository. A more deeply nested `AGENTS.md` may add stricter local rules, but it must not weaken the safety, dependency, testing, or review requirements here.

## Product mission

Kosui Forge is a cross-platform, GitHub Desktop-like application for local Git repositories, Forgejo, and GitHub. Forgejo is a first-class provider rather than a generic remote.

The product should let users:

- discover and manage local, Forgejo, and GitHub repositories in one catalog;
- create a repository on Forgejo only, GitHub only, or Forgejo with a GitHub push mirror;
- keep Forgejo as the source of truth for dual-provider repositories;
- inspect local changes, history, branches, ahead/behind state, and mirror health;
- perform reviewed Git and repository operations through a safe desktop interface;
- retain a headless compatibility CLI for automation and recovery.

The public Forgejo repository is the source of truth. A GitHub push mirror may be configured later and must never block local or Forgejo development.

## AI-generation disclosure

Kosui Forge is a fully AI-generated project under human direction. Do not remove, weaken, hide, or relocate the prominent README disclosure without explicit maintainer approval.

AI agents generate source code, tests, documentation, and build automation. Humans provide requirements, product decisions, review, and merge approval. Never auto-merge a pull request or imply that AI-generated output has received human review when it has not.

## Architecture: dependency rule

Use Clean Architecture with inward-only dependencies. Business and safety policy must not depend on Qt, HTTP SDKs, subprocesses, keyrings, filesystems, or presentation formatting.

Target boundaries:

```text
src/kosui_forge/
  domain/             entities, value objects, provider-neutral policy
  application/        use cases, typed requests/events/results, orchestration
  ports/              Protocol/ABC interfaces required by application use cases
  adapters/
    forgejo/           pyforgejo implementation
    github/            GitHubKit implementation
    git/               fixed-argument Git implementation
    credentials/       OS-keychain implementation
    persistence/       settings/catalog/cache/journal implementation
  presentation/
    desktop/           PySide6 views, view models/presenters, Qt workers
    cli/               argparse/text adapter and compatibility commands
  infrastructure/      composition roots, platform discovery, packaging glue
```

The exact number of files may evolve, but these dependency directions are mandatory:

1. `domain` uses only the Python standard library and imports no outer layer.
2. `application` depends on `domain` and `ports`, never concrete adapters or Qt.
3. `ports` expose the smallest interfaces required by real use cases; do not add speculative hooks.
4. `adapters` implement ports and may depend inward, never on presentation.
5. `presentation` invokes application use cases and renders typed events/results. It must not duplicate repository, privacy, collision, mirror, credential, or journal policy.
6. `infrastructure` is the composition root that selects concrete adapters. Business decisions do not live there.
7. Cross-layer convenience imports, global service locators, and circular dependencies are prohibited.

Prefer immutable dataclasses, enums, and narrow protocols at boundaries. Return structured results rather than prose that another layer must parse.

## Incremental migration

The existing `repo_bootstrap` package is a tested compatibility implementation. Refactor it by vertical slice; do not perform a big-bang rewrite.

For each migrated use case:

1. characterize current behavior with tests;
2. extract provider-neutral rules into `domain`/`application`;
3. define only the ports required by that behavior;
4. adapt the existing Forgejo, GitHub, Git, configuration, and journal code;
5. make the compatibility CLI call the same application use case;
6. verify output, exit codes, safety gates, and journals remain compatible;
7. delete old logic only after all callers use the new boundary and tests prove parity.

Compatibility shims are acceptable when explicit, tested, and scheduled for removal. Parallel implementations of safety policy are not acceptable.

## Non-negotiable safety invariants

- Repository creation is private by default on every provider.
- Public creation requires an explicit public option and layered acknowledgement. Non-interactive public creation requires exact-name confirmation.
- `doctor` and `--dry-run` perform no writes.
- Preflight completes immediately before any mutation.
- Existing repositories, directories, remotes, deploy keys, and mirrors are never deleted, replaced, overwritten, or silently reused.
- Ambiguous collisions fail closed and produce actionable, redacted diagnostics.
- Forgejo remains the source of truth for Forgejo-to-GitHub push mirrors.
- Mirror authentication uses a unique, repository-scoped writable GitHub deploy key.
- Git mirrors are not described as full Forgejo backups.
- Partial writes retain durable journals and links to created resources; do not invent automatic destructive rollback.
- Cancellation is honored only at explicit safe boundaries. Never kill a thread or process during an ambiguous remote mutation.
- No arbitrary shell or terminal input is exposed by the GUI.
- Start external programs with an executable and argument array. Never use `shell=True` or interpolate free-form values into shell commands.

## Credentials and privacy

- Tokens, passwords, private keys, authenticated URLs, and authorization headers must not appear in repository files, command arguments, logs, journals, crash reports, fixtures, screenshots, or analytics.
- GUI credentials belong in an approved OS credential backend: Windows Credential Manager, macOS Keychain, or Linux Secret Service.
- GUI mode fails closed if only a plaintext, null, or insecure keyring backend is available.
- Protected environment variables may remain available for headless automation.
- `gh` may be used only through reviewed fixed commands as an explicit compatibility/login path. Core GitHub API operations use GitHubKit.
- Forgejo API operations use pyforgejo. Any unsupported-version adapter must be narrow, documented, fail closed, and tested against the supported Forgejo instance/schema.
- There is no outbound telemetry unless a future user-facing opt-in is designed and approved.
- Redact at the service boundary and again before presentation or persistence.

## Desktop and concurrency rules

- Qt widgets are touched only on the GUI thread.
- Run application work through `QThread`, `QThreadPool`, or a reviewed asynchronous boundary.
- Use `QProcess` only for fixed-argument external tools and capture bounded, redacted output.
- Every long operation emits typed progress and reaches a structured terminal result.
- Read-only work may cancel between checks. Mutating work stops only at journaled safe points.
- Closing the application must not orphan child processes, corrupt settings, or lose recovery information.
- Support keyboard navigation, accessibility names, high-DPI displays, system light/dark palettes, paths containing spaces, Unicode paths, and platform-native path rules.
- Keep network and subprocess calls bounded by timeouts. The interface must remain responsive while they run.

## Cross-platform and packaging rules

- Shared source must support Windows, Linux, and macOS.
- Build, sign, and smoke-test release artifacts natively on each target operating system.
- A Linux build does not prove Windows or macOS support.
- Linux release builds must use the oldest supported glibc baseline and pass clean X11/Wayland tests with no missing delayed-load libraries.
- Windows executables/installers require signature verification and install/upgrade/uninstall tests before public release.
- macOS applications require hardened-runtime signing, notarization, stapling, and Gatekeeper verification before public release.
- Do not bundle Git, OpenSSH, or `gh` without an approved provenance, update, CVE-response, SBOM, notice, and license plan.
- Generate checksums, dependency inventory, SBOM/provenance, and required license notices from final signed artifacts.
- Do not claim release portability based only on a developer-machine proof.

## Testing and verification

Every behavior change needs focused tests. Prefer invariant and relationship assertions over snapshots of incidental values.

Minimum verification for Python changes, as applicable:

```sh
python -m pytest -q
python -m ruff check src tests
python -m ruff format --check src tests
python -m mypy src
python -m compileall -q src tests
python -m build
```

Also verify:

- clean wheel installation in a fresh temporary environment;
- `repo-bootstrap --version` and compatibility help while that command exists;
- the `kosui-forge` desktop entry point once introduced;
- `git diff --check` and clean tracked worktree status;
- no credential-shaped additions or generated build artifacts;
- Qt tests with `QT_QPA_PLATFORM=offscreen` where a display is unnecessary;
- real temporary filesystem/subprocess behavior for security and platform boundaries, not only mocks;
- Forgejo-specific behavior against Forgejo 11.0.16 fixtures/schema until the supported-version policy changes through review.

Authenticated or mutating E2E tests require an explicit disposable-environment gate. Never create or delete real repositories merely to make a routine unit test pass.

## Git and review workflow

- Work on an isolated task branch/worktree based on current `origin/main`.
- Keep each task and pull request narrowly scoped and independently reviewable.
- Use Conventional Commits 1.0.0 and SemVer 2.0.0.
- Run focused checks during development and the required final matrix before handoff.
- Push every meaningful completed checkpoint.
- Open a Forgejo pull request and record commit, tests, risks, and exact next step in the Kanban handoff.
- Leave manual-review cards unassigned in Review.
- Never merge automatically.
- Do not force-push a reviewed branch unless resolving an explicit review requirement and preserving the existing PR is intended.
- After merge, verify the feature commit is reachable from `origin/main` before removing branches or worktrees.

## Documentation expectations

- Keep README usage and architecture statements consistent with executable behavior.
- Record material architecture decisions under `docs/architecture/`.
- Explain security trade-offs and compatibility limits honestly; do not convert plans or mocked tests into claims of production support.
- Keep the fully AI-generated disclosure prominent.
- Use Kosui Forge naming for the product. Refer to `repo-bootstrap` only for the compatibility CLI/package during migration.
- Public documentation and fixtures must contain no private hostnames, credentials, personal paths, or ambiguous secret-like examples.

When requirements conflict, preserve security, user data, reversibility, and human review first; surface the conflict instead of silently weakening an invariant.
