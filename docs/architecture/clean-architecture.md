# Clean Architecture boundaries

<!-- markdownlint-disable MD013 -->

- Status: proposed architecture decision for human review
- Scope: source dependency direction and incremental `repo_bootstrap` migration
- Compatibility baseline: Kosui Forge 0.2.x and the `repo-bootstrap` command

## Context

Kosui Forge is growing from a tested headless repository-bootstrap tool into a cross-platform desktop product. The compatibility implementation already contains important privacy, collision, preflight, mirror, redaction, and recovery behavior. Moving all of that code at once would make behavioral parity difficult to prove and would put user repositories at risk.

The project therefore uses Clean Architecture with an incremental, vertical-slice migration. New application entry points depend on typed inward boundaries. Existing `repo_bootstrap` modules remain available while one use case at a time is characterized, moved, adapted, and proven compatible. A directory name alone is not an architecture boundary; import-contract tests must enforce the dependency rule.

This decision supplements the product and safety rules in the root `AGENTS.md`. It does not weaken any privacy, no-write, collision, credential, cancellation, journaling, or human-review requirement.

## Audited baseline

This decision is grounded in the Kosui Forge 0.2.0 tree at foundation merge `7121dd3`. The audit covered `pyproject.toml`, both architecture/dependency documents, every module under `src/kosui_forge` and `src/repo_bootstrap`, every test module and Forgejo 11.0.16 fixture, and the executable path declared by packaging.

The installed project currently has two third-party runtime integrations, GitHubKit and pyforgejo, and one console entry point: `repo-bootstrap = repo_bootstrap.cli:main`. There is no `kosui-forge` desktop entry point, PySide6 dependency, production desktop package, credential-store interface, repository catalog, or settings store. Those capabilities must not be represented as implemented.

The foundation already contains one partial vertical slice:

```text
repo_bootstrap.cli                         argparse entry point and compatibility rendering
  -> kosui_forge.infrastructure.doctor     concrete Doctor composition
     -> kosui_forge.adapters.doctor        bridge to repo_bootstrap config/SDK/preflight code
     -> kosui_forge.application.doctor     typed Doctor orchestration
        -> kosui_forge.ports.doctor        minimal read-only capability
```

Actual imports confirm that `application.doctor` depends on `ports.doctor` and application contracts, while `adapters.doctor` imports the compatibility config, provider clients, preflight report, and redaction function. `infrastructure.doctor` imports both the adapter and service plus the concrete compatibility factories it wires. The console entry point still imports `repo_bootstrap` configuration, clients, preflight, journals, paths, and workflow directly for `configure`, `create`, and `mirror-all`; only Doctor crosses the application boundary.

The compatibility package is intentionally mixed today:

- `preflight` combines policy, platform/filesystem/process/provider reads, cancellation collection, and text rendering;
- `workflow` combines repository and mirror policy, orchestration, journal calls, Git calls, and reporter text;
- `config` and `journal` combine value rules with atomic filesystem persistence;
- `forgejo` and `github` normalize SDK responses, while `github`, `gitops`, and `preflight` start fixed argument arrays through subprocesses;
- `mirror` contains useful policy and orchestration but also broad compatibility-local provider protocols;
- `paths` and `config` currently contain overlapping platform config-path discovery;
- `validation` contains deterministic repository-name and SSH-public-key shape checks with no I/O.

The existing command runners use argument sequences rather than shell strings, but the compatibility implementations in `github`, `gitops`, and `preflight` do not yet pass explicit subprocess timeouts or bound captured output. Migration must preserve fixed-argument behavior and add reviewed timeout/output bounds with real subprocess tests; this document does not claim those safeguards already exist.

Behavior tests establish the compatibility surface: private defaults and public acknowledgement, no-write Doctor/dry-run behavior, collision failures, SDK schemas, Forgejo 11.0.16 payloads, cross-platform paths, fixed Git operations, redaction, journals, partial failures, and exit codes. The authenticated mutation test is separately gated by `REPO_BOOTSTRAP_E2E=1` and retains resources for manual review rather than deleting them.

## Shared terminology

These terms have one meaning throughout code, tests, reviews, and planning:

