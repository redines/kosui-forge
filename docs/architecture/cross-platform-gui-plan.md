# Kosui Forge cross-platform GUI architecture and packaging proof

<!-- markdownlint-disable MD013 -->

- Status: architecture spike, not a production GUI
- Measured: 2026-07-21 on Linux x86-64
- Decision owner: Kosui Forge maintainer; licensing conclusions require review
- Source spike: `repo-tooling/docs/cross-platform-gui-plan.md` at commit `c8ca550c7f18587d02b5a9b720ca743daa73ffb2`
- Import note: quantitative evidence and findings are preserved; product names, repository-root paths, and target artifact names are mapped to Kosui Forge
- Exclusion: helper-tools PR #5 / commit `27e9f3e` was not imported because its launcher failed review (Windows PID probing, stale-lock race, PowerShell exit propagation, cache fallback, and missing-venv behavior)

## Decision

Proceed with a PySide6 GUI from the existing Python source tree. Keep the existing `repo-bootstrap` headless compatibility CLI as a first-class interface. A desktop launcher must be designed and reviewed independently; no source-spike launcher implementation is part of Kosui Forge. Do not introduce Tauri or Electron now.

The proof found no concrete PySide6 blocker. The official `pyside6-deploy` wrapper produced both a Linux one-file executable and a standalone directory with Nuitka. The packaged GUI remained responsive while Doctor ran, streamed redacted JSON events into a log and table, returned a structured result, and honored a process cancellation boundary. It imported and called `repo_bootstrap.preflight.run_preflight`; it did not copy Doctor rules.

The proof is **not a release candidate**. Its unpruned Qt plugin set creates a 146 MB standalone directory, resolves 112 shared libraries from the build host, and contains one dormant plugin dependency that `ldd` could not resolve (`libtiff.so.5`). It was built on glibc 2.43 and requires a symbol as new as GLIBC 2.38. Release builds therefore remain no-go until they are made on an older Linux baseline, the plugin allowlist is reduced, and clean-machine tests report no missing dependencies.

## Scope and invariants

The GUI is another adapter over the same `kosui_forge` application service, not a replacement for the compatibility CLI.

- Preserve `repo-bootstrap` CLI arguments, exit codes, private-by-default policy, dry-run behavior, journals, and redaction. Any future launcher is a separate, safety-reviewed adapter.
- Keep GitHubKit and pyforgejo as the API clients. `gh` is initially a login/token compatibility adapter, not an arbitrary command console or API implementation.
- Never construct a shell command. External tools are started as a program plus an argument array.
- Keep tokens out of command arguments, config, process logs, journals, URLs, crash reports, and analytics.
- Never hard-kill an operation after an ambiguous remote mutation. Cancellation becomes effective only at an explicitly safe boundary, with the recovery journal retained.
- Package and sign independently on each target operating system. A successful Linux build does not prove Windows or macOS packaging.

## Disposable proof

### What was built

The historical disposable source and build tree lived outside Git at `/tmp/repo-bootstrap-gui-spike`. That path and the historical probe artifact names below identify the measured source spike; they are evidence only, not the current Kosui Forge repository layout. No executable, virtual environment, generated C, Nuitka directory, or `pysidedeploy.spec` is imported.

The 12.2 KB spike:

1. imports `Config`, `run_preflight`, `ToolResult`, and `redact` from the installed `repo-bootstrap` package;
2. runs the existing `run_preflight` service in a child instance of the same executable;
3. starts that child with `QProcess.start(program, argument_list)`, never a shell string;
4. reads newline-delimited JSON from `readyReadStandardOutput` without blocking the Qt event loop;
5. renders each `CheckResult` into a three-column table and a redacted log;
6. exposes Cancel, first calling `terminate()` and then `kill()` after a grace period;
7. reports a structured terminal event with state, exit code, row count, and elapsed time.

The probe adapters are deterministic read-only fakes for Forgejo, GitHub, and tool-version calls. They isolate GUI scheduling and packaging from credentials and network availability. The Doctor rules themselves are real: one call to `run_preflight` generated all 20 checks. Production code must replace the probe adapters with the existing `ForgejoClient`, `GitHubClient`, and command adapter rather than retain a demo mode.

The central shell-free boundary was:

```python
worker_args = ["--doctor-worker", "--stream-delay-ms", "80"]
process.start(worker_program, worker_args, QProcess.ReadOnly)
```

The worker called the core once and serialized its existing result:

```python
report = run_preflight(
    config,
    forgejo,
    github,
    token_present=True,
    name="sample",
    description="GUI architecture probe",
    private=True,
    with_github=True,
    command_runner=command_adapter,
)
```

