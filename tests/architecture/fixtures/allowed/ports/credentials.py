# ruff: noqa: F401 -- import-contract fixture
from __future__ import annotations

from abc import ABC as AbstractInterface
from dataclasses import dataclass
from collections.abc import Callable
from typing import Protocol as Interface
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kosui_forge.domain import repository as repository_domain


@dataclass(frozen=True, slots=True)
class CredentialReference:
    reference_id: str
    token_present: bool


class RepositoryLookupPort(Interface):
    def exists(
        self,
        *,
        cancellation_token: object,
        credential_reference: CredentialReference,
        validator: Callable[[str], str],
    ) -> bool: ...


class CredentialStore(Interface):
    def credential_status(self, credential_reference: CredentialReference) -> bool: ...


class CredentialSource(AbstractInterface):
    """An abstract capability name does not imply that the port carries secrets."""
