from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .config import Config
from .errors import PartialFailure, SafetyError
from .gitops import GitRunner, clone_and_verify
from .mirror import ensure_github_mirror
from .redaction import redact
from .validation import is_ssh_public_key, validate_repo_name


def _repository_identity_matches(
    repo: Mapping[str, Any], owner: str, name: str
) -> bool:
    if repo.get("name") != name or repo.get("full_name") != f"{owner}/{name}":
        return False
    owner_data = repo.get("owner")
    if owner_data is None:
        return True
    if not isinstance(owner_data, Mapping):
        return False
    return (owner_data.get("login") or owner_data.get("username")) == owner


@dataclass(frozen=True)
class BatchResult:
    examined: int
    ready: int
    would_change: int
    changed: int
    skipped: int
    blockers: tuple[str, ...]


class RepositoryManager:
    def __init__(
        self,
        config: Config,
        forgejo: Any,
        github: Any | None,
        git_runner: GitRunner | None,
        reporter: Callable[[str], None] = print,
        journal: Any | None = None,
    ):
        self.config = config
        self.forgejo = forgejo
        self.github = github
        self.git_runner = git_runner
        self.report = reporter
        self.journal = journal

    def _record(self, stage: str, state: str, *, detail: object | None = None) -> None:
        if self.journal is not None:
            self.journal.record(
                stage,
                state,
                preexisting=state == "existing"
                if state in {"created", "existing"}
                else None,
                detail=detail,
            )

    @staticmethod
    def _check_repo_metadata(
        repo: Mapping[str, Any],
        *,
        owner: str,
        name: str,
        private: bool,
        description: str,
        default_branch: str,
        label: str,
    ) -> None:
        if not _repository_identity_matches(repo, owner, name):
            raise SafetyError(
                f"{label} identity collision; refusing to reuse unrelated metadata"
            )
        if repo.get("private") is not private:
            expected = "private" if private else "public"
            actual = "private" if repo.get("private") else "public"
            raise SafetyError(
                f"{label} visibility collision: expected {expected}, found {actual}"
            )
        existing_description = repo.get("description")
        if existing_description != description:
            raise SafetyError(
                f"{label} description collision; refusing to overwrite it silently"
            )
        if repo.get("default_branch") != default_branch:
            raise SafetyError(
                f"{label} default-branch collision; expected {default_branch!r}"
            )

    def create(
        self,
        name: str,
        description: str,
        *,
        private: bool,
        with_github: bool,
        dry_run: bool,
    ) -> dict[str, str]:
        validate_repo_name(name)
        if not description.strip():
            raise SafetyError("description must not be empty")
        owner = self.config.forgejo_owner
        repo = self.forgejo.get_repo(owner, name)
        states: dict[str, str] = {}
        if repo is None:
            if dry_run:
                visibility = "private" if private else "public"
                self.report(
                    f"[DRY-RUN] would create {visibility} Forgejo repository "
                    f"{owner}/{name} with default branch {self.config.default_branch}"
                )
                states["forgejo_repo"] = "would-create"
            else:
                self._record("forgejo_repo", "attempted")
                try:
                    repo = self.forgejo.create_repo(
                        owner,
                        name,
                        description,
                        private=private,
                        default_branch=self.config.default_branch,
                    )
                except Exception as exc:
                    self._record("forgejo_repo", "failed", detail=exc)
                    raise PartialFailure(
                        f"Forgejo create request for {owner}/{name} failed after it was sent: "
                        f"{redact(exc)}. Its outcome is ambiguous; inspect the target owner, "
                        "retain any repository that exists, and rerun the same command."
                    ) from exc
                states["forgejo_repo"] = "created"
                self._record("forgejo_repo", "created")
                try:
                    self._check_repo_metadata(
                        repo,
                        owner=owner,
                        name=name,
                        private=private,
                        description=description,
                        default_branch=self.config.default_branch,
                        label=f"Forgejo {owner}/{name}",
                    )
                except Exception as exc:
                    raise PartialFailure(
                        f"Forgejo repository {owner}/{name} was created, but its returned "
                        f"metadata could not be verified: {redact(exc)}. The repository was "
                        "retained; inspect it and rerun the same command."
                    ) from exc
        else:
            self._check_repo_metadata(
                repo,
                owner=owner,
                name=name,
                private=private,
                description=description,
                default_branch=self.config.default_branch,
                label=f"Forgejo {owner}/{name}",
            )
            states["forgejo_repo"] = "existing"
            self._record("forgejo_repo", "existing")
            visibility = "private" if repo.get("private") else "public"
            self.report(
                f"[DRY-RUN] Forgejo {owner}/{name}: existing {visibility} "
                f"repository (default {repo.get('default_branch', 'unknown')}); no changes"
                if dry_run
                else f"Forgejo {owner}/{name}: existing {visibility} repository"
            )

        if dry_run:
            clone_path = self.config.projects_root / name
            self.report(
                f"[DRY-RUN] would verify clone {clone_path} from SSH alias "
                f"{self.config.ssh_alias}"
            )
            states["clone"] = "would-verify"
            if with_github:
                github_owner = self.config.github_owner_for(owner)
                if github_owner is None:
                    raise SafetyError(
                        f"no GitHub owner mapping for Forgejo owner {owner}"
                    )
                visibility = "private" if private else "public"
                self.report(
                    f"[DRY-RUN] would ensure {visibility} GitHub repository "
                    f"{github_owner}/{name}, disable Actions, install an isolated writable "
                    "deploy key, and verify the Forgejo push mirror"
                )
                states["github_mirror"] = "would-ensure"
            return states

        clone_path = self.config.projects_root / name
        clone_preexisting = clone_path.exists()
        try:
            default_branch = repo.get("default_branch") or self.config.default_branch
            states["clone"] = clone_and_verify(
                clone_path,
                ssh_alias=self.config.ssh_alias,
                owner=owner,
                name=name,
                default_branch=default_branch,
                runner=self.git_runner,
            )
            self._record("clone", states["clone"])
        except Exception as exc:
            self._record("clone", "failed", detail=exc)
            if states["forgejo_repo"] == "created" or (
                not clone_preexisting and clone_path.exists()
            ):
                raise PartialFailure(
                    f"Repository setup left a new Forgejo repository or local clone, but clone "
                    f"verification failed: {exc}. Retain the resources, fix SSH/local-path "
                    "state, consult the stage journal, and rerun the same command."
                ) from exc
            raise

        if with_github:
            if self.github is None:
                raise PartialFailure(
                    "Forgejo repository and clone are ready, but GitHub support is unavailable; "
                    "install/authenticate gh and rerun the same command with --github"
                )
            github_owner = self.config.github_owner_for(owner)
            if github_owner is None:
                raise SafetyError(f"no GitHub owner mapping for Forgejo owner {owner}")
            try:
                states.update(
                    ensure_github_mirror(
                        self.forgejo,
                        self.github,
                        forgejo_owner=owner,
                        github_owner=github_owner,
                        name=name,
                        description=description,
                        private=private,
                        mirror_interval=self.config.mirror_interval,
                        stage_callback=self._record,
                        github_host=self.config.github_host,
                    )
                )
            except PartialFailure:
                raise
            except Exception as exc:
                if any(value == "created" for value in states.values()):
                    raise PartialFailure(
                        f"repository setup stopped after durable stages {states}: {redact(exc)}. "
                        "Resources were retained; use the stage journal and rerun the same command."
                    ) from exc
                raise
        return states

    def _discovered_repositories(self) -> list[dict[str, Any]]:
        repositories: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = self.forgejo.list_repos(page=page, limit=50)
            if not batch:
                break
            repositories.extend(batch)
            page += 1
        return repositories

    def batch(
        self,
        *,
        dry_run: bool,
        owner_filter: str | None = None,
        repository_filters: set[str] | None = None,
    ) -> BatchResult:
        """Discover accessible Forgejo repos and fill missing GitHub mirrors."""
        examined = ready = would_change = changed = skipped = 0
        blockers: list[str] = []
        candidates: list[tuple[str, str, str, str]] = []
        matched_filters: set[str] = set()
        matched_owner = False

        for repo in self._discovered_repositories():
            owner_data = repo.get("owner") or {}
            owner = owner_data.get("login") or owner_data.get("username")
            name = repo.get("name")
            advertised_full_name = repo.get("full_name")
            if not isinstance(owner, str) or not isinstance(name, str):
                blockers.append(
                    "malformed Forgejo repository metadata lacks owner/name"
                )
                continue
            full_name = f"{owner}/{name}"
            if advertised_full_name != full_name:
                blockers.append(
                    f"{full_name}: Forgejo repository identity metadata is missing or inconsistent"
                )
                continue
            if owner_filter and owner != owner_filter:
                continue
            matched_owner = True
            if repository_filters and not (
                name in repository_filters or full_name in repository_filters
            ):
                continue
            if repository_filters:
                if name in repository_filters:
                    matched_filters.add(name)
                if full_name in repository_filters:
                    matched_filters.add(full_name)
            if full_name in self.config.skip_repositories:
                skipped += 1
                self.report(
                    f"[SKIP] {full_name}: configured skip; mirror left untouched"
                )
                continue

            examined += 1
            if repo.get("private") is not True:
                reason = (
                    f"{full_name}: public Forgejo repository is not eligible for private mirror policy"
                    if repo.get("private") is False
                    else f"{full_name}: Forgejo visibility metadata is missing or ambiguous"
                )
                blockers.append(reason)
                self.report(f"[BLOCKED] {reason}")
                continue
            permissions = repo.get("permissions")
            if (
                not isinstance(permissions, dict)
                or permissions.get("admin") is not True
            ):
                reason = f"{full_name}: push-mirror admin permission metadata is missing or denied"
                blockers.append(reason)
                self.report(f"[BLOCKED] {reason}")
                continue
            try:
                mirrors = self.forgejo.list_push_mirrors(owner, name)
            except Exception as exc:
                reason = f"{full_name}: {redact(exc)}"
                blockers.append(reason)
                self.report(f"[BLOCKED] {reason}")
                continue
            github_owner = self.config.github_owner_for(owner)
            if github_owner is None:
                reason = (
                    f"{full_name}: no GitHub owner mapping for Forgejo owner {owner}"
                )
                blockers.append(reason)
                self.report(f"[BLOCKED] {reason}")
                continue

            remote_address = f"git@{self.config.github_host}:{github_owner}/{name}.git"
            matching = [
                mirror
                for mirror in mirrors
                if mirror.get("remote_address") == remote_address
            ]
            if len(matching) > 1:
                reason = f"{full_name}: duplicate matching push mirrors require manual review"
                blockers.append(reason)
                self.report(f"[BLOCKED] {reason}")
                continue
            if self.github is None:
                reason = f"{full_name}: gh is unavailable or unauthenticated"
                blockers.append(reason)
                self.report(f"[BLOCKED] {reason}")
                continue

            description = repo.get("description") or "Forgejo push mirror"
            try:
                github_repo = self.github.get_repo(github_owner, name)
            except Exception as exc:
                reason = f"{full_name}: {redact(exc)}"
                blockers.append(reason)
                self.report(f"[BLOCKED] {reason}")
                continue

            if github_repo is not None:
                if not _repository_identity_matches(github_repo, github_owner, name):
                    reason = f"{full_name}: GitHub ownership collision for {github_owner}/{name}"
                    blockers.append(reason)
                    self.report(f"[BLOCKED] {reason}")
                    continue
                if github_repo.get("private") is not True:
                    reason = (
                        f"{full_name}: GitHub visibility collision; expected private"
                    )
                    blockers.append(reason)
                    self.report(f"[BLOCKED] {reason}")
                    continue
                if github_repo.get("description") != description:
                    reason = f"{full_name}: GitHub description collision"
                    blockers.append(reason)
                    self.report(f"[BLOCKED] {reason}")
                    continue
                github_permissions = github_repo.get("permissions")
                if (
                    not isinstance(github_permissions, Mapping)
                    or github_permissions.get("admin") is not True
                ):
                    reason = (
                        f"{full_name}: GitHub admin permission is missing or ambiguous"
                    )
                    blockers.append(reason)
                    self.report(f"[BLOCKED] {reason}")
                    continue

            mirror = matching[0] if matching else None
            if mirror is not None:
                mirror_errors: list[str] = []
                if mirror.get("use_ssh") is not True:
                    mirror_errors.append("use_ssh=true")
                if mirror.get("sync_on_commit") is not True:
                    mirror_errors.append("sync_on_commit=true")
                if not is_ssh_public_key(mirror.get("public_key")):
                    mirror_errors.append("generated public_key")
                if "last_error" not in mirror or mirror.get("last_error") != "":
                    mirror_errors.append("present empty last_error")
                if mirror_errors:
                    reason = f"{full_name}: existing mirror must have " + ", ".join(
                        mirror_errors
                    )
                    blockers.append(reason)
                    self.report(f"[BLOCKED] {reason}")
                    continue

            key_ready = False
            actions_ready = False
            if github_repo is not None and mirror is not None:
                try:
                    keys = self.github.list_deploy_keys(github_owner, name)
                    actions = self.github.get_actions_permissions(github_owner, name)
                except Exception as exc:
                    reason = f"{full_name}: {redact(exc)}"
                    blockers.append(reason)
                    self.report(f"[BLOCKED] {reason}")
                    continue
                if not isinstance(actions, dict) or not isinstance(
                    actions.get("enabled"), bool
                ):
                    reason = (
                        f"{full_name}: GitHub Actions permission state is ambiguous"
                    )
                    blockers.append(reason)
                    self.report(f"[BLOCKED] {reason}")
                    continue
                actions_ready = actions.get("enabled") is False
                title = f"Forgejo mirror: {owner}/{name}"
                title_matches = [key for key in keys if key.get("title") == title]
                exact_keys = [
                    key
                    for key in keys
                    if key.get("key") == mirror.get("public_key")
                    and key.get("read_only") is False
                ]
                if len(title_matches) > 1 or (
                    title_matches and title_matches[0] not in exact_keys
                ):
                    reason = f"{full_name}: deploy-key collision requires manual review"
                    blockers.append(reason)
                    self.report(f"[BLOCKED] {reason}")
                    continue
                key_ready = bool(exact_keys)

            if (
                github_repo is not None
                and mirror is not None
                and key_ready
                and actions_ready
            ):
                ready += 1
                self.report(f"[READY] {full_name}: private GitHub mirror is configured")
            else:
                would_change += 1
                candidates.append((owner, github_owner, name, description))
                self.report(
                    f"[DRY-RUN] {full_name}: would create or complete private GitHub "
                    f"{github_owner}/{name} with a deploy-key push mirror"
                )

        if owner_filter and not matched_owner:
            blockers.append(
                f"owner filter {owner_filter!r} matched no Forgejo repositories"
            )
        if repository_filters:
            for missing in sorted(repository_filters - matched_filters):
                blockers.append(
                    f"repository filter {missing!r} matched no Forgejo repository"
                )

        # Fail closed across the entire discovery set: never begin batch writes if
        # any selected repository failed its read-only inspection.
        if dry_run or blockers:
            return BatchResult(
                examined=examined,
                ready=ready,
                would_change=would_change,
                changed=0,
                skipped=skipped,
                blockers=tuple(blockers),
            )

        if self.github is None:
            raise SafetyError("gh became unavailable after batch preflight")
        github = self.github
        for owner, github_owner, name, description in candidates:
            full_name = f"{owner}/{name}"
            try:
                ensure_github_mirror(
                    self.forgejo,
                    github,
                    forgejo_owner=owner,
                    github_owner=github_owner,
                    name=name,
                    description=description,
                    private=True,
                    mirror_interval=self.config.mirror_interval,
                    github_host=self.config.github_host,
                    stage_callback=lambda stage, state: self._record(
                        f"{owner}--{name}--{stage}", state
                    ),
                )
            except PartialFailure:
                raise
            except Exception as exc:
                if changed:
                    raise PartialFailure(
                        f"mirror-all changed {changed} repository before {full_name} failed: "
                        f"{redact(exc)}. Rerun after correcting the reported collision."
                    ) from exc
                blockers.append(f"{full_name}: {redact(exc)}")
                break
            changed += 1
            would_change -= 1
            self.report(f"[CHANGED] {full_name}: private GitHub mirror verified")

        return BatchResult(
            examined=examined,
            ready=ready,
            would_change=would_change,
            changed=changed,
            skipped=skipped,
            blockers=tuple(blockers),
        )