- **Domain**: provider-neutral entities, value objects, and business or safety policy. Domain code does not perform I/O.
- **Application service / use case**: orchestration for one user goal. It accepts typed requests, invokes ports, and returns typed events or results.
- **Port**: the smallest `Protocol` or abstract interface an application service needs from an outer capability. A port is driven by a real use case, not anticipated reuse.
- **Adapter**: an implementation that translates between a port and a concrete SDK, subprocess, filesystem, keyring, or the temporary compatibility core.
- **Presentation adapter**: CLI text, Qt view-model, or widget-facing code that converts user intent to application requests and renders typed results. It owns no repository policy.
- **Infrastructure**: composition roots and platform/package glue. It selects concrete implementations but makes no business decision.
- **Composition root**: a narrow entry-point module where concrete objects are constructed and injected. It is the only place that may know both a use case and its concrete adapters.
- **Compatibility shim**: a temporary, tested import or call bridge that preserves an existing public behavior while ownership moves inward. Every shim has explicit removal criteria.
- **Safe boundary**: a point where cancellation or retry is unambiguous. Mutating safe boundaries are journaled.

## Dependency rule

Source dependencies point inward. The accepted direction is:

```text
presentation ---------------------> application ---> domain
                                         |
                                         +---------> ports ---> domain
adapters -----------------------------> application/ports/domain
infrastructure ---> presentation/application/adapters
```

The diagram is about source dependencies, not runtime callbacks. At runtime an application service calls an injected port and an adapter implements that port; the adapter still imports inward.

The following invariants are mandatory and independently testable:

1. `domain` is standard-library-only and performs no I/O.
2. `application` imports only its own modules, `domain`, `ports`, and the standard library; it never imports a concrete adapter or outer framework.
3. `ports` are minimal and use-case-driven. They expose capabilities and provider-neutral values, not SDK clients, credential material, or speculative provider APIs.
4. `adapters` implement ports and depend inward. Translation code may map concrete failures and values but must not become a second policy implementation.
5. `presentation` invokes application services and renders typed events/results without duplicating privacy, collision, mirror, credential, journal, or recovery policy.
6. `infrastructure` only constructs and wires concrete implementations. It does not select business outcomes or format user-visible results.

Service locators, dependency cycles, cross-layer convenience imports, arbitrary-shell APIs, and credential-bearing interfaces are prohibited. Dynamic imports may not be used to hide an otherwise forbidden edge. Ports and application requests may identify a protected credential by non-secret reference when a use case requires it, but they never carry the credential value, authenticated URL, authorization header, password, or private key.

| Layer | May import | Must not import or own |
| --- | --- | --- |
| `domain` | Python standard library and other domain modules | application, ports, adapters, presentation, infrastructure, Qt, SDKs, subprocess/filesystem infrastructure, keyring, any I/O call |
| `application` | standard library, `domain`, `ports`, and its own modules | concrete adapters, compatibility code, Qt, SDKs, subprocess/filesystem infrastructure, keyring, presentation, infrastructure |
| `ports` | standard library, `domain`, and its own modules | application orchestration, concrete adapters, Qt, SDKs, subprocess/filesystem infrastructure, keyring, presentation, infrastructure |
| `adapters` | standard library, third-party integrations, compatibility code, `domain`, `application`, `ports`, and its own modules | presentation, infrastructure, application policy duplicated in translation code |
| `presentation` | standard library, UI/toolkit libraries, `domain`, `application`, and its own modules | ports, concrete adapters, infrastructure, `repo_bootstrap`, privacy/collision/mirror/journal policy |
| `infrastructure` | all inward layers and concrete integration packages needed for construction | business or safety decisions, presentation formatting, a global service locator |

`pathlib.Path` is permitted as an immutable typed boundary value. Inner layers may not use it to inspect or mutate the filesystem. Process and filesystem infrastructure includes imports such as `os`, `shutil`, `subprocess`, and `tempfile`; those belong in adapters or infrastructure.

Package facades are not an internal shortcut. Layer code imports the exact inward module it needs rather than importing `kosui_forge` and relying on convenience re-exports. The top-level package may continue to expose reviewed public application contracts to installed consumers.

## Layer responsibilities and approved first-slice module map

