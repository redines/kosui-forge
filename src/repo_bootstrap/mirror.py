from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from .errors import PartialFailure, SafetyError
from .redaction import redact
from .validation import is_ssh_public_key


class ForgejoMirrors(Protocol):
    def list_push_mirrors(self, owner: str, name: str) -> list[dict[str, Any]]: ...

    def create_push_mirror(
        self,
        owner: str,
        name: str,
        remote_address: str,
        *,
        interval: str,
    ) -> dict[str, Any]: ...

    def sync_push_mirrors(self, owner: str, name: str) -> None: ...


class GitHubRepositories(Protocol):
    def get_repo(self, owner: str, name: str) -> dict[str, Any] | None: ...

    def create_repo(
        self, owner: str, name: str, description: str, *, private: bool
    ) -> dict[str, Any]: ...

    def disable_actions(self, owner: str, name: str) -> None: ...

    def get_actions_permissions(self, owner: str, name: str) -> dict[str, Any]: ...

    def list_deploy_keys(self, owner: str, name: str) -> list[dict[str, Any]]: ...

    def add_deploy_key(
        self, owner: str, name: str, title: str, public_key: str
    ) -> dict[str, Any]: ...


def _matching_mirror(
    mirrors: list[dict[str, Any]], address: str
) -> dict[str, Any] | None:
    matches = [mirror for mirror in mirrors if mirror.get("remote_address") == address]
    if len(matches) > 1:
        raise SafetyError(f"multiple Forgejo push mirrors already target {address}")
    return matches[0] if matches else None


def _validated_public_key(mirror: dict[str, Any]) -> str:
    key = mirror.get("public_key")
    if not is_ssh_public_key(key):
        raise PartialFailure(
            "Forgejo mirror exists but its generated SSH public key is unavailable; "
            "inspect the mirror in Forgejo, then rerun after confirming use_ssh is enabled"
        )
    return key.strip()


def _validate_github_repository(
    repo: Mapping[str, Any],
    *,
    owner: str,
    name: str,
    description: str,
    private: bool,
) -> None:
    owner_data = repo.get("owner")
    owner_matches = owner_data is None or (
        isinstance(owner_data, Mapping)
        and (owner_data.get("login") or owner_data.get("username")) == owner
    )
    if (
        repo.get("name") != name
        or repo.get("full_name") != f"{owner}/{name}"
        or not owner_matches
    ):
        raise SafetyError(
            f"GitHub identity collision for {owner}/{name}; refusing to modify it"
        )
    if repo.get("private") is not private:
        expected = "private" if private else "public"
        actual = "private" if repo.get("private") else "public"
        raise SafetyError(
            f"GitHub visibility collision for {owner}/{name}: "
            f"expected {expected}, found {actual}; refusing to modify it"
        )
    if repo.get("description") != description:
        raise SafetyError(
            f"GitHub description collision for {owner}/{name}; "
            "refusing to overwrite it silently"
        )
    permissions = repo.get("permissions")
    if not isinstance(permissions, Mapping) or permissions.get("admin") is not True:
        raise SafetyError(
            f"GitHub admin permission for {owner}/{name} is missing or ambiguous"
        )