### Commands actually run

The source-spike environment was created outside its worktree. The then-extracted subtree install maps to `-e .` at the Kosui Forge repository root; the measured dependency versions and commands are otherwise preserved:

```sh
python3 -m venv /tmp/repo-bootstrap-gui-spike/.venv
/tmp/repo-bootstrap-gui-spike/.venv/bin/python -m pip install \
  -e . PySide6 Nuitka ordered-set zstandard
```

The official wrapper generated the one-file executable:

```sh
cd /tmp/repo-bootstrap-gui-spike
.venv/bin/pyside6-deploy main.py \
  --name repo-bootstrap-gui-probe \
  --mode onefile \
  --nuitka-version 4.1.3 \
  --keep-deployment-files \
  -f -v
```

It generated the standalone directory with the same command except:

```sh
--mode standalone
```

Headless execution exercised the built artifacts:

```sh
export QT_QPA_PLATFORM=offscreen
./repo-bootstrap-gui-probe.bin --startup-probe
./repo-bootstrap-gui-probe.bin --auto-doctor --exit-after-result
./repo-bootstrap-gui-probe.bin --auto-doctor --cancel-after-ms 500 --exit-after-result

./repo-bootstrap-gui-probe.dist/main.bin --startup-probe
./repo-bootstrap-gui-probe.dist/main.bin --auto-doctor --exit-after-result
./repo-bootstrap-gui-probe.dist/main.bin \
  --auto-doctor --cancel-after-ms 180 --exit-after-result
```

### Measured evidence

Host: Linux `7.0.0-27-generic`, x86-64; Python 3.11.15; glibc 2.43. Build inputs resolved to PySide6 6.11.1, Nuitka 4.1.3, GitHubKit 0.16.0, and pyforgejo 2.0.7.

| Measurement | Actual result |
| --- | ---: |
| One-file ELF size | 40,974,544 bytes (39.08 MiB) |
| One-file SHA-256 | `bf60bf5b6cf13ba2b02a3db4d6b8134eb5fe05bf2a3d804ffcc0ca3e0fc7ae8b` |
| Standalone directory size | 146,093,230 bytes (139.33 MiB) |
| Standalone executable size | 7,113,841 bytes (6.78 MiB) |
| Standalone executable SHA-256 | `943c954ae34ec08e68da732c2c2313dcf08e6c50efcd77722c82240b71236953` |
| Standalone files | 78 |
| Bundled shared objects | 77 |
| Qt plugin files | 46 |
| One-file cold-start proxy, wall clock | 680.01 ms |
| One-file warm start, wall-clock median of 7 | 549.85 ms (534.07–611.21 ms) |
| One-file warm event-loop median after unpack/bootstrap | 1.53 ms |
| Packaged one-file Doctor | 20 rows, normal exit 0, 2,142.69 ms |
| Packaged one-file cancellation | cancelled, crash exit 9, 12 rows, 1,417.41 ms |
| Packaged standalone Doctor | 20 rows, normal exit 0, 1,747.44 ms |
| Packaged standalone cancellation | cancelled, crash exit 15, 1 row, 166.64 ms |
| Maximum observed GLIBC symbol requirement | GLIBC 2.38, from `main.bin` |

The cold proxy used `POSIX_FADV_DONTNEED` on the executable before the first run. The process had no privilege to clear the machine-wide page cache, so this is repeatable evidence rather than a laboratory cold-boot claim. Wall clock includes one-file extraction; the event-loop timer begins only after the compiled Python code starts.

The cancellation exit is reported as a crash by `QProcess` because POSIX `terminate()` sends SIGTERM. The controller deliberately maps a user-requested termination to `state="cancelled"`, not to an application failure. The production service must prefer cooperative stage-boundary cancellation; see below.

### Dependency inventory findings

`ldd` on the one-file bootloader resolved only glibc. `ldd` on the standalone `main.bin` resolved bundled `libpython3.11.so.1.0` and host `libm`, `libc`, `libdl`, `librt`, `libpthread`, and `libutil`. Scanning the executable and all 77 bundled shared objects found:

- bundled CPython, PySide6/Shiboken, Qt Core/DBus/GUI/Widgets, and unexpectedly broad Qt Network, QML, Quick, Virtual Keyboard, PDF, SVG, Wayland, XCB, ICU, image-format, print, theme, and input plugins;
- 112 libraries resolved from the build host, including X11/XCB, Wayland, GL/EGL, GTK 3, accessibility, CUPS, font, DBus, Kerberos, and TLS support;
- one missing optional dependency, `libtiff.so.5`, pulled by an image plugin even though the exercised path did not load it;
- no `gh` in the build host, while `/usr/bin/git` was 4,547,768 bytes and `/usr/bin/ssh` was 924,800 bytes. The artifact did not bundle any of them.

