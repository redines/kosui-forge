"""Doctor port required by the application service."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RawDoctorCheck:
    """Adapter check before application-boundary redaction."""

    name: str
    ok: bool
    detail: str
    guidance: str = ""


@dataclass(frozen=True, slots=True)
class RawResourceLink:
    """Adapter resource target before application-boundary redaction."""

    label: str
    target: str


@dataclass(frozen=True, slots=True)
class RawDoctorReport:
    """Minimal read-only report supplied by a Doctor adapter."""

    checks: tuple[RawDoctorCheck, ...]
    ok: bool
    cancelled: bool = False
    links: tuple[RawResourceLink, ...] = ()


class DoctorPort(Protocol):
    """Smallest outer-layer interface consumed by the Doctor use case."""

    def redact(self, value: object) -> str:
        """Redact an adapter value with operation-specific secret knowledge."""
        ...

    def run(
        self,
        *,
        config_path: Path,
        repository_name: str | None,
        description: str | None,
        include_github: bool,
        started: Callable[[], None],
        reporter: Callable[[RawDoctorCheck], None],
        cancellation_requested: Callable[[], bool],
    ) -> RawDoctorReport:
        """Run read-only policy and report checks in completion order."""
        ...
