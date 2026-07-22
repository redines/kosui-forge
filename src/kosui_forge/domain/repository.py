"""Provider-neutral repository value validation."""

import re
from typing import TypeGuard

_REPOSITORY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def is_ssh_public_key(value: object) -> TypeGuard[str]:
    """Return whether a value has a supported OpenSSH public-key shape."""
    if not isinstance(value, str):
        return False
    parts = value.strip().split()
    key_type = parts[0] if parts else ""
    return (
        len(parts) >= 2
        and (
            key_type in {"ssh-ed25519", "ssh-rsa"} or key_type.startswith("ecdsa-sha2-")
        )
        and bool(parts[1])
    )


def validate_repo_name(name: str) -> str:
    """Validate a conservative repository name safe for paths and API routes."""
    if not _REPOSITORY_NAME.fullmatch(name) or name.endswith(".git"):
        raise ValueError(
            "repository name must be 1-100 letters, numbers, dots, underscores, "
            "or hyphens; it must start with a letter/number and not end in .git"
        )
    return name
