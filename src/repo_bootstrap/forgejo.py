from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

from pyforgejo import PyforgejoApi
from pyforgejo.errors.not_found_error import NotFoundError

from .errors import ApiError
from .redaction import redact
from .validation import is_ssh_public_key


SdkFactory = Callable[..., Any]
_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_MIRROR_INTERVAL = "8h"
_WRITE_REQUEST_OPTIONS = {"max_retries": 0}


class ForgejoClient:
    """Security-preserving adapter around the generated pyforgejo client."""

    def __init__(
        self,
        base_url: str,
        token: str,
        sdk: Any | None = None,
        *,
        sdk_factory: SdkFactory = PyforgejoApi,
    ):
        if not token:
            raise ApiError("Forgejo API token is empty")
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ApiError("Forgejo API URL must use absolute HTTPS")
        if parsed.username is not None or parsed.password is not None:
            raise ApiError("Forgejo API URL must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ApiError(
                "Forgejo API URL must not contain a query string or fragment"
            )
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.sdk = sdk or sdk_factory(
            base_url=f"{self.base_url}/api/v1",
            api_key=token,
            timeout=_DEFAULT_TIMEOUT_SECONDS,
            follow_redirects=False,
        )

    def _error(self, operation: str, exc: Exception) -> ApiError:
        status = getattr(exc, "status_code", None)
        detail = getattr(exc, "body", None)
        if detail is None:
            detail = str(exc)
        if status is None:
            message = f"Forgejo request failed during {operation}: {detail}"
        else:
            message = f"Forgejo API {operation} returned HTTP {status}: {detail}"
        return ApiError(redact(message, (self.token,)))

    def _call(self, operation: str, function: Callable[..., Any], *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except ApiError:
            raise
        except Exception as exc:
            raise self._error(operation, exc) from exc

    def _call_optional(
        self, operation: str, function: Callable[..., Any], *args, **kwargs
    ):
        try:
            return function(*args, **kwargs)
        except NotFoundError:
            return None
        except Exception as exc:
            raise self._error(operation, exc) from exc

    def _model_dict(self, value: Any, operation: str) -> dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        dump = getattr(value, "model_dump", None)
        if callable(dump):
            result = dump(mode="json")
            if isinstance(result, dict):
                return result
        raise ApiError(f"Forgejo API {operation} returned an unexpected schema")

    def _model_list(self, value: Any, operation: str) -> list[dict[str, Any]]:
        if not isinstance(value, Sequence) or isinstance(
            value, (str, bytes, bytearray)
        ):
            raise ApiError(f"Forgejo API {operation} returned an unexpected schema")
        return [self._model_dict(item, operation) for item in value]

    def _mirror_dict(self, value: Any, operation: str) -> dict[str, Any]:
        result = self._model_dict(value, operation)
        # Forgejo 11 accepts use_ssh on creation but intentionally omits it from
        # PushMirror responses. A generated public key is the response-side proof
        # that SSH was enabled; deriving this field preserves the fail-closed
        # adapter contract consumed by mirror verification.
        result["use_ssh"] = is_ssh_public_key(result.get("public_key"))
        return result

    def authenticated_user(self) -> dict[str, Any]:
        result = self._call("GET /user", self.sdk.user.get_current)
        return self._model_dict(result, "GET /user")

    def get_version(self) -> dict[str, Any]:
        result = self._call("GET /version", self.sdk.miscellaneous.get_version)
        return self._model_dict(result, "GET /version")

    def version(self) -> dict[str, Any]:
        return self.get_version()

    def get_org(self, owner: str) -> dict[str, Any] | None:
        result = self._call_optional(
            f"GET /orgs/{owner}", self.sdk.organization.org_get, owner
        )
        return None if result is None else self._model_dict(result, "GET organization")

    def list_user_orgs(self) -> list[dict[str, Any]]:
        result = self._call(
            "GET /user/orgs", self.sdk.organization.org_list_current_user_orgs
        )
        return self._model_list(result, "GET /user/orgs")

    def get_org_membership(self, owner: str) -> dict[str, Any] | None:
        user = self.authenticated_user()
        login = user.get("login") or user.get("username")
        if not isinstance(login, str) or not login:
            raise ApiError("Forgejo authenticated-user response lacks a login")
        result = self._call_optional(
            f"GET /users/{login}/orgs/{owner}/permissions",
            self.sdk.organization.org_get_user_permissions,
            login,
            owner,
        )
        if result is None:
            return None
        permissions = self._model_dict(result, "GET organization permissions")
        role = (
            "owner"
            if permissions.get("is_owner") is True
            else "admin"
            if permissions.get("is_admin") is True
            else "member"
        )
        return {**permissions, "state": "active", "role": role}

    def get_owner_access(self, owner: str) -> dict[str, bool]:
        user = self.authenticated_user()
        login = user.get("login") or user.get("username")
        if not isinstance(login, str) or not login:
            raise ApiError("Forgejo authenticated-user response lacks a login")
        if login == owner or user.get("is_admin") is True:
            return {"exists": True, "can_create": True, "can_admin": True}
        organization = self.get_org(owner)
        if organization is None:
            return {"exists": False, "can_create": False, "can_admin": False}
        permissions = self.get_org_membership(owner)
        if permissions is None:
            return {"exists": True, "can_create": False, "can_admin": False}
        can_admin = bool(
            permissions.get("is_owner") is True or permissions.get("is_admin") is True
        )
        return {
            "exists": True,
            "can_create": bool(
                can_admin or permissions.get("can_create_repository") is True
            ),
            "can_admin": can_admin,
        }

    def get_repo(self, owner: str, name: str) -> dict[str, Any] | None:
        result = self._call_optional(
            f"GET /repos/{owner}/{name}",
            self.sdk.repository.repo_get,
            owner,
            name,
        )
        return None if result is None else self._model_dict(result, "GET repository")

    def create_repo(
        self,
        owner: str,
        name: str,
        description: str,
        *,
        private: bool,
        default_branch: str,
    ) -> dict[str, Any]:
        user = self.authenticated_user()
        login = user.get("login") or user.get("username")
        if not isinstance(login, str) or not login:
            raise ApiError("Forgejo authenticated-user response lacks a login")
        payload = {
            "name": name,
            "description": description,
            "private": private,
            "auto_init": True,
            "default_branch": default_branch,
        }
        if login == owner:
            result = self._call(
                "POST /user/repos",
                self.sdk.repository.create_current_user_repo,
                **payload,
                request_options=_WRITE_REQUEST_OPTIONS,
            )
        else:
            if self.get_org(owner) is None:
                raise ApiError(
                    f"Forgejo owner {owner!r} is neither the authenticated user nor an organization"
                )
            result = self._call(
                f"POST /orgs/{owner}/repos",
                self.sdk.organization.create_org_repo,
                owner,
                **payload,
                request_options=_WRITE_REQUEST_OPTIONS,
            )
        return self._model_dict(result, "POST repository")

    def list_repos(self, *, page: int = 1, limit: int = 50) -> list[dict[str, Any]]:
        result = self._call(
            "GET /user/repos",
            self.sdk.user.current_list_repos,
            page=page,
            limit=limit,
        )
        return self._model_list(result, "GET /user/repos")

    def list_push_mirrors(self, owner: str, name: str) -> list[dict[str, Any]]:
        result = self._call(
            f"GET /repos/{owner}/{name}/push_mirrors",
            self.sdk.repository.repo_list_push_mirrors,
            owner,
            name,
        )
        if not isinstance(result, Sequence) or isinstance(
            result, (str, bytes, bytearray)
        ):
            raise ApiError("Forgejo API push-mirror list returned an unexpected schema")
        return [self._mirror_dict(item, "GET push mirrors") for item in result]

    def create_push_mirror(
        self,
        owner: str,
        name: str,
        remote_address: str,
        *,
        interval: str,
    ) -> dict[str, Any]:
        effective_interval = interval.strip() or _DEFAULT_MIRROR_INTERVAL
        result = self._call(
            f"POST /repos/{owner}/{name}/push_mirrors",
            self.sdk.repository.repo_add_push_mirror,
            owner,
            name,
            remote_address=remote_address,
            sync_on_commit=True,
            use_ssh=True,
            interval=effective_interval,
            request_options=_WRITE_REQUEST_OPTIONS,
        )
        return self._mirror_dict(result, "POST push mirror")

    def sync_push_mirrors(self, owner: str, name: str) -> None:
        self._call(
            f"POST /repos/{owner}/{name}/push_mirrors-sync",
            self.sdk.repository.repo_push_mirror_sync,
            owner,
            name,
            request_options=_WRITE_REQUEST_OPTIONS,
        )
