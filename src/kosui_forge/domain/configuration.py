"""Immutable non-secret repository policy and deterministic validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from urllib.parse import urlsplit

from .errors import ConfigError
from .repository import validate_repo_name

_INTERVAL = re.compile(r"^[1-9][0-9]*(?:m|h|d)(?:[0-9]+m)?(?:[0-9]+s)?$")
_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?$")
_HOST = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)
_SSH_ALIAS = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,253}[A-Za-z0-9])?$")
_ENVIRONMENT_VARIABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class Config:
    """Validated, non-secret policy consumed by repository use cases."""

    forgejo_url: str
    forgejo_owner: str
    github_owner: str
    projects_root: Path
    ssh_alias: str
    github_host: str = "github.com"
    default_branch: str = "main"
    forgejo_token_env: str = "FORGEJO_TOKEN"
    owner_map: dict[str, str] = field(default_factory=dict)
    skip_repositories: tuple[str, ...] = ()
    authentication_mode: str = "per-repository-deploy-key"
    privacy_policy: str = "private"
    sync_on_commit: bool = True
    mirror_interval: str = "8h"

    def github_owner_for(self, forgejo_owner: str) -> str | None:
        if forgejo_owner in self.owner_map:
            return self.owner_map[forgejo_owner]
        if forgejo_owner == self.forgejo_owner:
            return self.github_owner
        return None


def validate_forgejo_url(value: str) -> str:
    """Normalize a credential-free absolute Forgejo HTTPS URL."""
    forgejo_url = value.rstrip("/")
    parsed = urlsplit(forgejo_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ConfigError("forgejo_url must be an absolute HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ConfigError("forgejo_url must not contain embedded credentials")
    if parsed.query or parsed.fragment:
        raise ConfigError("forgejo_url must not contain a query string or fragment")
    return forgejo_url


def validate_config(config: Config) -> None:
    """Fail closed when non-secret repository policy is unsafe or ambiguous."""
    validate_forgejo_url(config.forgejo_url)
    for key, value in (
        ("forgejo_owner", config.forgejo_owner),
        ("github_owner", config.github_owner),
        ("github_host", config.github_host),
        ("ssh_alias", config.ssh_alias),
        ("default_branch", config.default_branch),
        ("forgejo_token_env", config.forgejo_token_env),
    ):
        if not value.strip():
            raise ConfigError(f"configuration field {key!r} must be a non-empty string")
    for key, value in (
        ("forgejo_owner", config.forgejo_owner),
        ("github_owner", config.github_owner),
    ):
        if not _OWNER.fullmatch(value):
            raise ConfigError(f"configuration field {key!r} is not a safe owner name")
    if not _HOST.fullmatch(config.github_host):
        raise ConfigError("configuration field 'github_host' must be a bare hostname")
    if not _SSH_ALIAS.fullmatch(config.ssh_alias):
        raise ConfigError(
            "configuration field 'ssh_alias' is not a safe SSH host alias"
        )
    if not _ENVIRONMENT_VARIABLE.fullmatch(config.forgejo_token_env):
        raise ConfigError(
            "configuration field 'forgejo_token_env' must be an environment-variable name"
        )
    for forgejo_owner, github_owner in config.owner_map.items():
        if not _OWNER.fullmatch(forgejo_owner) or not _OWNER.fullmatch(github_owner):
            raise ConfigError("owner_map contains an unsafe owner name")
    for full_name in config.skip_repositories:
        if full_name.count("/") != 1:
            raise ConfigError("skip_repositories entries must be owner/name")
        owner, name = full_name.split("/", 1)
        if not _OWNER.fullmatch(owner):
            raise ConfigError("skip_repositories contains an unsafe owner name")
        try:
            validate_repo_name(name)
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
    if not config.projects_root.is_absolute():
        raise ConfigError("projects_root must be an absolute path")
    if config.authentication_mode != "per-repository-deploy-key":
        raise ConfigError("authentication_mode must be per-repository-deploy-key")
    if config.privacy_policy != "private":
        raise ConfigError("privacy_policy is fixed to private")
    if config.sync_on_commit is not True:
        raise ConfigError("sync_on_commit is fixed to true")
    if not _INTERVAL.fullmatch(config.mirror_interval):
        raise ConfigError("mirror_interval must look like 10m, 8h, or 1d0m")