The approved map extends only behavior already present in the Doctor foundation and deterministic compatibility validation. It is the destination for the first boundary implementation, not a claim that every path below existed at the audited baseline. Empty layer packages carry a boundary description, not a framework or speculative interface.

- **Domain** owns deterministic provider-neutral entities, values, and invariants. It neither orchestrates a user goal nor knows how data is stored or transported.
- **Application** owns one use case per user goal: request validation, sequencing, safe cancellation points, typed progress/results, and calls through ports.
- **Ports** express only capabilities called by an application service. Protocol values are immutable, redacted before they cross a sensitive boundary, and independent of SDK response shapes.
- **Adapters** translate concrete SDK, subprocess, filesystem, keyring, or compatibility behavior to a port. They enforce transport-level validation and defense-in-depth redaction without deciding product policy.
- **Presentation** parses user intent and renders typed application output for argparse or Qt. It owns interaction mechanics, not the rule that determines whether an operation is safe.
- **Infrastructure** is executable-specific construction and platform/package glue. It chooses implementations from explicit configuration and injects them; it does not orchestrate the operation after construction.

```text
src/kosui_forge/
  __init__.py                   reviewed public version/application-contract facade
  domain/
    repository.py             repository-name and SSH-public-key value validation
  application/
    contracts.py              immutable requests, events, results, and cancellation state
    doctor.py                 read-only Doctor use case orchestration
  ports/
    doctor.py                 minimal DoctorPort and raw adapter report values
  adapters/
    doctor.py                 bridge to the tested repo_bootstrap preflight core
  presentation/
    cli/
      doctor.py               compatibility Doctor text rendering
  infrastructure/
    cli.py                    headless CLI composition root
    doctor.py                 temporary import compatibility shim
```

At the audited baseline, `application/contracts.py`, `application/doctor.py`, `ports/doctor.py`, `adapters/doctor.py`, and `infrastructure/doctor.py` already exist. The first boundary implementation adds `domain/repository.py` by moving the already-tested deterministic validation functions, extracts only Doctor text rendering into `presentation/cli/doctor.py`, and normalizes the headless root at `infrastructure/cli.py`. `infrastructure/doctor.py` then becomes a temporary compatibility import for the foundation path.

The `domain` slice is deliberately small because only repository-name and SSH-public-key shape validation is currently provider-neutral, deterministic, and covered independently of I/O. The `presentation` slice is equally narrow because Doctor is the only application service currently consumed by the CLI. Create, mirror, configuration, persistence, credentials, and desktop interfaces gain new modules only when a real migrated use case requires them.

Layer package `__init__.py` files do not re-export cross-layer services. This avoids hidden edges and circular imports.

## Doctor data and control flow

Doctor is the first migrated vertical slice:

```text
repo_bootstrap.cli
  -> kosui_forge.infrastructure.cli.build_doctor_service
       -> RepoBootstrapDoctorAdapter
       -> DoctorService
  -> DoctorService.run(DoctorRequest)
       -> DoctorPort.run(... callbacks ...)
            -> repo_bootstrap.preflight.run_preflight
            -> RepoBootstrapDoctorAdapter maps raw checks
       -> DoctorService redacts and emits OperationEvent values
       -> DoctorService returns OperationResult
  -> kosui_forge.presentation.cli.doctor.render_doctor_result
  -> compatibility stdout/stderr and exit code
```

Control enters through presentation, concrete wiring happens in infrastructure, application orchestration calls only the port, and raw compatibility values return through the adapter. The presentation renderer receives only an `OperationResult`; it cannot create clients, inspect configuration, or make a policy decision.

Redaction occurs in the compatibility service boundary and again before compatibility output where existing behavior requires it. A port never carries a token, password, private key, authorization header, or authenticated URL. The Doctor port accepts only non-secret configuration location/selection values, callbacks, and raw check data that is redacted before it crosses the application boundary.

