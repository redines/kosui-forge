"""Composition root for the Doctor application service."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from repo_bootstrap.config import Config, load_config
from repo_bootstrap.forgejo import ForgejoClient
from repo_bootstrap.github import GitHubClient
from repo_bootstrap.preflight import PreflightReport, run_preflight

from kosui_forge.adapters.doctor import RepoBootstrapDoctorAdapter
from kosui_forge.application.doctor import DoctorService


def build_doctor_service(
    *,
    environment: Mapping[str, str] | None = None,
    config_loader: Callable[[Path], Config] = load_config,
    forgejo_factory: Callable[[str, str], Any] = ForgejoClient,
    github_factory: Callable[..., Any] = GitHubClient,
    preflight_runner: Callable[..., PreflightReport] = run_preflight,
) -> DoctorService:
    """Compose the application use case with the compatibility adapter."""
    return DoctorService(
        RepoBootstrapDoctorAdapter(
            environment=environment,
            config_loader=config_loader,
            forgejo_factory=forgejo_factory,
            github_factory=github_factory,
            preflight_runner=preflight_runner,
        )
    )
