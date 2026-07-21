from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .errors import ApiError
from .redaction import redact


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes


Transport = Callable[[str, str, dict[str, str], bytes | None], HttpResponse]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = build_opener(_NoRedirect())


def _urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> HttpResponse:
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with _OPENER.open(request, timeout=30) as response:
            return HttpResponse(response.status, response.read())
    except HTTPError as exc:
        return HttpResponse(exc.code, exc.read())
    except URLError as exc:
        raise ApiError(f"Forgejo request failed: {redact(exc.reason)}") from exc


class ForgejoClient:
    def __init__(self, base_url: str, token: str, transport: Transport | None = None):
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
        self.transport = transport or _urllib_transport

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        allowed: tuple[int, ...] = (200,),
    ) -> tuple[int, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Authorization": f"token {self.token}",
            "User-Agent": "repo-bootstrap/0.1",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        url = f"{self.base_url}/api/v1{path}"
        try:
            response = self.transport(method, url, headers, body)
        except ApiError:
            raise
        except Exception as exc:
            raise ApiError(
                f"Forgejo request failed: {redact(exc, (self.token,))}"
            ) from exc
        if response.status not in allowed:
            detail = response.body.decode("utf-8", errors="replace")
            raise ApiError(
                f"Forgejo API {method} {path} returned HTTP {response.status}: "
                f"{redact(detail, (self.token,))}"
            )
        if not response.body:
            return response.status, None
        try:
            return response.status, json.loads(response.body)
        except json.JSONDecodeError as exc:
            raise ApiError(
                f"Forgejo API {method} {path} returned invalid JSON"
            ) from exc

    @staticmethod
    def _repo_path(owner: str, name: str) -> str:
        return f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"

    def authenticated_user(self) -> dict[str, Any]:
        return self._request("GET", "/user")[1]

    def get_version(self) -> dict[str, Any]:
        return self._request("GET", "/version")[1]

    def version(self) -> dict[str, Any]:
        return self.get_version()

    def get_org(self, owner: str) -> dict[str, Any] | None:
        status, result = self._request(
            "GET", f"/orgs/{quote(owner, safe='')}", allowed=(200, 404)
        )
        return None if status == 404 else result

    def list_user_orgs(self) -> list[dict[str, Any]]:
        return self._request("GET", "/user/orgs")[1]

    def get_org_membership(self, owner: str) -> dict[str, Any] | None:
        status, result = self._request(
            "GET",
            f"/user/memberships/orgs/{quote(owner, safe='')}",
            allowed=(200, 404),
        )
        return None if status == 404 else result

    def get_owner_access(self, owner: str) -> dict[str, bool]:
        user = self.authenticated_user()
        login = user.get("login") or user.get("username")
        if login == owner or user.get("is_admin") is True:
            return {"exists": True, "can_create": True, "can_admin": True}
        organization = self.get_org(owner)
        membership = (
            self.get_org_membership(owner) if organization is not None else None
        )
        active = (
            membership is not None and membership.get("state", "active") == "active"
        )
        admin = bool(
            membership is not None
            and active
            and membership.get("role") in {"owner", "admin"}
        )
        return {
            "exists": organization is not None,
            "can_create": admin,
            "can_admin": admin,
        }

    def get_repo(self, owner: str, name: str) -> dict[str, Any] | None:
        path = self._repo_path(owner, name)
        status, result = self._request("GET", path, allowed=(200, 404))
        return None if status == 404 else result

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
        if user.get("login") == owner or user.get("username") == owner:
            path = "/user/repos"
        else:
            status, _ = self._request(
                "GET", f"/orgs/{quote(owner, safe='')}", allowed=(200, 404)
            )
            if status == 404:
                raise ApiError(
                    f"Forgejo owner {owner!r} is neither the authenticated user nor an organization"
                )
            path = f"/orgs/{quote(owner, safe='')}/repos"
        payload = {
            "name": name,
            "description": description,
            "private": private,
            "auto_init": True,
            "default_branch": default_branch,
        }
        return self._request("POST", path, payload, allowed=(201,))[1]

    def list_repos(self, *, page: int = 1, limit: int = 50) -> list[dict[str, Any]]:
        _, result = self._request("GET", f"/user/repos?page={page}&limit={limit}")
        return result

    def list_push_mirrors(self, owner: str, name: str) -> list[dict[str, Any]]:
        path = self._repo_path(owner, name) + "/push_mirrors"
        return self._request("GET", path)[1]

    def create_push_mirror(
        self,
        owner: str,
        name: str,
        remote_address: str,
        *,
        interval: str,
    ) -> dict[str, Any]:
        path = self._repo_path(owner, name) + "/push_mirrors"
        payload = {
            "remote_address": remote_address,
            "sync_on_commit": True,
            "use_ssh": True,
            "interval": interval,
        }
        return self._request("POST", path, payload, allowed=(201,))[1]

    def sync_push_mirrors(self, owner: str, name: str) -> None:
        path = self._repo_path(owner, name) + "/push_mirrors-sync"
        self._request("POST", path, allowed=(200, 204))