Future mutating slices preserve the control relationships already tested in `repo_bootstrap.workflow`, `mirror`, and `journal` without copying their mixed implementation. Presentation submits one typed request. The application service validates provider-neutral values, completes read-only collision/preflight calls through narrow ports immediately before mutation, and returns without writes if any check is ambiguous. After confirmation, the service records a durable safe boundary through a journal port before invoking one mutation capability at a time. Each adapter maps the concrete response or ambiguous failure into a typed result; the application records the resulting durable state before cancellation can be honored. Presentation receives typed progress, resource links, and recovery context and never infers state by parsing adapter prose.

## Composition roots

### Headless CLI

Packaging dispatches `repo-bootstrap` directly to `repo_bootstrap.cli:main`. At the audited baseline that presentation calls `kosui_forge.infrastructure.doctor.build_doctor_service`; the approved first slice places the executable-wide headless root at `kosui_forge.infrastructure.cli` and retains the old module as a compatibility import. The root constructs `RepoBootstrapDoctorAdapter`, injects the configured compatibility factories, and returns `DoctorService`. It contains no parsing, rendering, privacy choice, preflight rule, or exception-to-exit-code policy.

`repo_bootstrap.cli` remains the executable compatibility presentation. During migration it may import the infrastructure composition root, typed application contracts, and presentation renderers. It must not instantiate an alternative policy implementation. When every compatibility command delegates to application services, a later reviewed entry-point migration may make `kosui_forge.infrastructure.cli` the installed callable; that packaging change is not part of this decision's first slice.

### Future desktop application

`kosui_forge.infrastructure.desktop` is the reserved composition-root name for the future PySide6 application. The module does not exist yet because there is no production desktop presentation to compose. Creating a placeholder Qt framework or speculative desktop ports would violate this decision. The module is added with the first real desktop vertical slice and will:

1. construct concrete credential, persistence, provider, Git, and compatibility adapters required by that slice;
2. inject them into the same application services used by headless interfaces;
3. construct the desktop presenter/view-model graph;
4. return or start the application shell without embedding business policy.

Qt imports belong in `presentation.desktop` and platform-specific desktop composition code, never in domain, application, or ports. Qt workers invoke application services off the GUI thread and deliver immutable events back to the GUI thread.

Each executable owns one visible composition root. There is no module-level mutable container, global registry, dynamic service locator, or cross-layer convenience import.

## Incremental migration sequence

Every remaining use case follows this order:

1. **Characterize** current output, exit codes, safety gates, no-write behavior, journal state, and redaction with focused tests.
2. **Extract domain policy** only when the rule is provider-neutral and performs no I/O.
3. **Define the application request/result** for that one user goal and no more.
4. **Define minimal ports** from the operations the application service actually calls.
5. **Adapt existing code** behind those ports; retain tested compatibility behavior instead of copying it.
6. **Switch all presentations** for the slice to the same application service and typed result.
7. **Prove parity** with unit, contract, real temporary-filesystem/subprocess, and compatibility CLI tests.
8. **Remove the old path** only after no callers use it and the shim's removal criteria are satisfied.

The planned order is Doctor (in progress), repository creation, mirror inspection/provisioning, configuration, journals/recovery, credential sources, catalog/persistence, and then desktop-only workflows. This order does not pre-authorize public creation, destructive operations, or new credential flows.

## `repo_bootstrap` migration map

Every existing compatibility module has a target. A target can be split because the current module often mixes policy, orchestration, and I/O.

