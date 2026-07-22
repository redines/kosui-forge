"""Text rendering for the compatibility Doctor command."""

from kosui_forge.application.contracts import OperationResult


def render_doctor_result(result: OperationResult) -> str:
    """Render a typed Doctor result without making application decisions."""
    lines: list[str] = []
    for check in result.checks:
        status = "PASS" if check.ok else "FAIL"
        line = f"[{status}] {check.name}: {check.detail}"
        if not check.ok and check.guidance:
            line += f"; {check.guidance}"
        lines.append(line)
    return "\n".join(lines)
