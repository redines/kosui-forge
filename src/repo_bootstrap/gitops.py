"""Compatibility exports for the fixed-argument Git adapter."""

from kosui_forge.adapters.git import (
    GitResult,
    GitRunner,
    clone_and_verify,
    forgejo_ssh_url,
)

__all__ = ["GitResult", "GitRunner", "clone_and_verify", "forgejo_ssh_url"]