This inventory proves that “one file” is not equivalent to “portable to every Linux desktop.” The release pipeline must inventory delayed-load plugins, test X11 and Wayland on clean images, and build on the oldest supported glibc baseline. The official Nuitka guidance likewise recommends compiling Linux standalone applications on the oldest Linux version to be supported.

## Proposed application boundary

Kosui Forge introduces UI-neutral contracts and use cases under `src/kosui_forge/application`, narrow protocols under `src/kosui_forge/ports`, the compatibility preflight bridge under `src/kosui_forge/adapters`, and concrete wiring under `src/kosui_forge/infrastructure`. The compatibility CLI and future GUI depend inward on the same tested policy core:

```text
PySide6 widgets       argparse CLI       future reviewed launcher
       |                    |                     |
       +---------- application service ----------+
                    | typed requests/events/results
       +------------+-------------+--------------+
       |                          |               |
  preflight/core          repository workflow   stage journal
       |                          |
  GitHubKit adapter       pyforgejo adapter      Git adapter
       |                          |               |
 GitHub REST API            Forgejo API      QProcess/subprocess argv
```

The foundation implements only contracts consumed by the real Doctor adapter, avoiding speculative workflow hierarchies:

- `DoctorRequest(config_path, repository_name, description, include_github)` with a generated correlation ID
- immutable `OperationEvent(operation_id, sequence, stage, state, message, completed, total, check)`
- immutable `OperationResult(status, checks, links, recovery, cancellation_state)`
- typed `DoctorCheck`, `ResourceLink`, and `RecoveryInfo` values
- a thread-safe, monotonic `CancellationToken` checked by read-only preflight at completed-check boundaries

Create and mirror request shapes remain deferred until a real adapter consumes them. Mutating services must later check cancellation only at journaled safe points, never while a remote mutation is ambiguous.

The application service owns use-case orchestration and result construction through narrow ports. Concrete configuration, SDK, and preflight calls remain in adapters/infrastructure during the incremental migration; tested safety policy remains in the compatibility core until a vertical slice moves it inward. The GUI owns widgets, navigation, clipboard/browser integration, and presentation only. The CLI remains responsible for terminal parsing and text rendering only. Shared policy must never be reimplemented in signal handlers.

### Execution and cancellation model

Use an in-process worker object moved to `QThread` or `QThreadPool` for ordinary service calls. Emit typed Qt signals carrying immutable events. Never touch a widget from a worker thread.

Use `QProcess` only for required system tools and, if later justified, an isolated application worker. Always pass an executable and argument list. Resolve the executable once, display its absolute path/version for review, apply a timeout, set a minimal environment, and redact stdout/stderr before persistence. Do not expose a free-form command field.

Cancellation rules:

1. A user request immediately changes the UI to “cancellation requested.”
2. Read-only Doctor may stop between checks. A currently executing SDK request may finish or hit its bounded timeout; Python threads must not be killed.
3. A mutating workflow checks the token only at journaled safe points. It must not be interrupted between sending a create request and recording/inspecting its ambiguous result.
4. If cancellation follows a durable mutation, return `cancelled-with-recovery`, retain the journal, and link to created resources.
5. Hard process termination is reserved for a read-only worker or a wedged child known not to be inside a mutation. Otherwise offer “stop after current stage.”

The foundation adds optional `reporter` and `cancellation_requested` hooks to `run_preflight` without changing default CLI output or policy. Each completed `CheckResult` is emitted in order; cancellation returns a partial, explicitly cancelled report at that read-only boundary. A currently executing bounded SDK or command call still finishes before cancellation is honored.

## UX flow

1. **Welcome and setup wizard**
   - Find or create the non-secret config.
   - Detect Git, OpenSSH, and optional `gh`; show exact resolved path and version.
   - Select projects root, Forgejo URL/owner, GitHub owner/host, and SSH alias.
   - Store credentials only through the credential abstraction.
2. **Doctor checklist**
   - Run all existing read-only preflight checks off the UI thread.
   - Show Pass/Fail/Blocked, actionable guidance, elapsed time, and a copy-safe redacted report.
3. **Create repository**
   - Collect name and description; private remains the default.
   - Present a dry-run plan before enabling Apply.
   - Require the existing exact public acknowledgement if public support remains exposed.
