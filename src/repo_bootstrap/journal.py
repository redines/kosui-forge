from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .redaction import redact
from .validation import validate_repo_name


_RECOVERY = (
    "Correct the failed prerequisite or stage and rerun the same command. "
    "The workflow reuses matching resources. Never delete a pre-existing resource; "
    "manually remove only resources marked preexisting=false after explicit approval."
)


class StageJournal:
    """Owner-only, non-secret progress journal for safe resume after partial failure."""

    def __init__(
        self,
        path: Path,
        *,
        command: str,
        forgejo_repository: str,
        github_repository: str | None,
    ) -> None:
        self.path = path
        self.document: dict[str, Any] = {
            "schema": 1,
            "command": command,
            "forgejo_repository": forgejo_repository,
            "github_repository": github_repository,
            "status": "planned",
            "plan": [],
            "stages": {},
            "recovery": _RECOVERY,
        }

    @staticmethod
    def path_for(state_dir: Path, owner: str, name: str) -> Path:
        validate_repo_name(name)
        safe_owner = "".join(
            character if character.isalnum() or character in "._-" else "_"
            for character in owner
        ).strip(".")
        if not safe_owner:
            safe_owner = "owner"
        return state_dir / f"{safe_owner}--{name}.json"

    def start(self, plan: list[str]) -> None:
        self.document["status"] = "running"
        self.document["plan"] = [redact(item) for item in plan]
        self._write()

    def record(
        self,
        stage: str,
        state: str,
        *,
        preexisting: bool | None = None,
        detail: object | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "state": state,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        if preexisting is not None:
            entry["preexisting"] = preexisting
        if detail is not None:
            entry["detail"] = redact(detail)
        self.document["stages"][stage] = entry
        self.document["status"] = "failed" if state == "failed" else "running"
        self._write()

    def complete(self) -> None:
        self.document["status"] = "complete"
        self._write()

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        content = json.dumps(self.document, indent=2, sort_keys=True) + "\n"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=self.path.parent, text=True
        )
        temporary = Path(temporary_name)
        try:
            os.chmod(temporary, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
            os.chmod(self.path, 0o600)
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            temporary.unlink(missing_ok=True)
            raise
