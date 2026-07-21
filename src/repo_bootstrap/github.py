from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import json
import subprocess
from typing import Any

from .errors import ApiError
from .redaction import redact


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str], str | None], CommandResult]


def _subprocess_runner(args: Sequence[str], stdin: str | None) -> CommandResult:
    try:
        result = subprocess.run(
            list(args),
            input=stdin,
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ApiError("gh is not installed or is not available on PATH") from exc
    return CommandResult(result.returncode, result.stdout, result.stderr)


class GitHubClient:
    def __init__(self, runner: Runner | None = None, *, host: str = "github.com"):
        self.runner = runner or _subprocess_runner
        self.host = host

    def _api(
        self,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        *,
        allow_404: bool = False,
    ) -> Any:
        args = [
            "gh",
            "api",
            "--hostname",
            self.host,
            "--method",
            method,
            endpoint,
        ]
        stdin = None
        if payload is not None:
            args.extend(["--input", "-"])
            stdin = json.dumps(payload)
        result = self.runner(args, stdin)
        if result.returncode != 0:
            diagnostic = redact(result.stderr or result.stdout)
            if allow_404 and "HTTP 404" in diagnostic:
                return None
            raise ApiError(f"gh api {method} {endpoint} failed: {diagnostic.strip()}")
        if not result.stdout.strip():
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ApiError(f"gh api {method} {endpoint} returned invalid JSON") from exc

    def authenticated_user(self) -> dict[str, Any]:
        return self._api("GET", "/user")

    def get_owner(self, owner: str) -> dict[str, Any] | None:
        return self._api("GET", f"/users/{owner}", allow_404=True)

    def get_membership(self, owner: str) -> dict[str, Any] | None:
        return self._api("GET", f"/user/memberships/orgs/{owner}", allow_404=True)

    def get_owner_access(self, owner: str) -> dict[str, bool]:
        user = self.authenticated_user()
        if user.get("login") == owner:
            return {"exists": True, "can_create": True, "can_admin": True}
        owner_data = self.get_owner(owner)
        if owner_data is None:
            return {"exists": False, "can_create": False, "can_admin": False}
        membership = self.get_membership(owner)
        active = membership is not None and membership.get("state") == "active"
        admin = membership is not None and active and membership.get("role") == "admin"
        can_create = bool(
            admin
            or (active and owner_data.get("members_can_create_repositories") is True)
        )
        return {"exists": True, "can_create": can_create, "can_admin": admin}

    def get_repo(self, owner: str, name: str) -> dict[str, Any] | None:
        return self._api("GET", f"/repos/{owner}/{name}", allow_404=True)

    def create_repo(
        self, owner: str, name: str, description: str, *, private: bool
    ) -> dict[str, Any]:
        login = self.authenticated_user().get("login")
        endpoint = "/user/repos" if login == owner else f"/orgs/{owner}/repos"
        payload = {"name": name, "description": description, "private": private}
        return self._api("POST", endpoint, payload)

    def disable_actions(self, owner: str, name: str) -> None:
        self._api(
            "PUT",
            f"/repos/{owner}/{name}/actions/permissions",
            {"enabled": False},
        )

    def get_actions_permissions(self, owner: str, name: str) -> dict[str, Any]:
        return self._api("GET", f"/repos/{owner}/{name}/actions/permissions")

    def list_deploy_keys(self, owner: str, name: str) -> list[dict[str, Any]]:
        return self._api("GET", f"/repos/{owner}/{name}/keys")

    def add_deploy_key(
        self, owner: str, name: str, title: str, public_key: str
    ) -> dict[str, Any]:
        return self._api(
            "POST",
            f"/repos/{owner}/{name}/keys",
            {"title": title, "key": public_key, "read_only": False},
        )