4. **Mirror-all selection**
   - Discover repositories read-only.
   - Default to no broad write; let the user select repositories and show blockers.
   - Re-run preflight immediately before Apply.
5. **Progress and cancellation**
   - Show current durable stage, bounded indeterminate network waits, count progress, and redacted logs.
   - Offer cancellation according to the stage rules above.
6. **Result and recovery**
   - Render structured resource states rather than parsing prose.
   - Show links to Forgejo/GitHub resources, local clone, and journal.
   - On partial failure or cancellation, show the exact safe resume command and retain all resources.

Logs are plain text, redacted before signal emission and again before display/persistence. Links are constructed only from validated host/owner/name fields. Rich text never renders service-supplied HTML.

## Authentication and external commands

### First-release recommendation

Keep the current model for the first GUI release, with one change in product wording: GitHubKit and pyforgejo are the API clients; `gh` is an optional authentication/login compatibility path.

- Detect system `git`, `ssh`, and `gh` with absolute paths.
- Execute version/auth checks using argument arrays and bounded timeouts.
- Continue direct API calls through GitHubKit and pyforgejo.
- If an existing `gh` login is selected, call only the reviewed fixed commands (`gh auth status` and `gh auth token`) and keep the returned token in memory for the SDK.
- Never bundle an interactive terminal or accept arbitrary `gh`/Git/SSH arguments.

This is the lowest-risk route because it preserves current, tested behavior and works with GitHub Enterprise hosts. It does mean installation and login are prerequisites.

### Direct GitHub OAuth Device Flow

A direct flow gives the best integrated UX and can remove `gh` as an authentication requirement:

1. register and govern a GitHub OAuth App or GitHub App with Device Flow enabled;
2. POST the application client ID to the device-code endpoint;
3. display `verification_uri` and the short user code, with an Open Browser action;
4. poll at no less than GitHub's returned interval, honoring `authorization_pending`, `slow_down`, expiry, and user denial;
5. request only the minimum scopes required for private repository administration;
6. pass the resulting token to GitHubKit in memory;
7. store it in the OS credential service and implement logout/revocation.

GitHub's documentation says the device flow requires the app client ID and that `slow_down` increases the polling interval by five seconds. A native application cannot protect an embedded client secret; do not treat one as confidential. Enterprise Server endpoint/version behavior and organization OAuth restrictions need real-host tests.

Device Flow should be phase 2, gated on application ownership, scope review, revocation, enterprise-host tests, and secure-storage tests. Until then, `gh` remains a clear compatibility/login fallback.

### Credential storage

Use the maintained Python `keyring` abstraction behind a small `CredentialStore` interface:

- Windows: Credential Locker/Credential Manager backend;
- macOS: Keychain;
- Linux: Freedesktop Secret Service, with the required Secret Service/DBus dependency.

At startup, verify the selected backend is secure and usable. Fail closed rather than silently fall back to a plaintext or null backend. Account keys should include service, host, and user; do not enumerate or log values. Linux headless sessions without a Secret Service must keep the current protected-environment path available for the CLI. Add platform tests for lock/unlock, logout, missing service, denied access, and migration from the environment variable.

### Bundle decision for `gh`, Git, and OpenSSH

| Option | Benefit | Cost/risk | Decision |
| --- | --- | --- | --- |
| System tools | OS/package-manager updates, smaller artifact, established trust/config | prerequisite drift and PATH ambiguity | **Use for release 1; resolve and display absolute paths** |
| Bundle `gh` | consistent login fallback | per-platform binary, updater/CVE/provenance work, MIT notice, larger release | Do not bundle; make it optional once Device Flow lands |
| Bundle Git | consistent clone behavior | large dependency tree, GPLv2 redistribution/source obligations, credential-helper integration | Do not bundle |
| Bundle OpenSSH | consistent SSH client | crypto provenance, host integration, BSD-family and third-party notices, patch burden | Do not bundle |

Evidence does not favor verified sidecars for release 1. A future sidecar proposal must include exact upstream version, checksum/signature verification, SBOM, CVE response owner, update SLA, platform size, and legal approval.

## Toolkit decision matrix

No concrete PySide6 blocker was found, so Tauri was not built. It is retained only as a fallback comparison.

