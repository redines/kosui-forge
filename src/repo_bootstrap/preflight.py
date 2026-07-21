from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

from .config import Config, validate_config
from .gitops import forgejo_ssh_url
from .redaction import redact
from .validation import is_ssh_public_key, validate_repo_name


@dataclass(frozen=True)
class ToolResult:
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[Sequence[str]], ToolResult]


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    guidance: str = ""


CheckReporter = Callable[[CheckResult], None]


@dataclass(frozen=True)
class PreflightReport:
    checks: tuple[CheckResult, ...]
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return (
            not self.cancelled
            and bool(self.checks)
            and all(check.ok for check in self.checks)
        )

    def render(self) -> str:
        lines: list[str] = []
        for check in self.checks:
            status = "PASS" if check.ok else "FAIL"
            line = f"[{status}] {check.name}: {redact(check.detail)}"
            if not check.ok and check.guidance:
                line += f"; {redact(check.guidance)}"
            lines.append(line)
        return "\n".join(lines)


class _PreflightCancelled(Exception):
    def __init__(self, checks: tuple[CheckResult, ...]) -> None:
        super().__init__("preflight cancellation requested")
        self.checks = checks


class _CheckCollector(list[CheckResult]):
    def __init__(
        self,
        reporter: CheckReporter | None,
        cancellation_requested: Callable[[], bool] | None,
    ) -> None:
        super().__init__()
        self._reporter = reporter
        self._cancellation_requested = cancellation_requested

    def append(self, check: CheckResult) -> None:
        super().append(check)
        if self._reporter is not None:
            self._reporter(check)
        if self._cancellation_requested is not None and self._cancellation_requested():
            raise _PreflightCancelled(tuple(self))


def _subprocess_runner(args: Sequence[str]) -> ToolResult:
    try:
        result = subprocess.run(list(args), text=True, capture_output=True, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"{args[0]} is not installed or not available on PATH"
        ) from exc
    return ToolResult(result.returncode, result.stdout, result.stderr)


def _platform_family(platform_name: str) -> str:
    lowered = platform_name.lower()
    if lowered.startswith("win"):
        return "windows"
    if lowered in {"darwin", "macos"}:
        return "macos"
    if lowered.startswith("linux"):
        return "linux"
    return lowered


def _install_guidance(executable: str, platform_name: str) -> str:
    family = _platform_family(platform_name)
    if family == "windows":
        packages = {
            "git": "Git.Git",
            "gh": "GitHub.cli",
            "ssh": "Microsoft.OpenSSH.Beta",
        }
        return (
            f"install with winget install --id {packages[executable]} and rerun doctor"
        )
    if family == "macos":
        package = "openssh" if executable == "ssh" else executable
        return f"install with brew install {package} and rerun doctor"
    return f"install {executable} with the platform package manager and rerun doctor"


def _parse_major(version: object) -> int | None:
    match = re.search(r"(?<!\d)(\d+)(?:\.\d+)+", str(version))
    return int(match.group(1)) if match else None


def _login(value: Mapping[str, Any]) -> str | None:
    login = value.get("login") or value.get("username")
    return login if isinstance(login, str) else None


def _repo_identity_ok(repo: Mapping[str, Any], owner: str, name: str) -> bool:
    if repo.get("full_name") != f"{owner}/{name}" or repo.get("name") != name:
        return False
    owner_data = repo.get("owner")
    if owner_data is not None:
        if not isinstance(owner_data, Mapping) or _login(owner_data) != owner:
            return False
    return True