def ensure_github_mirror(
    forgejo: ForgejoMirrors,
    github: GitHubRepositories,
    *,
    forgejo_owner: str,
    github_owner: str,
    name: str,
    description: str,
    private: bool,
    mirror_interval: str = "8h",
    verify_attempts: int = 5,
    sleeper=time.sleep,
    github_host: str = "github.com",
    stage_callback: Callable[..., None] | None = None,
) -> dict[str, str]:
    """Idempotently provision a GitHub repo, writable deploy key, and push mirror."""
    states: dict[str, str] = {}

    def record(stage: str, state: str) -> None:
        if stage_callback is not None:
            stage_callback(stage, state)

    github_repo = github.get_repo(github_owner, name)
    github_created = github_repo is None
    if github_created:
        record("github_repo", "attempted")
        try:
            github.create_repo(github_owner, name, description, private=private)
        except Exception as exc:
            record("github_repo", "failed")
            raise PartialFailure(
                f"GitHub create request for {github_owner}/{name} failed after it was sent: "
                f"{redact(exc)}. Its outcome is ambiguous; inspect the target owner, retain "
                "any repository that exists, and rerun the same command."
            ) from exc
        states["github_repo"] = "created"
        record("github_repo", "created")
        try:
            github_repo = github.get_repo(github_owner, name)
        except Exception as exc:
            raise PartialFailure(
                f"GitHub repository {github_owner}/{name} was created but read-back "
                f"verification failed: {redact(exc)}. It was retained for inspection and "
                "safe resume."
            ) from exc
        if github_repo is None:
            raise PartialFailure(
                f"GitHub repository {github_owner}/{name} was created but could not be read "
                "back for verification; it was retained for inspection and safe resume"
            )
    else:
        states["github_repo"] = "existing"
        record("github_repo", "existing")

    if github_repo is None:
        raise PartialFailure(
            f"GitHub repository {github_owner}/{name} could not be verified after setup"
        )
    try:
        _validate_github_repository(
            github_repo,
            owner=github_owner,
            name=name,
            description=description,
            private=private,
        )
    except SafetyError as exc:
        if github_created:
            raise PartialFailure(
                f"GitHub repository {github_owner}/{name} was created, but verification "
                f"failed: {exc}. It was retained; inspect it and rerun only after resolving "
                "the identity, visibility, description, or admin-permission mismatch."
            ) from exc
        raise

    remote_address = f"git@{github_host}:{github_owner}/{name}.git"
    pending_write: str | None = None
    durable_updates: list[str] = []
    try:
        mirrors = forgejo.list_push_mirrors(forgejo_owner, name)
        mirror = _matching_mirror(mirrors, remote_address)
        if mirror is None:
            pending_write = "Forgejo push-mirror creation request"
            record("push_mirror", "attempted")
            mirror = forgejo.create_push_mirror(
                forgejo_owner, name, remote_address, interval=mirror_interval
            )
            pending_write = None
            states["push_mirror"] = "created"
            record("push_mirror", "created")
        else:
            states["push_mirror"] = "existing"
            record("push_mirror", "existing")
        if mirror.get("use_ssh") is not True:
            raise SafetyError(
                f"push-mirror collision for {forgejo_owner}/{name}: "
                "the existing mirror does not have use_ssh=true"
            )
        if mirror.get("sync_on_commit") is not True:
            raise SafetyError(
                f"push-mirror collision for {forgejo_owner}/{name}: "
                "the existing mirror does not have sync_on_commit=true"
            )

        public_key = _validated_public_key(mirror)
        title = f"Forgejo mirror: {forgejo_owner}/{name}"
        keys = github.list_deploy_keys(github_owner, name)
        title_matches = [key for key in keys if key.get("title") == title]
        if len(title_matches) > 1:
            raise SafetyError(f"multiple GitHub deploy keys use title {title!r}")
        if title_matches:
            existing = title_matches[0]
            if (
                existing.get("key") != public_key
                or existing.get("read_only") is not False
            ):
                raise SafetyError(
                    f"deploy-key collision on {github_owner}/{name}: title {title!r} "
                    "already exists with different key material or permissions"
                )
            states["deploy_key"] = "existing"
            record("deploy_key", "existing")
        else:
            same_keys = [
                key
                for key in keys
                if key.get("key") == public_key and key.get("read_only") is False
            ]
            if same_keys:
                states["deploy_key"] = "existing"
                record("deploy_key", "existing")
            else:
                pending_write = "GitHub deploy-key creation request"
                record("deploy_key", "attempted")
                github.add_deploy_key(github_owner, name, title, public_key)
                pending_write = None
                states["deploy_key"] = "created"
                record("deploy_key", "created")

        # Defer this write until repository, mirror, and key collisions have been
        # checked, minimizing state changes when a safe-resume check fails.
        pending_write = "GitHub Actions update request"
        record("github_actions", "attempted")
        github.disable_actions(github_owner, name)
        pending_write = None
        durable_updates.append("GitHub Actions setting")
        record("github_actions", "updated")
        actions = github.get_actions_permissions(github_owner, name)
        if not isinstance(actions, dict) or actions.get("enabled") is not False:
            raise PartialFailure(
                f"GitHub Actions could not be verified disabled for {github_owner}/{name}; "
                "the repository, mirror, and deploy key were retained for safe resume"
            )
        states["github_actions"] = "disabled"
        record("github_actions", "disabled")

        pending_write = "Forgejo mirror-sync request"
        record("mirror_sync", "attempted")
        forgejo.sync_push_mirrors(forgejo_owner, name)
        pending_write = None
        durable_updates.append("Forgejo mirror synchronization")
        states["mirror_sync"] = "triggered"
        record("mirror_sync", "triggered")
        last_error = "mirror status was not returned"
        for attempt in range(max(1, verify_attempts)):
            current = _matching_mirror(
                forgejo.list_push_mirrors(forgejo_owner, name), remote_address
            )
            if current is None:
                last_error = "mirror disappeared after synchronization"
            else:
                if "last_error" not in current:
                    last_error = "mirror status omitted last_error"
                else:
                    reported_error = current.get("last_error")
                    last_error = (
                        reported_error
                        if isinstance(reported_error, str)
                        else "mirror status returned a non-string last_error"
                    )
                if last_error == "":
                    states["mirror"] = "verified"
                    record("mirror", "verified")
                    return states
            if attempt + 1 < verify_attempts:
                sleeper(1)
        raise PartialFailure(
            "Forgejo push-mirror synchronization did not verify cleanly: "
            f"{redact(last_error)}. The repositories and deploy key were retained; "
            "correct connectivity or key permissions and rerun the same command."
        )
    except PartialFailure:
        raise
    except Exception as exc:
        changed = [
            key.replace("_", " ") for key, value in states.items() if value == "created"
        ]
        changed.extend(durable_updates)
        if pending_write is not None:
            changed.append(f"{pending_write} (ambiguous outcome)")
        changed = list(dict.fromkeys(changed))
        if changed:
            raise PartialFailure(
                f"Mirror setup stopped after creating or updating {', '.join(changed)}: "
                f"{redact(exc)}. The GitHub repository, Forgejo mirror, and deploy key "
                "were retained as applicable; correct the error and rerun the same command."
            ) from exc
        raise