| Criterion | PySide6 + application service | Tauri 2 + Python sidecar |
| --- | --- | --- |
| Existing Python core reuse | Direct imports; one service and type model | IPC protocol plus separately packaged Python sidecar |
| Non-blocking work | QThread/QThreadPool and QProcess | Rust async/front-end events plus sidecar IPC |
| Packaging proof here | Linux one-file and standalone artifacts executed | Not exercised because PySide6 had no blocker |
| Native formats | Nuitka output; installer tooling added per OS | Built-in bundler supports NSIS/MSI, app/DMG, deb/rpm/AppImage |
| Updater | No PySide-specific built-in updater; use package managers or a signed manifest | Official updater verifies a mandatory signature |
| Security surface | Python + Qt + selected plugins | Rust/Tauri + WebView + JavaScript dependencies + Python sidecar/IPC |
| Build complexity | Python, Qt, C compiler | Rust, Node/web UI, WebView, Python compiler, sidecar per target triple |
| Cancellation/recovery | Can call service directly at journaled boundaries | Must design cancellation and recovery across IPC |
| Team maintenance cost | Lowest for this repository | Higher; two language ecosystems and duplicated release provenance |

Reconsider Tauri only if a native-runner proof shows an unresolved PySide blocker in signing/notarization, accessibility, enterprise deployment, updater requirements, or a release-size ceiling that plugin pruning cannot meet. A Tauri fallback must still build and sign one Python sidecar per target and must define a narrow, authenticated IPC allowlist; it must never expose arbitrary command execution.

## Native build and distribution plan

Pin Python, PySide6, Nuitka, build tools, and hashes in a dedicated GUI build lock before production. The commands below are the proposed exact runner steps; only the Linux spike commands above were executed in this proof.

### Windows x86-64

Build on Windows with CPython 3.11 x64 and MSVC Build Tools:

```powershell
py -3.11 -m venv .venv-gui
.venv-gui\Scripts\python.exe -m pip install --upgrade pip
.venv-gui\Scripts\python.exe -m pip install . PySide6==6.11.1 Nuitka==4.1.3
.venv-gui\Scripts\pyside6-deploy.exe gui\main.py `
  --name kosui-forge `
  --mode standalone `
  --nuitka-version 4.1.3 `
  --keep-deployment-files `
  -f
```

Expected primary artifact: `kosui-forge.exe` plus its standalone directory. Sign the executable and DLLs before constructing the installer. Build an Inno Setup installer from a reviewed `packaging/windows/kosui-forge.iss`:

```powershell
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" `
  "/DMyAppVersion=$env:RELEASE_VERSION" `
  packaging\windows\kosui-forge.iss
```

Expected installer: a signed `kosui-forge-<version>-windows-x86_64-setup.exe`. An MSIX may be added for managed environments after filesystem/SSH integration is tested; it is not assumed here.

Use `signtool sign /fd SHA256 /td SHA256 /tr <approved-RFC3161-URL> /a <file>` with an approved OV/EV certificate or Microsoft Artifact Signing identity, then `signtool verify /pa /all /v <file>`. Sign both application files and the final installer. SmartScreen is reputation-based; a valid signature improves identity and reputation continuity but does not guarantee that a new publisher or artifact will never warn. Retain the publisher identity across releases and submit false positives through Microsoft channels.

### Linux x86-64

Build the standalone directory on the oldest glibc runner in the support policy, not on the newest developer machine:

```sh
python3.11 -m venv .venv-gui
.venv-gui/bin/python -m pip install --upgrade pip
.venv-gui/bin/python -m pip install . PySide6==6.11.1 Nuitka==4.1.3
.venv-gui/bin/pyside6-deploy gui/main.py \
  --name kosui-forge \
  --mode standalone \
  --nuitka-version 4.1.3 \
  --keep-deployment-files \
  -f
```

Expected primary artifact: `kosui-forge.dist/` containing `kosui-forge.bin` and its libraries/plugins. Preserve a `.tar.zst` of that exact directory for diagnostics.

Recommended direct-download format for release 1: AppImage, because system Git/SSH access and broad project-directory access make a Flatpak sandbox a poor first fit. Stage the standalone payload under `AppDir/usr/lib/kosui-forge`, add a launcher under `AppDir/usr/bin`, and provide a reverse-DNS `.desktop` file, icon, and AppStream metainfo. Then run:

```sh
OUTPUT="kosui-forge-${RELEASE_VERSION}-linux-x86_64.AppImage" \
./linuxdeploy-x86_64.AppImage \
  --appdir AppDir \
  --executable AppDir/usr/bin/kosui-forge \
  --desktop-file packaging/linux/io.kosuiroom.KosuiForge.desktop \
  --icon-file packaging/linux/io.kosuiroom.KosuiForge.png \
  --output appimage
```

