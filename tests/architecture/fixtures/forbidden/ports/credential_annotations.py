# ruff: noqa: F401 -- deliberately forbidden import-contract fixture
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class AccessToken(str):
    pass


ValueAlias = AccessToken


@dataclass(frozen=True, slots=True)
class AnnotatedPortValues:
    value: ValueAlias


class AnnotatedProviderPort(Protocol):
    def authenticate(self, value: ValueAlias) -> ValueAlias: ...