| Existing module | Current responsibility | Target boundary | Interim status |
| --- | --- | --- | --- |
| `repo_bootstrap.__init__` | compatibility package version | compatibility facade / packaging | retained while installed consumers and CLI exist |
| `repo_bootstrap.cli` | argparse, plans/prompts, Doctor/create/mirror orchestration, output, exit codes | `presentation.cli` for parsing/rendering; application services for orchestration; `infrastructure.cli` for wiring | Doctor calls the application service through `infrastructure.cli` and uses the extracted typed-result renderer; `infrastructure.doctor` remains an import shim; create/mirror remain compatibility code |
| `repo_bootstrap.config` | config value, validation, platform path/file reads and atomic writes | provider-neutral policy in `domain`; config ports in `ports`; filesystem implementation in `adapters.persistence`; orchestration in `application` | not migrated; no duplicate config model is allowed |
| `repo_bootstrap.doctor` | embedding facade over preflight | application Doctor service plus a compatibility facade | retained for embedding compatibility until consumers move to `DoctorService` |
| `repo_bootstrap.errors` | compatibility exception taxonomy | typed application errors/results and adapter-specific mapped failures | retained until every caller returns typed results without parsing prose |
| `repo_bootstrap.forgejo` | pyforgejo transport and Forgejo schema normalization | `adapters.forgejo` implementing use-case-driven provider ports | retained; later split by real repository/preflight/mirror ports |
| `repo_bootstrap.github` | fixed `gh auth token` command and GitHubKit transport | `adapters.github` plus a narrow authentication adapter | retained; core API behavior continues to use GitHubKit |
| `repo_bootstrap.gitops` | fixed-argument Git subprocesses and working-tree verification | `adapters.git` implementing a Git port; provider-neutral collision rules move to `domain` | retained until create/catalog slices use the port |
| `repo_bootstrap.journal` | durable redacted atomic stage journal | journal value/events in `application`/`domain`, journal port in `ports`, filesystem implementation in `adapters.persistence` | retained; schema/output compatibility is mandatory |
| `repo_bootstrap.mirror` | mirror/deploy-key policy and provider orchestration | mirror policy in `domain`, use case in `application`, Forgejo/GitHub ports in `ports`, provider adapters outside | not migrated; existing protocols are compatibility-local and must not be promoted wholesale |
| `repo_bootstrap.paths` | platform config/state path discovery | platform discovery in `infrastructure` or a persistence adapter | retained while path names stay compatibility commitments |
| `repo_bootstrap.preflight` | read-only policy, platform/process/filesystem checks, provider checks, rendering | check policy in `domain`, Doctor orchestration in `application`, capability ports in `ports`, system/provider checks in adapters, rendering in presentation | Doctor currently bridges this module through `RepoBootstrapDoctorAdapter`; migrate check families vertically |
| `repo_bootstrap.redaction` | common diagnostic secret redaction | provider-neutral redaction policy in `domain` plus defense-in-depth in adapters/presentation/persistence | retained because adapters need compatibility secret knowledge; do not reduce existing redaction layers |
| `repo_bootstrap.validation` | repository and SSH-key shape validation | `kosui_forge.domain.repository` | first boundary slice moves the implementation and leaves a compatibility re-export |
| `repo_bootstrap.workflow` | create and batch orchestration, collision checks, journal calls | policies in `domain`, create/mirror use cases in `application`, minimal ports in `ports`, concrete adapters outside | not migrated; must be split by use case rather than moved as one class |

The mapping is directional, not a request to create every listed package now. For example, `adapters.persistence` appears only when a configuration or journal application service needs a persistence port.

## Compatibility policy

The `repo-bootstrap` command remains a first-class headless compatibility interface. Migration must preserve command names and arguments, output semantics, exit codes, private defaults, explicit public acknowledgement, preflight timing, dry-run/Doctor no-write guarantees, collisions, journals, redaction, and partial-failure recovery. Existing configuration and state paths retain the `repo-bootstrap` name.

The internal `repo_bootstrap` package remains importable during the pre-1.0 migration. New product code must not deepen dependence on it: only compatibility presentations, adapters, and composition roots may import it.

Required temporary compatibility bridges, including the first boundary slice, are:

- `RepoBootstrapDoctorAdapter` already bridges the application Doctor port to `repo_bootstrap.preflight` until check families migrate.
- `repo_bootstrap.doctor.Doctor` already remains as an embedding facade over the compatibility preflight function.
- the first boundary slice changes `repo_bootstrap.validation` into a re-export of `kosui_forge.domain.repository`, so existing callers and all compatibility modules use one policy implementation;
- the first boundary slice changes `kosui_forge.infrastructure.doctor` into a re-export of the builder from `kosui_forge.infrastructure.cli`, preserving the 0.2 foundation import path.

### Removal criteria

A compatibility shim may be deleted only when all of the following are true. These criteria are owner-neutral and based on observable repository/release state rather than assignment to a person or team:

1. repository search proves no supported caller imports it;
2. all presentations for the use case call one application service;
3. focused parity tests cover output, exit code, safety, redaction, cancellation, and journals as applicable;
4. the full required verification matrix passes from a clean install;
5. public deprecation/release notes are present when an installed import path changes;
6. a human-reviewed pull request approves removal.

