"""Compatibility exports for the domain error taxonomy."""

from kosui_forge.domain.errors import (
    ApiError,
    ConfigError,
    PartialFailure,
    RepoToolingError,
    SafetyError,
)

__all__ = [
    "ApiError",
    "ConfigError",
    "PartialFailure",
    "RepoToolingError",
    "SafetyError",
]
