"""Read-only Doctor application service."""

from __future__ import annotations

from collections.abc import Callable

from kosui_forge.ports.doctor import DoctorPort, RawDoctorCheck

from .contracts import (
    CancellationToken,
    DoctorCheck,
    DoctorRequest,
    EventState,
    OperationEvent,
    OperationResult,
    OperationStatus,
    RecoveryInfo,
    ResourceLink,
)

ProgressReporter = Callable[[OperationEvent], None]


class DoctorService:
    """Return typed, redacted Doctor progress and results without owning adapters."""

    def __init__(self, doctor: DoctorPort) -> None:
        self._doctor = doctor

    def run(
        self,
        request: DoctorRequest,
        *,
        progress: ProgressReporter | None = None,
        cancellation: CancellationToken | None = None,
    ) -> OperationResult:
        """Run Doctor without mutation and stop only between completed checks."""
        cancellation = cancellation or CancellationToken()
        sequence = 0
        completed = 0
        checks_seen: list[DoctorCheck] = []

        def clean(value: object) -> str:
            return self._doctor.redact(value)

        def emit(
            stage: str,
            state: EventState,
            message: str,
            *,
            check: DoctorCheck | None = None,
            total: int | None = None,
        ) -> None:
            nonlocal sequence
            event = OperationEvent(
                operation_id=clean(request.operation_id),
                sequence=sequence,
                stage=clean(stage),
                state=state,
                message=clean(message),
                completed=completed,
                total=total,
                check=check,
            )
            sequence += 1
            if progress is not None:
                progress(event)

        def map_check(check: RawDoctorCheck) -> DoctorCheck:
            return DoctorCheck(
                name=clean(check.name),
                ok=check.ok,
                detail=clean(check.detail),
                guidance=clean(check.guidance),
            )

        def report_check(check: RawDoctorCheck) -> None:
            nonlocal completed
            structured = map_check(check)
            checks_seen.append(structured)
            completed += 1
            emit(
                structured.name,
                EventState.CHECK_PASSED if structured.ok else EventState.CHECK_FAILED,
                f"{structured.name}: {structured.detail}",
                check=structured,
            )

        try:
            report = self._doctor.run(
                config_path=request.config_path,
                repository_name=request.repository_name,
                description=(
                    request.description if request.repository_name is not None else None
                ),
                include_github=request.include_github,
                started=lambda: emit("doctor", EventState.STARTED, "Doctor started"),
                reporter=report_check,
                cancellation_requested=lambda: cancellation.requested,
            )
        except Exception as exc:
            emit("doctor", EventState.FAILED, clean(exc), total=completed)
            return OperationResult(
                operation_id=clean(request.operation_id),
                status=OperationStatus.FAILED,
                checks=tuple(checks_seen),
                recovery=RecoveryInfo(
                    "Doctor is read-only; correct the reported error and rerun it."
                ),
                cancellation_state=cancellation.state,
            )

        structured_checks = tuple(map_check(check) for check in report.checks)
        links = tuple(
            ResourceLink(clean(link.label), clean(link.target)) for link in report.links
        )
        if report.cancelled:
            cancellation.mark_honored()
            emit(
                "doctor",
                EventState.CANCELLED,
                "Doctor cancelled at a read-only safe point",
                total=len(structured_checks),
            )
            return OperationResult(
                operation_id=clean(request.operation_id),
                status=OperationStatus.CANCELLED,
                checks=structured_checks,
                links=links,
                recovery=RecoveryInfo(
                    "Doctor is read-only; cancellation changed no resources and it is safe to rerun."
                ),
                cancellation_state=cancellation.state,
            )

        status = OperationStatus.SUCCEEDED if report.ok else OperationStatus.FAILED
        emit(
            "doctor",
            EventState.SUCCEEDED if report.ok else EventState.FAILED,
            "Doctor completed successfully"
            if report.ok
            else "Doctor completed with failed checks",
            total=len(structured_checks),
        )
        return OperationResult(
            operation_id=clean(request.operation_id),
            status=status,
            checks=structured_checks,
            links=links,
            recovery=(
                None
                if report.ok
                else RecoveryInfo(
                    "Doctor is read-only; follow failed-check guidance and rerun it."
                )
            ),
            cancellation_state=cancellation.state,
        )
