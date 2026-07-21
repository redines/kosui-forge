from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .config import Config
from .preflight import PreflightReport, ToolResult, run_preflight


class Doctor:
    """Compatibility facade for embedding the read-only preflight checker."""

    def __init__(
        self,
        config: Config,
        forgejo: Any,
        github: Any,
        runner: Any,
        *,
        environ: Mapping[str, str],
        system: str,
    ) -> None:
        self.config = config
        self.forgejo = forgejo
        self.github = github
        self.runner = runner
        self.environ = environ
        self.system = system

    def _run_command(self, args: Sequence[str]) -> ToolResult:
        result = self.runner(args, None)
        return ToolResult(result.returncode, result.stdout, result.stderr)

    def run(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        with_github: bool = True,
        private: bool = True,
    ) -> PreflightReport:
        platform_name = {
            "Linux": "linux",
            "Darwin": "darwin",
            "Windows": "win32",
        }.get(self.system, self.system)
        return run_preflight(
            self.config,
            self.forgejo,
            self.github,
            token_present=bool(self.environ.get(self.config.forgejo_token_env)),
            name=name,
            description=description,
            private=private,
            with_github=with_github,
            command_runner=self._run_command,
            which=lambda executable: f"mock://{executable}",
            platform_name=platform_name,
        )