For `repo_bootstrap.validation`, removal additionally requires a documented replacement import and completion of the applicable public deprecation window. For `kosui_forge.infrastructure.doctor`, removal requires every supported internal and installed caller to use the executable-level root and the applicable public deprecation window to be complete. For `RepoBootstrapDoctorAdapter`, removal requires every Doctor check to be supplied by reviewed ports/adapters and repository search to prove no Doctor path reaches `repo_bootstrap.preflight`. For `repo_bootstrap.doctor.Doctor`, removal requires no supported embedding caller plus parity coverage through `DoctorService`.

## Mechanical enforcement

The executable architecture contract lives in `tests/architecture/import_contract.py`, and the unittest harness that exercises it lives in `tests/architecture/test_import_contracts.py`. The harness scans the real source tree under `src/kosui_forge` and the synthetic fixtures under `tests/architecture/fixtures`.

The checker encodes the layer matrix with the `LAYERS` tuple and `_ALLOWED_LAYER_IMPORTS` mapping, then walks every Python file under the scanned root to collect import edges and cycle candidates. The documented layer rules are enforced as follows:

- all six reviewed layers must exist as real Python packages under `src/kosui_forge`;
- Python modules outside the six reviewed layer packages and the top-level public facade are rejected;
- allowed layer edges only;
- standard-library-only domain code;
- no third-party, Qt, SDK, keyring, compatibility, process, or filesystem-infrastructure imports in inner layers;
- no presentation-to-port/adapter/infrastructure bypass;
- no adapter-to-presentation/infrastructure dependency;
- no direct, relative, or literal dynamic import can bypass an allowed layer edge;
- no inner-layer filesystem I/O hidden behind standard-library modules or APIs;
- no credential-bearing public classes, fields, methods, parameters, annotations, or return types in ports;
- no internal import cycles (strongly connected module components), including relative and literal dynamic imports;
- explicit headless and future desktop composition-root decisions.

The import checker does not stop at a denylist containing only `os`, `shutil`, `subprocess`, and `tempfile`: standard-library filesystem access through `open`, `io.open`, reviewed filesystem-reading or filesystem-writing `pathlib.Path` methods, `fileinput`, `glob`, or `mmap` is equally forbidden in inner layers. Pure path-value operations such as `Path.as_posix()`, `Path.name`, lexical path composition, and similar non-I/O path helpers remain allowed. Literal calls through direct aliases, imported aliases, or straightforward `getattr(..., "import_module")` selection of `importlib.import_module` and `__import__` are dependency edges and participate in both layer and cycle checks; relative import calls using a literal package or the module's `__package__` are resolved too. Non-literal targets passed to those reviewed import functions are reported as unprovable rather than guessed.

The `allowed` fixtures prove that absolute and relative inward imports, safe standard-library imports, pure typed `Path` use, literal standard-library dynamic imports, third-party adapters, UI presentation libraries, Protocol/ABC capability ports, and composition-root wiring are accepted. The `forbidden` fixtures deliberately cover direct imports, from-imports, import aliases, Qt, GitHubKit, keyring, compatibility imports, process/filesystem I/O, direct and aliased file APIs, absolute and relative outward imports, literal and non-literal dynamic imports, package-facade shortcuts, and presentation bypasses. Cycle fixtures cover absolute, relative, and literal-dynamic cycles. Each category needs a relationship assertion proving that the intended violation was found; merely finding some violation in the same fixture is insufficient.

Imports guarded by `typing.TYPE_CHECKING` follow the same matrix because they remain source dependencies even though Python does not execute them at runtime. The allowed fixtures include aliased, inward type-only imports, while a type-only outward port import is rejected. This prevents runtime-only assumptions from hiding coupling without treating a valid inward annotation as a violation.

The port check inspects public type, field, method, function, and parameter names plus public field/parameter/return annotations and straightforward re-export aliases for credential-bearing shapes such as access/authentication tokens, passwords, secrets, authorization headers, authenticated URLs, API keys, and private keys. Explicit non-secret references, identifiers, availability/status metadata, credential capability protocols, and non-credential concepts such as cancellation or pagination tokens remain allowed. This is a deterministic guard against an accidental raw-secret contract, not a semantic proof: sufficiently indirect value flow can still hide a secret and must be rejected in review.