Validate the desktop entry, AppStream metadata, X11, Wayland, a minimal desktop image, no-display/headless error handling, Secret Service present/absent, and external Git/SSH discovery. Flatpak can be reconsidered after a portal/SSH-agent/system-tool design exists. Distro-specific `.deb`/`.rpm` packages are later channels, not substitutes for the standalone proof.

### macOS

Build on macOS. Produce and test arm64 first; add an x86-64 build or a verified universal2 process only after wheel and native-library architecture checks.

```sh
python3.11 -m venv .venv-gui
.venv-gui/bin/python -m pip install --upgrade pip
.venv-gui/bin/python -m pip install . PySide6==6.11.1 Nuitka==4.1.3
.venv-gui/bin/pyside6-deploy gui/main.py \
  --name kosui-forge \
  --mode standalone \
  --nuitka-version 4.1.3 \
  --keep-deployment-files \
  -f
```

Expected primary artifact: `kosui-forge.app`. Sign nested code from the inside out with hardened runtime and a Developer ID Application identity; do not use an unreviewed blanket `--deep` signing step. After verifying the bundle, create and sign the DMG:

```sh
find kosui-forge.app/Contents -type f \
  \( -name '*.dylib' -o -name '*.so' \) -print0 |
while IFS= read -r -d '' item; do
  codesign --force --options runtime --timestamp \
    --sign "$DEVELOPER_ID_APPLICATION" "$item"
done
codesign --force --options runtime --timestamp \
  --sign "$DEVELOPER_ID_APPLICATION" \
  kosui-forge.app/Contents/MacOS/kosui-forge
codesign --force --options runtime --timestamp \
  --sign "$DEVELOPER_ID_APPLICATION" kosui-forge.app
codesign --verify --deep --strict --verbose=2 kosui-forge.app
hdiutil create -volname "kosui-forge" \
  -srcfolder kosui-forge.app -ov -format UDZO \
  "kosui-forge-${RELEASE_VERSION}-macos.dmg"
codesign --force --timestamp --sign "$DEVELOPER_ID_APPLICATION" \
  "kosui-forge-${RELEASE_VERSION}-macos.dmg"
xcrun notarytool submit \
  "kosui-forge-${RELEASE_VERSION}-macos.dmg" \
  --keychain-profile kosui-forge-notary --wait
xcrun stapler staple "kosui-forge-${RELEASE_VERSION}-macos.dmg"
spctl --assess --type open --context context:primary-signature -v \
  "kosui-forge-${RELEASE_VERSION}-macos.dmg"
```

Apple states that software distributed with Developer ID must be notarized. Store signing/notary credentials only in the runner's protected keychain/secret service.

### Native runner matrix

| Runner | Required proof |
| --- | --- |
| Windows 11 x86-64 | MSVC build, standalone launch, installer install/upgrade/uninstall, path-with-spaces, Credential Manager, Git for Windows/OpenSSH variants, signature verification, SmartScreen observation |
| Linux oldest supported glibc x86-64 | standalone and AppImage build, GLIBC audit, X11/Wayland, minimal clean images, Secret Service present/absent, system Git/SSH, desktop/AppStream validation |
| macOS current and previous supported versions on arm64 | `.app` launch, Keychain, system Git/SSH, hardened runtime, signing, notarization, stapling, DMG install, Gatekeeper assessment |
| Optional macOS x86-64 | same proof if Intel is in the support policy |
| Future Windows arm64/Linux arm64 | separate native runners and artifacts; not implied by x86-64 success |

Except for an explicitly documented and vendor-supported exception, every target artifact is built, signed, and smoke-tested on that operating system. Tauri documents a specialized NSIS cross-compile route, but it does not change the PySide6 decision or prove runtime behavior on Windows.

## Updates, integrity, and release checks

Release 1 should notify rather than self-modify:

1. fetch a small HTTPS release manifest from the controlled Forgejo/GitHub release location;
2. compare SemVer and platform/architecture;
3. show release notes and open the signed download page;
4. let the OS installer/package manager perform the update.

This avoids inventing a privileged cross-platform updater before signing and rollback are mature. Preferred update channels are WinGet/managed Windows deployment, Homebrew cask on macOS, and an AppImage-aware or distro package channel on Linux. Qt Installer Framework can provide a cross-platform maintenance tool and online repositories, but adds another distribution system and should be evaluated only if notification plus package-manager channels are insufficient.

For every release:

```sh
sha256sum kosui-forge-* > SHA256SUMS
```

Generate checksums on the native release runner after signing/notarization, never before. Sign the checksum manifest with the project's approved release-signing mechanism, publish an SBOM and provenance/attestation, and verify downloaded assets before promotion. The release gate must compare artifact hashes between staging and publication and scan artifacts for embedded credentials.

