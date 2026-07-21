"""Compatibility adapter from the application Doctor port to repo-bootstrap."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from repo_bootstrap.config import Config, load_config
from repo_bootstrap.forgejo import ForgejoClient
from repo_bootstrap.github import GitHubClient
from repo_bootstrap.preflight import CheckResult, PreflightReport, run_preflight
from repo_bootstrap.redaction import redact

from kosui_forge.ports.doctor import (
    RawDoctorCheck,
    RawDoctorReport,
    RawResourceLink,
)


class RepoBootstrapDoctorAdapter:
    """Invoke the tested preflight core and expose no mutation capability."""

    def __init__(
        self,
        *,
        environment: Mapping[str, str] | None = None,
        config_loader: Callable[[Path], Config] = load_config,
        forgejo_factory: Callable[[str, str], Any] = ForgejoClient,
        github_factory: Callable[..., Any] = GitHubClient,
        preflight_runner: Callable[..., PreflightReport] = run_preflight,
    ) -> None:
        import os

        self._environment = os.environ if environment is None else environment
        self._config_loader = config_loader
        self._forgejo_factory = forgejo_factory
        self._github_factory = github_factory
        self._preflight_runner = preflight_runner
        self._secrets: ContextVar[tuple[str, ...]] = ContextVar(
            "doctor_redaction_secrets", default=()
        )

    def redact(self, value: object) -> str:
        return redact(value, self._secrets.get())

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
        config = self._config_loader(config_path)
        token = self._environment.get(config.forgejo_token_env)
        self._secrets.set((token,) if token else ())
        forgejo = self._forgejo_factory(
            config.forgejo_url,
            token or "kosui-forge-preflight-missing-credential",
        )
        github = self._github_factory(host=config.github_host)
        started()

        def report_check(check: CheckResult) -> None:
            reporter(RawDoctorCheck(check.name, check.ok, check.detail, check.guidance))

        report = self._preflight_runner(
            config,
            forgejo,
            github,
            token_present=bool(token),
            name=repository_name,
            description=description,
            private=True,
            with_github=include_github,
            reporter=report_check,
            cancellation_requested=cancellation_requested,
        )
        checks = tuple(
            RawDoctorCheck(check.name, check.ok, check.detail, check.guidance)
            for check in report.checks
        )
        return RawDoctorReport(
            checks,
            ok=report.ok,
            cancelled=report.cancelled,
            links=(
                RawResourceLink("Forgejo", config.forgejo_url),
                RawResourceLink(
                    "GitHub owner",
                    f"https://{config.github_host}/{config.github_owner}",
                ),
                RawResourceLink("Projects root", str(config.projects_root)),
            ),
        )
