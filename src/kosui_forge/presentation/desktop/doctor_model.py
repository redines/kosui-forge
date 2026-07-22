"""Qt-free presentation model for the Doctor dashboard."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic

from kosui_forge.application.contracts import OperationEvent, OperationResult


@dataclass(slots=True)
class DoctorPresentationModel:
    """Collect already-redacted application contracts for deterministic rendering."""

    events: list[OperationEvent] = field(default_factory=list)
    result: OperationResult | None = None
    started_at: float = field(default_factory=monotonic)
    finished_at: float | None = None

    @property
    def elapsed_seconds(self) -> float:
        end = self.finished_at if self.finished_at is not None else monotonic()
        return max(0.0, end - self.started_at)

    def record(self, event: OperationEvent) -> None:
        self.events.append(event)

    def finish(self, result: OperationResult) -> None:
        self.result = result
        self.finished_at = monotonic()

    def diagnostic_text(self) -> str:
        """Render only redacted application-boundary values for safe copying."""
        lines = [
            "Kosui Forge Doctor diagnostic",
            f"Status: {self.result.status.value if self.result else 'running'}",
            f"Elapsed: {self.elapsed_seconds:.2f} seconds",
            "",
            "Progress:",
        ]
        lines.extend(
            f"  {event.sequence:03d} {event.state.value}: {event.message}"
            for event in self.events
        )
        if self.result is None:
            return "\n".join(lines)

        lines.extend(("", "Checks:"))
        if not self.result.checks:
            lines.append("  No checks completed.")
        for check in self.result.checks:
            status = "PASS" if check.ok else "FAIL"
            lines.append(f"  [{status}] {check.name}: {check.detail}")
            if check.guidance:
                lines.append(f"    Remediation: {check.guidance}")
        if self.result.error is not None:
            lines.extend(("", f"Error: {self.result.error.message}"))
        if self.result.recovery is not None:
            lines.append(f"Next step: {self.result.recovery.summary}")
        if self.result.links:
            lines.extend(("", "Resources:"))
            lines.extend(f"  {link.label}: {link.target}" for link in self.result.links)
        return "\n".join(lines)