def _existing_parent(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def _forgejo_owner_access(
    forgejo: Any, owner: str, user: Mapping[str, Any]
) -> dict[str, bool]:
    if hasattr(forgejo, "get_owner_access"):
        return dict(forgejo.get_owner_access(owner))
    if _login(user) == owner or user.get("is_admin") is True:
        return {"exists": True, "can_create": True, "can_admin": True}
    organization = forgejo.get_org(owner)
    memberships = forgejo.list_user_orgs()
    member = any(_login(item) == owner for item in memberships)
    return {
        "exists": organization is not None,
        "can_create": bool(organization and member),
        "can_admin": bool(organization and member),
    }


def _github_owner_access(
    github: Any, owner: str, user: Mapping[str, Any]
) -> dict[str, bool]:
    if hasattr(github, "get_owner_access"):
        return dict(github.get_owner_access(owner))
    if _login(user) == owner:
        return {"exists": True, "can_create": True, "can_admin": True}
    owner_data = github.get_owner(owner)
    membership = github.get_membership(owner) if owner_data else None
    active = membership is not None and membership.get("state") == "active"
    admin = membership is not None and active and membership.get("role") == "admin"
    can_create = bool(
        admin
        or (
            active
            and isinstance(owner_data, Mapping)
            and owner_data.get("members_can_create_repositories") is True
        )
    )
    return {
        "exists": owner_data is not None,
        "can_create": can_create,
        "can_admin": admin,
    }


def _run_preflight(
    config: Config,
    forgejo: Any,
    github: Any,
    *,
    token_present: bool,
    name: str | None,
    description: str | None,
    private: bool,
    with_github: bool,
    command_runner: CommandRunner | None = None,
    which: Callable[[str], str | None] = shutil.which,
    platform_name: str = sys.platform,
    reporter: CheckReporter | None = None,
    cancellation_requested: Callable[[], bool] | None = None,
) -> PreflightReport:
    """Run a comprehensive read-only prerequisite and collision inspection."""
    run = command_runner or _subprocess_runner
    checks = _CheckCollector(reporter, cancellation_requested)

    family = _platform_family(platform_name)
    platform_ok = family in {"linux", "macos", "windows"}
    checks.append(
        CheckResult(
            "runtime-platform",
            platform_ok and sys.version_info >= (3, 11),
            f"Python {sys.version_info.major}.{sys.version_info.minor} on {family}",
            "use Python 3.11+ on Linux, macOS, or Windows",
        )
    )
    try:
        validate_config(config)
    except Exception as exc:
        checks.append(
            CheckResult(
                "global-config", False, str(exc), "run repo-bootstrap configure"
            )
        )
    else:
        checks.append(
            CheckResult("global-config", True, "private deploy-key policy is valid")
        )

    checks.append(
        CheckResult(
            "forgejo-credential",
            token_present,
            f"credential source {config.forgejo_token_env} is {'present' if token_present else 'missing'}",
            f"load {config.forgejo_token_env} from a protected environment or credential source",
        )
    )

    available: dict[str, bool] = {}
    version_commands = {
        "git": ["git", "--version"],
        "gh": ["gh", "--version"],
        "ssh": ["ssh", "-V"],
    }
    for executable, command in version_commands.items():
        present = which(executable) is not None
        available[executable] = present
        if not present:
            checks.append(
                CheckResult(
                    f"tool-{executable}",
                    False,
                    f"{executable} is not available on PATH",
                    _install_guidance(executable, platform_name),
                )
            )
            continue
        try:
            result = run(command)
            output = (result.stdout or result.stderr).strip()
            parseable = result.returncode == 0 and _parse_major(output) is not None
            checks.append(
                CheckResult(
                    f"tool-{executable}",
                    parseable,
                    output or f"{executable} returned no version",
                    _install_guidance(executable, platform_name),
                )
            )
        except Exception as exc:
            checks.append(
                CheckResult(
                    f"tool-{executable}",
                    False,
                    str(exc),
                    _install_guidance(executable, platform_name),
                )
            )

    if available.get("gh"):
        try:
            result = run(["gh", "auth", "status", "--hostname", config.github_host])
            checks.append(
                CheckResult(
                    "gh-auth",
                    result.returncode == 0,
                    (result.stdout or result.stderr).strip()
                    or "no authentication status",
                    f"run gh auth login --hostname {config.github_host}",
                )
            )
        except Exception as exc:
            checks.append(
                CheckResult(
                    "gh-auth",
                    False,
                    str(exc),
                    f"run gh auth login --hostname {config.github_host}",
                )
            )

    if available.get("ssh"):
        try:
            result = run(["ssh", "-G", config.ssh_alias])
            output = result.stdout.lower()
            usable = (
                result.returncode == 0
                and "hostname " in output
                and "user " in output
                and "%" not in output
            )
            checks.append(
                CheckResult(
                    "forgejo-ssh-alias",
                    usable,
                    f"ssh -G resolved {config.ssh_alias}"
                    if usable
                    else (result.stderr or result.stdout).strip(),
                    "add a usable Host entry to the user SSH config and verify ssh -G",
                )
            )
        except Exception as exc:
            checks.append(
                CheckResult("forgejo-ssh-alias", False, str(exc), "fix the SSH alias")
            )

    root = config.projects_root
    parent = _existing_parent(root)
    root_ok = (
        (not root.exists() or root.is_dir())
        and parent.is_dir()
        and os.access(parent if not root.exists() else root, os.W_OK | os.X_OK)
    )
    checks.append(
        CheckResult(
            "projects-root",
            root_ok,
            f"{root} exists and is writable"
            if root.exists() and root_ok
            else f"{root} can be safely created"
            if root_ok
            else f"{root} is not a writable directory",
            "choose an absolute projects root with a writable existing parent",
        )
    )

    if name is not None:
        try:
            validate_repo_name(name)
            if description is not None and not description.strip():
                raise ValueError("description must not be empty")
        except Exception as exc:
            checks.append(
                CheckResult(
                    "repository-input",
                    False,
                    str(exc),
                    "use a safe name and explicit description",
                )
            )
        else:
            mapped = config.github_owner_for(config.forgejo_owner)
            checks.append(
                CheckResult(
                    "repository-input",
                    mapped == config.github_owner,
                    f"name and owner mapping resolve to {config.github_owner}/{name}",
                    "add an explicit owner_map entry",
                )
            )
            destination = root / name
            if destination.exists():
                local_ok = destination.is_dir() and (destination / ".git").is_dir()
                detail = (
                    f"existing Git working tree at {destination}"
                    if local_ok
                    else f"local path collision: {destination} is not a Git working tree"
                )
                if local_ok and available.get("git"):
                    expected = forgejo_ssh_url(
                        config.ssh_alias, config.forgejo_owner, name
                    )
                    remote = run(
                        ["git", "-C", str(destination), "remote", "get-url", "origin"]
                    )
                    branch = run(
                        ["git", "-C", str(destination), "branch", "--show-current"]
                    )
                    local_ok = (
                        remote.returncode == 0
                        and remote.stdout.strip() == expected
                        and branch.returncode == 0
                        and branch.stdout.strip() == config.default_branch
                    )
                    if not local_ok:
                        detail = "local clone origin or default branch collision"
                checks.append(
                    CheckResult(
                        "local-destination",
                        local_ok,
                        detail,
                        "move the unrelated path or correct the clone manually",
                    )
                )
            else:
                checks.append(
                    CheckResult(
                        "local-destination",
                        True,
                        f"destination {destination} is available",
                    )
                )

    forgejo_user: Mapping[str, Any] | None = None
    forgejo_version_ok = False
    try:
        version_method = getattr(forgejo, "version", None) or getattr(
            forgejo, "get_version"
        )
        version_data = version_method()
        version_text = (
            version_data.get("version")
            if isinstance(version_data, Mapping)
            else version_data
        )
        version_major = _parse_major(version_text)
        forgejo_version_ok = version_major is not None and version_major >= 11
        checks.append(
            CheckResult(
                "forgejo-mirror-api",
                forgejo_version_ok,
                f"Forgejo version {version_text}",
                "Forgejo 11 or newer with the push-mirror API is required",
            )
        )
        authenticated_forgejo_user: Mapping[str, Any] = forgejo.authenticated_user()
        forgejo_user = authenticated_forgejo_user
        checks.append(
            CheckResult(
                "forgejo-auth",
                True,
                f"authenticated as {_login(authenticated_forgejo_user) or 'unknown'}",
            )
        )
    except Exception as exc:
        checks.append(
            CheckResult(
                "forgejo-auth",
                False,
                str(exc),
                "verify the HTTPS URL, token, and network",
            )
        )

    if forgejo_user is not None:
        try:
            access = _forgejo_owner_access(forgejo, config.forgejo_owner, forgejo_user)
            checks.append(
                CheckResult(
                    "forgejo-owner",
                    bool(access.get("exists")),
                    f"target owner {config.forgejo_owner} exists",
                    "correct forgejo_owner",
                )
            )
            checks.append(
                CheckResult(
                    "forgejo-create-permission",
                    bool(access.get("can_create")),
                    "repository-create permission is available"
                    if access.get("can_create")
                    else "repository-create permission is missing",
                    "grant repository creation permission",
                )
            )
            checks.append(
                CheckResult(
                    "forgejo-mirror-admin",
                    bool(access.get("can_admin")),
                    "push-mirror admin permission is available"
                    if access.get("can_admin")
                    else "push-mirror admin permission is missing",
                    "grant owner/repository admin permission",
                )
            )
        except Exception as exc:
            checks.append(
                CheckResult(
                    "forgejo-owner",
                    False,
                    str(exc),
                    "verify owner membership and permissions",
                )
            )

    github_user: Mapping[str, Any] | None = None
    try:
        authenticated_github_user: Mapping[str, Any] = github.authenticated_user()
        github_user = authenticated_github_user
        checks.append(
            CheckResult(
                "github-api-user",
                True,
                f"authenticated as {_login(authenticated_github_user) or 'unknown'} on {config.github_host}",
            )
        )
    except Exception as exc:
        checks.append(
            CheckResult(
                "github-api-user",
                False,
                str(exc),
                f"run gh auth login --hostname {config.github_host}",
            )
        )
    if github_user is not None:
        try:
            access = _github_owner_access(github, config.github_owner, github_user)
            owner_ok = bool(
                access.get("exists")
                and access.get("can_create")
                and access.get("can_admin")
            )
            checks.append(
                CheckResult(
                    "github-owner",
                    owner_ok,
                    f"GitHub owner {config.github_owner} exists with create/admin permission"
                    if owner_ok
                    else f"GitHub owner {config.github_owner} lacks create/admin permission",
                    "grant repository create/admin permission or correct github_owner",
                )
            )
        except Exception as exc:
            checks.append(
                CheckResult(
                    "github-owner",
                    False,
                    str(exc),
                    "verify GitHub owner membership and policy",
                )
            )

    forgejo_repo: Mapping[str, Any] | None = None
    github_repo: Mapping[str, Any] | None = None
    mirror: Mapping[str, Any] | None = None
    if name is not None and forgejo_user is not None:
        try:
            forgejo_repo = forgejo.get_repo(config.forgejo_owner, name)
            if forgejo_repo is None:
                checks.append(
                    CheckResult(
                        "forgejo-repository-collision",
                        True,
                        "target repository does not yet exist",
                    )
                )
            else:
                identity_ok = _repo_identity_ok(
                    forgejo_repo, config.forgejo_owner, name
                )
                metadata_ok = (
                    identity_ok
                    and forgejo_repo.get("private") is private
                    and forgejo_repo.get("description") == description
                )
                checks.append(
                    CheckResult(
                        "forgejo-repository-collision",
                        metadata_ok,
                        "existing Forgejo repository identity, visibility, and description match"
                        if metadata_ok
                        else "Forgejo ownership, visibility, or description collision",
                        "do not reuse or modify the unrelated repository",
                    )
                )
                if with_github:
                    admin = (forgejo_repo.get("permissions") or {}).get("admin") is True
                    checks.append(
                        CheckResult(
                            "forgejo-repository-admin",
                            admin,
                            "push-mirror admin permission confirmed on existing repository"
                            if admin
                            else "push-mirror admin permission missing on existing repository",
                            "grant repository admin permission",
                        )
                    )
                    mirrors = forgejo.list_push_mirrors(config.forgejo_owner, name)
                    address = (
                        f"git@{config.github_host}:{config.github_owner}/{name}.git"
                    )
                    matching = [
                        item
                        for item in mirrors
                        if item.get("remote_address") == address
                    ]
                    if len(matching) > 1:
                        checks.append(
                            CheckResult(
                                "mirror-collision",
                                False,
                                "duplicate matching push mirrors",
                                "remove duplicates only after manual review",
                            )
                        )
                    elif matching:
                        mirror = matching[0]
                        valid = (
                            mirror.get("use_ssh") is True
                            and mirror.get("sync_on_commit") is True
                            and is_ssh_public_key(mirror.get("public_key"))
                            and "last_error" in mirror
                            and mirror.get("last_error") == ""
                        )
                        checks.append(
                            CheckResult(
                                "mirror-collision",
                                valid,
                                "existing mirror has use_ssh=true, sync_on_commit=true, a public key, and empty last_error"
                                if valid
                                else "existing mirror must have use_ssh=true, sync_on_commit=true, a public key, and present empty last_error",
                                "repair or review the existing mirror manually",
                            )
                        )
                    else:
                        checks.append(
                            CheckResult(
                                "mirror-collision",
                                True,
                                "no matching mirror exists; one is planned",
                            )
                        )
        except Exception as exc:
            checks.append(
                CheckResult(
                    "forgejo-repository-collision",
                    False,
                    str(exc),
                    "verify repository read/admin permission and mirror API support",
                )
            )

    if name is not None and with_github and github_user is not None:
        try:
            github_repo = github.get_repo(config.github_owner, name)
            if github_repo is None:
                checks.append(
                    CheckResult(
                        "github-repository-collision",
                        True,
                        "target repository does not yet exist",
                    )
                )
            else:
                identity_ok = _repo_identity_ok(github_repo, config.github_owner, name)
                metadata_ok = (
                    identity_ok
                    and github_repo.get("private") is private
                    and github_repo.get("description") == description
                    and (github_repo.get("permissions") or {}).get("admin") is True
                )
                detail = (
                    "existing GitHub repository identity, visibility, description, and admin permission match"
                    if metadata_ok
                    else "GitHub ownership, visibility collision, description, or admin permission mismatch"
                )
                checks.append(
                    CheckResult(
                        "github-repository-collision",
                        metadata_ok,
                        detail,
                        "abort rather than reuse an unrelated or public repository",
                    )
                )
                actions = github.get_actions_permissions(config.github_owner, name)
                actions_known = isinstance(actions, Mapping) and isinstance(
                    actions.get("enabled"), bool
                )
                checks.append(
                    CheckResult(
                        "github-actions-policy",
                        actions_known,
                        "GitHub Actions are disabled"
                        if actions_known and actions.get("enabled") is False
                        else "GitHub Actions are enabled and will be disabled by the confirmed plan"
                        if actions_known
                        else "GitHub Actions permission state is ambiguous",
                        "grant Actions administration permission and inspect repository settings",
                    )
                )
                keys = github.list_deploy_keys(config.github_owner, name)
                title = f"Forgejo mirror: {config.forgejo_owner}/{name}"
                titled = [item for item in keys if item.get("title") == title]
                if mirror is not None:
                    public_key = mirror.get("public_key")
                    exact = [
                        item
                        for item in keys
                        if item.get("key") == public_key
                        and item.get("read_only") is False
                    ]
                    key_ok = len(titled) <= 1 and (
                        not titled or bool(exact and titled[0] in exact)
                    )
                    detail = (
                        "deploy key can be safely reused"
                        if key_ok
                        else "deploy-key collision: title, key material, or write permission differs"
                    )
                else:
                    key_ok = not titled
                    detail = (
                        "no existing mirror key requires reuse"
                        if key_ok
                        else "deploy-key collision: reserved mirror title exists before a mirror key can be verified"
                    )
                checks.append(
                    CheckResult(
                        "deploy-key-collision",
                        key_ok,
                        detail,
                        "review deploy keys manually; never replace an unrelated key silently",
                    )
                )
        except Exception as exc:
            checks.append(
                CheckResult(
                    "github-repository-collision",
                    False,
                    str(exc),
                    "verify repository visibility, ownership, and admin access",
                )
            )

    return PreflightReport(tuple(checks))


def run_preflight(
    config: Config,
    forgejo: Any,
    github: Any,
    *,
    token_present: bool,
    name: str | None,
    description: str | None,
    private: bool,
    with_github: bool,
    command_runner: CommandRunner | None = None,
    which: Callable[[str], str | None] = shutil.which,
    platform_name: str = sys.platform,
    reporter: CheckReporter | None = None,
    cancellation_requested: Callable[[], bool] | None = None,
) -> PreflightReport:
    """Run read-only checks with optional progress and cooperative cancellation."""
    if cancellation_requested is not None and cancellation_requested():
        return PreflightReport((), cancelled=True)
    try:
        return _run_preflight(
            config,
            forgejo,
            github,
            token_present=token_present,
            name=name,
            description=description,
            private=private,
            with_github=with_github,
            command_runner=command_runner,
            which=which,
            platform_name=platform_name,
            reporter=reporter,
            cancellation_requested=cancellation_requested,
        )
    except _PreflightCancelled as exc:
        return PreflightReport(exc.checks, cancelled=True)