The checker is intentionally static and does not claim to recover arbitrary runtime behavior. It tracks reviewed `Path` constructors, aliases, typed or assigned values, lexical path composition, and path-returning methods used before a filesystem call, but it does not perform general data-flow inference. It records only literal dynamic imports it can resolve; unresolvable targets are treated as `(<dynamic>)` and remain part of the violation set. Straightforward `getattr`-selected `import_module` aliases are reviewed, while broader runtime tricks such as import hooks, `exec`, or `eval` remain outside the checker's proven scope and are not claimed as covered by these tests. Cycle detection is limited to the modules reachable under the scanned root and the import edges the checker can prove from source text.

Architecture fixtures are source text, not executable tests. They must never contain credentials, network calls, filesystem mutation, or generated dependencies.

Run the contract checks from the repository root with `python3 -m unittest -v tests.architecture.test_import_contracts`.

## Testing strategy

Architecture checks complement rather than replace behavior tests:

1. focused domain tests cover provider-neutral invariants and compatibility re-exports;
2. application tests use in-memory fake ports and assert typed results, progress order, cancellation, and redaction;
3. adapter contract tests exercise mapping against Forgejo 11.0.16 fixtures and reviewed SDK behavior;
4. presentation tests assert exact compatibility rendering without policy;
5. composition tests prove concrete adapters are injected at the correct root;
6. temporary-filesystem and fixed-argument subprocess tests cover real security boundaries;
7. compatibility CLI tests preserve stdout/stderr and exit codes;
8. the full unit suite, Ruff, formatting, Mypy, compileall, build, clean-wheel install, entry-point help/version, diff checks, and credential scans gate delivery.

Authenticated or mutating E2E tests remain explicitly gated to disposable environments. Architecture work never creates a real remote repository merely to prove an import rule.

## Security implications

The dependency rule keeps secrets and side effects outside policy code. Ports describe capabilities, not credentials: an application service asks an injected provider to inspect or mutate a named resource and does not receive a reusable token. Adapters obtain credentials from approved outer sources and redact failures before returning. No interface may accept arbitrary shell text; every migrated external-tool adapter must invoke an executable with a fixed argument array, bounded output, and a timeout.

Infrastructure is powerful because it sees concrete implementations. Keeping it small and covered by composition tests limits that risk. A composition root must not become a second workflow, policy switchboard, or service locator.

## Rejected alternatives

### Big-bang package move

Rejected because current modules mix policy and I/O and because a mass move would obscure output, exit-code, safety, and journal regressions. Vertical slices provide reviewable parity evidence.

### Treat `repo_bootstrap` as the domain layer

Rejected because it imports SDKs, subprocesses, filesystems, and presentation formatting. Naming it "core" would not satisfy inward dependencies.

### Put all protocols in one speculative ports module

Rejected because broad provider interfaces couple unrelated use cases and expose credential-bearing or destructive capabilities unnecessarily. Ports are added from observed application calls.

### Global dependency container or service locator

Rejected because dependencies become hidden, tests can accidentally use production integrations, and cycles are difficult to detect. Constructors and explicit composition roots are sufficient.

### Allow presentation to call adapters for convenience

Rejected because CLI and desktop policy would drift. Presentation creates requests and renders typed application output only.

### Use an import-contract dependency now

Rejected for the current small tree because a focused standard-library AST test is auditable, fast, and supports deliberate fixtures and cycle checks. Reconsider a maintained tool only if the checker becomes materially more complex; preserve the same rules and fixtures.

### Create an empty desktop framework now

Rejected because there is no production desktop vertical slice to compose. The future root and dependency direction are explicit, but code is added only with real behavior and tests.

## Consequences

The immediate cost is more explicit modules and temporary shims. The benefit is one policy path shared by CLI and desktop, mechanical review of dependency direction, smaller integration interfaces, safer migration, and a clear point at which each compatibility bridge can be removed.

Any exception to this decision requires an architecture update, focused contract tests, security analysis, and human review before implementation.
