from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import subprocess
from typing import Any

from githubkit import GitHub, TokenAuthStrategy

from .errors import ApiError


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str], str | None], CommandResult]
SDKFactory = Callable[[str, str | None], Any]


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


def _sdk_factory(token: str, base_url: str | None) -> GitHub:
    return GitHub(TokenAuthStrategy(token), base_url=base_url)


def _status_code(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


class GitHubClient:
    """Thin, fail-closed normalization adapter over GitHubKit's REST SDK."""

    def __init__(
        self,
        runner: Runner | None = None,
        *,
        host: str = "github.com",
        sdk: Any | None = None,
        sdk_factory: SDKFactory | None = None,
    ):
        self.runner = runner or _subprocess_runner
        self.host = host
        self._sdk = sdk
        self._sdk_factory = sdk_factory or _sdk_factory
        self._rest_api: Any | None = None

    @property
    def _base_url(self) -> str | None:
        if self.host == "github.com":
            return None
        return f"https://{self.host}/api/v3"

    def _client(self) -> Any:
        if self._sdk is not None:
            return self._sdk
        result = self.runner(
            ["gh", "auth", "token", "--hostname", self.host],
            None,
        )
        if result.returncode != 0:
            # gh diagnostics can include credential material supplied by helpers.
            # Keep the failure actionable without copying command output.
            raise ApiError(
                f"gh auth token failed for {self.host}; run gh auth status --hostname {self.host}"
            )
        token = result.stdout.strip()
        if not token:
            raise ApiError(f"gh auth token returned no credential for {self.host}")
        try:
            client = self._sdk_factory(token, self._base_url)
        except Exception as exc:
            raise ApiError(
                f"GitHub SDK initialization failed for {self.host}: {type(exc).__name__}"
            ) from exc
        self._sdk = client
        return client

    def _rest(self, sdk: Any) -> Any:
        if self._rest_api is not None:
            return self._rest_api
        rest = sdk.rest
        if self.host != "github.com" and callable(rest):
            # The stable version is broadly supported by GitHub Enterprise
            # Server; the latest public schema can target newer endpoints.
            rest = rest("2022-11-28")
        self._rest_api = rest
        return rest

    def _call(
        self,
        label: str,
        operation: Callable[[Any], Any],
        *,
        allow_404: bool = False,
    ) -> Any | None:
        try:
            return operation(self._client())
        except ApiError:
            raise
        except Exception as exc:
            status = _status_code(exc)
            if allow_404 and status == 404:
                return None
            detail = f"HTTP {status}" if status is not None else type(exc).__name__
            raise ApiError(f"GitHub SDK {label} failed: {detail}") from exc

    def _object(
        self,
        label: str,
        operation: Callable[[Any], Any],
        *,
        allow_404: bool = False,
    ) -> dict[str, Any] | None:
        response = self._call(label, operation, allow_404=allow_404)
        if response is None:
            return None
        try:
            data = response.json()
        except Exception as exc:
            raise ApiError(
                f"GitHub SDK {label} returned an unreadable response"
            ) from exc
        if not isinstance(data, dict):
            raise ApiError(f"GitHub SDK {label} returned malformed object metadata")
        return data

    def _page(
        self,
        label: str,
        operation: Callable[[Any], Any],
    ) -> list[dict[str, Any]]:
        response = self._call(label, operation)
        if response is None:
            raise ApiError(f"GitHub SDK {label} returned no response")
        try:
            data = response.json()
        except Exception as exc:
            raise ApiError(
                f"GitHub SDK {label} returned an unreadable response"
            ) from exc
        if not isinstance(data, list) or not all(
            isinstance(item, dict) for item in data
        ):
            raise ApiError(f"GitHub SDK {label} returned malformed list metadata")
        return data

    def authenticated_user(self) -> dict[str, Any]:
        result = self._object(
            "get authenticated user",
            lambda sdk: self._rest(sdk).users.get_authenticated(),
        )
        if result is None:
            raise ApiError("GitHub SDK returned no authenticated user")
        return result

    def get_owner(self, owner: str) -> dict[str, Any] | None:
        result = self._object(
            "get owner",
            lambda sdk: self._rest(sdk).users.get_by_username(username=owner),
            allow_404=True,
        )
        if result is None or result.get("type") != "Organization":
            return result
        organization = self._object(
            "get organization policy",
            lambda sdk: self._rest(sdk).orgs.get(org=owner),
            allow_404=True,
        )
        return organization or result

    def get_membership(self, owner: str) -> dict[str, Any] | None:
        return self._object(
            "get organization membership",
            lambda sdk: self._rest(sdk).orgs.get_membership_for_authenticated_user(
                org=owner
            ),
            allow_404=True,
        )

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
        return self._object(
            "get repository",
            lambda sdk: self._rest(sdk).repos.get(owner, name),
            allow_404=True,
        )

    def create_repo(
        self, owner: str, name: str, description: str, *, private: bool
    ) -> dict[str, Any]:
        login = self.authenticated_user().get("login")
        if login == owner:

            def operation(sdk: Any) -> Any:
                return self._rest(sdk).repos.create_for_authenticated_user(
                    name=name,
                    description=description,
                    private=private,
                )
        else:

            def operation(sdk: Any) -> Any:
                return self._rest(sdk).repos.create_in_org(
                    owner,
                    name=name,
                    description=description,
                    private=private,
                )

        result = self._object("create repository", operation)
        if result is None:
            raise ApiError("GitHub SDK returned no created repository")
        return result

    def disable_actions(self, owner: str, name: str) -> None:
        self._call(
            "disable Actions",
            lambda sdk: self._rest(
                sdk
            ).actions.set_github_actions_permissions_repository(
                owner, name, enabled=False
            ),
        )

    def get_actions_permissions(self, owner: str, name: str) -> dict[str, Any]:
        result = self._object(
            "get Actions permissions",
            lambda sdk: self._rest(
                sdk
            ).actions.get_github_actions_permissions_repository(owner, name),
        )
        if result is None:
            raise ApiError("GitHub SDK returned no Actions permission state")
        return result

    def list_deploy_keys(self, owner: str, name: str) -> list[dict[str, Any]]:
        keys: list[dict[str, Any]] = []
        page = 1
        while True:

            def list_page(sdk: Any, current: int = page) -> Any:
                return self._rest(sdk).repos.list_deploy_keys(
                    owner,
                    name,
                    per_page=100,
                    page=current,
                )

            batch = self._page(
                f"list deploy keys page {page}",
                list_page,
            )
            keys.extend(batch)
            if len(batch) < 100:
                return keys
            page += 1

    def add_deploy_key(
        self, owner: str, name: str, title: str, public_key: str
    ) -> dict[str, Any]:
        result = self._object(
            "create deploy key",
            lambda sdk: self._rest(sdk).repos.create_deploy_key(
                owner,
                name,
                title=title,
                key=public_key,
                read_only=False,
            ),
        )
        if result is None:
            raise ApiError("GitHub SDK returned no created deploy key")
        return result
