# ruff: noqa: F401 -- deliberately forbidden import-contract fixture
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from kosui_forge.presentation.cli import doctor as presentation_doctor


@dataclass(frozen=True, slots=True)
class ProviderCredentials:
    access_token: str
    private_key: bytes
    refresh_token = object()


class APIKey(str):
    pass


class LeakyProviderPort(Protocol):
    def authenticate(self, *, password: str) -> str: ...

    def get_secret(self) -> str: ...