Tauri's updater requires cryptographic update signatures and does not permit disabling verification. That is a useful reference design, not sufficient reason to add Rust, WebView, JavaScript, and a Python sidecar now.

## Licensing and redistribution review

This section is an engineering inventory, not legal advice. Final notices, source-offer mechanics, and license compatibility require maintainer/legal approval before public distribution.

| Component | Observed/upstream terms | If shipped or used to build | Required decision/review |
| --- | --- | --- | --- |
| PySide6/Shiboken 6.11.1 and Qt | Package metadata: LGPL-3.0-only OR GPL alternatives; commercial option exists; Qt contains module-specific third-party code | Use shared Qt libraries under the selected LGPL route, include license/copyright notices, allow replacement/relinking as required, provide corresponding source/source offer and installation information where required, inventory only shipped modules/plugins | Legal review LGPLv3 compliance or purchase commercial Qt; aggressively prune plugins and include Qt SPDX SBOM/notices |
| Nuitka 4.1.3 | Compiler is AGPLv3; installed `LICENSE-RUNTIME.txt` grants a runtime-library exception allowing resulting target code under terms of choice | Compiler is a build tool and is not shipped; preserve license, runtime-exception, and NOTICE evidence in the compliance archive | Confirm release artifact contains only exception-covered runtime, and publish applicable notices; do not distribute a modified compiler without AGPL review |
| CPython | PSF license plus bundled third-party notices | Python runtime is bundled | Include PSF and relevant runtime notices/source references |
| GitHubKit 0.16.0 and schemas | MIT | Directly bundled by the production GUI if GitHub features are included | Include MIT notices and inventory selected schema packages; avoid `all-schemas` if a smaller supported subset is possible |
| pyforgejo 2.0.7 | MIT | Directly bundled | Include MIT notice and generated-client notices |
| SDK transitives | Observed metadata includes BSD-3-Clause, MIT, Apache-2.0, MPL-2.0, and one unclassified package (`hishel`) | Bundled according to actual static analysis | Generate an SBOM/license report from the final artifact, resolve every unknown, and include notices/source obligations; do not infer from the build venv alone |
| Python `keyring` and backend dependencies | Project documents macOS Keychain, Secret Service/KWallet, and Windows Credential Locker; exact release not installed in this spike | Will be bundled when credential storage is implemented | Pin a version, verify its license and every backend/transitive dependency, exclude insecure alternate backends by default |
| GitHub CLI | MIT | Only if later bundled | Include MIT notice, upstream provenance, checksum/signature, SBOM, and update owner |
| Git | Project COPYING identifies GPLv2 | Only if later bundled | GPLv2 source/offer and notices, plus licenses for bundled helpers/libraries; avoid by using system Git |
| OpenSSH | Upstream `LICENCE` describes BSD or more permissive component terms and no GPL code; crypto/compression libraries have separate terms | Only if later bundled | Ship complete upstream and third-party notices, provenance, export/security review, and patch process; avoid by using system OpenSSH |
| Inno Setup/linuxdeploy/AppImage tools | Build/installer tooling with their own terms | Usually build-time, but installer/runtime pieces may be conveyed | Review exact versions and conveyed runtime before adopting |

The Qt for Python license page recommends acknowledging the third-party components actually used. Qt 6.8 and later publish SPDX 2.3 SBOM data. The release process should merge that data with a Python dependency SBOM and a binary-level inventory; a lock file or `pip list` alone is not proof of shipped content.

## Phased implementation tasks

### Phase 0 — close packaging proof gaps

- Pin a minimal PySide6/Nuitka build environment and hashes.
- Commit a production-oriented `pysidedeploy.spec` only after it has a Qt plugin allowlist.
- Rebuild Linux on the oldest supported glibc runner.
- Eliminate missing delayed-load libraries; test X11 and Wayland clean images.
- Generate artifact SBOM, license bundle, checksums, and dependency report.

### Phase 1 — application service boundary

- Define typed requests, events, results, and cancellation token.
- Adapt Doctor to emit progress and check cancellation between checks.
- Move CLI orchestration behind the shared service without changing CLI snapshots, exit codes, safety gates, or journals.
- Add redaction tests at the event boundary.

### Phase 2 — minimal useful GUI

- Implement setup wizard, Doctor checklist, dry-run plan review, create flow, mirror selection, redacted logs, links, cancellation states, and journal recovery.
- Use direct GitHubKit/pyforgejo adapters and fixed-argument system-tool probes.
- Add accessibility names, keyboard navigation, high-DPI behavior, and crash-safe settings.

