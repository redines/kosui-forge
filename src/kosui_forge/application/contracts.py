"""UI-neutral application contracts for Kosui Forge."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from threading import Lock
from uuid import uuid4


class OperationStatus(str, Enum):
    """Terminal status shared by application adapters."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EventState(str, Enum):
    """Ordered progress states emitted by an application service."""

    STARTED = "started"
    CHECK_PASSED = "check-passed"
    CHECK_FAILED = "check-failed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CancellationState(str, Enum):
    """Monotonic cancellation lifecycle."""

    NOT_REQUESTED = "not-requested"
    REQUESTED = "requested"
    HONORED = "honored"


class CancellationToken:
    """Thread-safe cooperative cancellation token with no reset operation."""

    def __init__(self) -> None:
        self._state = CancellationState.NOT_REQUESTED
        self._lock = Lock()

    @property
    def state(self) -> CancellationState:
        with self._lock:
            return self._state

    @property
    def requested(self) -> bool:
        return self.state is not CancellationState.NOT_REQUESTED

    def request(self) -> CancellationState:
        with self._lock:
            if self._state is CancellationState.NOT_REQUESTED:
                self._state = CancellationState.REQUESTED
            return self._state

    def mark_honored(self) -> CancellationState:
        with self._lock:
            if self._state is CancellationState.REQUESTED:
                self._state = CancellationState.HONORED
            return self._state


@dataclass(frozen=True, slots=True)
class DoctorRequest:
    """Read-only Doctor operation input."""

    config_path: Path
    repository_name: str | None = None
    description: str | None = None
    include_github: bool = True
    operation_id: str = field(default_factory=lambda: uuid4().hex, init=False)


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    """Structured, presentation-neutral Doctor check."""

    name: str
    ok: bool
    detail: str
    guidance: str = ""


@dataclass(frozen=True, slots=True)
class ResourceLink:
    """Validated resource target suitable for an adapter to open or display."""

    label: str
    target: str


@dataclass(frozen=True, slots=True)
class RecoveryInfo:
    """Safe recovery context for non-successful operations."""

    summary: str
    journal_path: Path | None = None
    resume_command: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OperationError:
    """Redacted terminal diagnostic for an operation that could not complete."""

    message: str


@dataclass(frozen=True, slots=True)
class OperationEvent:
    """One immutable, ordered progress event."""

    operation_id: str
    sequence: int
    stage: str
    state: EventState
    message: str
    completed: int
    total: int | None = None
    check: DoctorCheck | None = None


@dataclass(frozen=True, slots=True)
class OperationResult:
    """Structured terminal result returned by the application boundary."""

    operation_id: str
    status: OperationStatus
    checks: tuple[DoctorCheck, ...] = ()
    links: tuple[ResourceLink, ...] = ()
    recovery: RecoveryInfo | None = None
    error: OperationError | None = None
    cancellation_state: CancellationState = CancellationState.NOT_REQUESTED
