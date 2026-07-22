"""Compatibility exports for repository validation policy."""

from kosui_forge.domain.repository import is_ssh_public_key, validate_repo_name

__all__ = ["is_ssh_public_key", "validate_repo_name"]
