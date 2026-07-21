from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import platform
import re
import tempfile
import tomllib
from collections.abc import Mapping
from urllib.parse import urlsplit

from .errors import ConfigError
from .validation import validate_repo_name

_SUPPORTED_KEYS = {
    "forgejo_url",
    "forgejo_owner",
    "github_owner",
    "github_host",
    "projects_root",
    "ssh_alias",
    "default_branch",
    "forgejo_token_env",
    "owner_map",
    "skip_repositories",
    "authentication_mode",
    "privacy_policy",
    "sync_on_commit",
    "mirror_interval",
}
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


def default_config_path(
    *,
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    system = system or platform.system()
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else home
    if system == "Windows":
        base = Path(environ.get("APPDATA", str(home / "AppData/Roaming")))
    elif system == "Darwin":
        base = home / "Library/Application Support"
    else:
        base = Path(environ.get("XDG_CONFIG_HOME", str(home / ".config")))
    return base / "repo-bootstrap/config.toml"


def _required_string(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"configuration field {key!r} must be a non-empty string")
    return value.strip()


def _validated_url(value: str) -> str:
    forgejo_url = value.rstrip("/")
    parsed = urlsplit(forgejo_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ConfigError("forgejo_url must be an absolute HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ConfigError("forgejo_url must not contain embedded credentials")
    if parsed.query or parsed.fragment:
        raise ConfigError("forgejo_url must not contain a query string or fragment")
    return forgejo_url


def load_config(path: Path) -> Config:
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc

    unsupported = sorted(set(data) - _SUPPORTED_KEYS)
    if unsupported:
        raise ConfigError(
            f"unsupported configuration field(s): {', '.join(unsupported)}"
        )

    forgejo_url = _validated_url(_required_string(data, "forgejo_url"))
    owner_map = data.get("owner_map", {})
    if not isinstance(owner_map, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in owner_map.items()
    ):
        raise ConfigError(
            "owner_map must map Forgejo owner names to GitHub owner names"
        )
    skip_repositories = data.get("skip_repositories", [])
    if not isinstance(skip_repositories, list) or not all(
        isinstance(item, str) and "/" in item for item in skip_repositories
    ):
        raise ConfigError("skip_repositories must be a list of owner/name strings")

    root = Path(_required_string(data, "projects_root")).expanduser()
    if not root.is_absolute():
        raise ConfigError("projects_root must be an absolute path")
    auth_mode = str(data.get("authentication_mode", "per-repository-deploy-key"))
    if auth_mode != "per-repository-deploy-key":
        raise ConfigError("authentication_mode must be per-repository-deploy-key")
    privacy = str(data.get("privacy_policy", "private"))
    if privacy != "private":
        raise ConfigError("privacy_policy is fixed to private")
    sync_on_commit = data.get("sync_on_commit", True)
    if sync_on_commit is not True:
        raise ConfigError("sync_on_commit is fixed to true")
    interval = str(data.get("mirror_interval", "8h"))
    if not _INTERVAL.fullmatch(interval):
        raise ConfigError("mirror_interval must look like 10m, 8h0m, or 1d0m")

    config = Config(
        forgejo_url=forgejo_url,
        forgejo_owner=_required_string(data, "forgejo_owner"),
        github_owner=_required_string(data, "github_owner"),
        github_host=str(data.get("github_host", "github.com")),
        projects_root=root,
        ssh_alias=_required_string(data, "ssh_alias"),
        default_branch=str(data.get("default_branch", "main")),
        forgejo_token_env=str(data.get("forgejo_token_env", "FORGEJO_TOKEN")),
        owner_map=dict(owner_map),
        skip_repositories=tuple(skip_repositories),
        authentication_mode=auth_mode,
        privacy_policy=privacy,
        sync_on_commit=True,
        mirror_interval=interval,
    )
    validate_config(config)
    return config


def validate_config(config: Config) -> None:
    _validated_url(config.forgejo_url)
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


def serialize_config(config: Config) -> str:
    validate_config(config)
    lines = [
        "# Non-secret global policy. Credentials come from protected stores/environment.",
        f"forgejo_url = {json.dumps(config.forgejo_url.rstrip('/'))}",
        f"forgejo_owner = {json.dumps(config.forgejo_owner)}",
        f"github_owner = {json.dumps(config.github_owner)}",
        f"github_host = {json.dumps(config.github_host)}",
        f"projects_root = {json.dumps(str(config.projects_root.expanduser()))}",
        f"ssh_alias = {json.dumps(config.ssh_alias)}",
        f"default_branch = {json.dumps(config.default_branch)}",
        f"forgejo_token_env = {json.dumps(config.forgejo_token_env)}",
        'authentication_mode = "per-repository-deploy-key"',
        'privacy_policy = "private"',
        "sync_on_commit = true",
        f"mirror_interval = {json.dumps(config.mirror_interval)}",
    ]
    if config.skip_repositories:
        values = ", ".join(json.dumps(value) for value in config.skip_repositories)
        lines.append(f"skip_repositories = [{values}]")
    lines.extend(("", "[owner_map]"))
    mappings = config.owner_map or {config.forgejo_owner: config.github_owner}
    for owner, github_owner in sorted(mappings.items()):
        lines.append(f"{json.dumps(owner)} = {json.dumps(github_owner)}")
    return "\n".join(lines) + "\n"


def write_config(
    path: Path,
    config: Config | None = None,
    *,
    force: bool = False,
    forgejo_url: str | None = None,
    forgejo_owner: str | None = None,
    github_owner: str | None = None,
    github_host: str = "github.com",
    projects_root: Path | None = None,
    ssh_alias: str | None = None,
    mirror_interval: str = "8h",
) -> None:
    if config is None:
        required = {
            "forgejo_url": forgejo_url,
            "forgejo_owner": forgejo_owner,
            "github_owner": github_owner,
            "projects_root": projects_root,
            "ssh_alias": ssh_alias,
        }
        missing = [key for key, value in required.items() if value is None]
        if missing:
            raise ConfigError(
                f"missing configuration value(s): {', '.join(sorted(missing))}"
            )
        assert forgejo_url is not None
        assert forgejo_owner is not None
        assert github_owner is not None
        assert projects_root is not None
        assert ssh_alias is not None
        config = Config(
            forgejo_url=forgejo_url,
            forgejo_owner=forgejo_owner,
            github_owner=github_owner,
            github_host=github_host,
            projects_root=projects_root.expanduser().absolute(),
            ssh_alias=ssh_alias,
            owner_map={forgejo_owner: github_owner},
            mirror_interval=mirror_interval,
        )
    content = serialize_config(config)
    if path.exists() and not force:
        raise FileExistsError(f"configuration file already exists: {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent, text=True
    )
    temporary = Path(temporary_name)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