### Phase 3 — credentials

- Add `CredentialStore` with Windows Credential Manager, macOS Keychain, and Linux Secret Service tests.
- Keep protected environment variables for headless CLI automation.
- Add GitHub Device Flow only after app governance/scope/revocation review; retain `gh` fallback.

### Phase 4 — native release lanes

- Add Windows installer/signing and clean-VM tests.
- Add Linux AppImage/desktop/AppStream packaging on the baseline runner.
- Add macOS signing/notarization/DMG lane.
- Publish checksums, SBOMs, provenance, notices, and a signed update-notification manifest.

### Phase 5 — updater decision

- Measure support burden for package-manager channels and update notifications.
- Evaluate Qt Installer Framework or a TUF-style staged updater only if users require in-app updates.
- Reconsider Tauri only against a recorded unresolved PySide6 go/no-go failure.

## Go/no-go criteria

### Architecture go

- CLI and GUI invoke the same service and policy tests; no workflow logic is copied.
- UI remains responsive during Doctor/create/mirror operations.
- Progress events and results are typed and redacted before leaving the service.
- Cancellation behavior is defined and tested for read-only, pre-mutation, post-mutation, and ambiguous-request states.
- Journals and resource links make every partial result recoverable.

### Platform release go

- Each OS builds on a native runner and passes clean-machine launch/smoke tests.
- Linux has an explicit minimum glibc, no missing `ldd` dependencies, tested X11 and Wayland paths, and a validated desktop entry/AppStream file.
- Windows application and installer signatures verify, upgrade/uninstall works, and SmartScreen behavior is recorded.
- macOS `.app` and DMG are signed, notarized, stapled, and pass Gatekeeper assessment.
- Credentials are stored only in an approved OS backend; plaintext fallback is impossible in GUI mode.
- Final artifacts contain no credentials, publish SHA-256 checksums, SBOM/provenance, and complete reviewed notices.
- Dependency and CVE owners, update SLA, and rollback instructions are documented.

### No-go triggers

- Any GUI-only copy of privacy, confirmation, collision, mirror, or journal logic.
- Shell interpolation, arbitrary command fields, or tokens in arguments/logs.
- Hard cancellation inside an unjournaled mutation.
- A package that starts only on the build host, has missing delayed-load dependencies, or requires a newer OS than documented.
- Unsigned Windows/macOS public artifacts, failed notarization, unknown license metadata, or unresolved Qt LGPL/source-offer obligations.
- Bundling `gh`, Git, or OpenSSH without provenance, update ownership, SBOM, notices, and measurable benefit.

On the evidence in this spike, the **architecture is go** and **production distribution is conditionally no-go** until Phase 0 and all native-runner release gates pass.

## Primary references

Accessed 2026-07-21:

- Qt for Python, `pyside6-deploy`: <https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-deploy.html>
- Qt for Python license inventory: <https://doc.qt.io/qtforpython-6/licenses.html>
- Qt licensing and LGPL: <https://doc.qt.io/qt-6/licensing.html> and <https://doc.qt.io/qt-6/lgpl.html>
- Qt third-party components/SBOM: <https://doc.qt.io/qt-6/licenses-used-in-qt.html>
- Nuitka manual and license/download statement: <https://nuitka.net/user-documentation/user-manual.html> and <https://nuitka.net/doc/download.html>
- GitHub OAuth Device Flow: <https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps>
- Python keyring project: <https://pypi.org/project/keyring/>
- Microsoft SmartScreen reputation and signing options: <https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation> and <https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options>
- Apple notarization and Developer ID: <https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution> and <https://developer.apple.com/developer-id/>
- Desktop Entry and AppStream specifications: <https://specifications.freedesktop.org/desktop-entry/latest/> and <https://www.freedesktop.org/software/appstream/docs/sect-Metadata-Application.html>
- AppImage desktop integration/distribution: <https://docs.appimage.org/reference/desktop-integration.html>
- Tauri sidecars, updater, and distribution: <https://v2.tauri.app/develop/sidecar/>, <https://v2.tauri.app/plugin/updater/>, and <https://v2.tauri.app/distribute/>
- Qt Installer Framework: <https://doc.qt.io/qtinstallerframework/>
- Git, GitHub CLI, and OpenSSH license texts: <https://github.com/git/git/blob/master/COPYING>, <https://github.com/cli/cli/blob/trunk/LICENSE>, and <https://github.com/openssh/openssh-portable/blob/master/LICENCE>
