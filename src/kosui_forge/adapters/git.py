"""Fixed-argument Git subprocess adapter."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
import subprocess

from kosui_forge.domain.errors import ApiError, SafetyError
from kosui_forge.domain.redaction import redact


@dataclass(frozen=True)
class GitResult:
    returncode: int
    stdout: str
    stderr: str


GitRunner = Callable[[Sequence[str], Path | None], GitResult]


def _run_git(args: Sequence[str], cwd: Path | None) -> GitResult:
    try:
        result = subprocess.run(
            list(args), cwd=cwd, text=True, capture_output=True, check=False
        )
    except FileNotFoundError as exc:
        raise ApiError("git is not installed or is not available on PATH") from exc
    return GitResult(result.returncode, result.stdout, result.stderr)


def _checked(runner: GitRunner, args: list[str], cwd: Path | None) -> str:
    result = runner(args, cwd)
    if result.returncode != 0:
        diagnostic = redact(result.stderr or result.stdout).strip()
        raise ApiError(f"{' '.join(args[:3])} failed: {diagnostic}")
    return result.stdout.strip()


def forgejo_ssh_url(ssh_alias: str, owner: str, name: str) -> str:
    return f"ssh://git@{ssh_alias}/{owner}/{name}.git"


def clone_and_verify(
    path: Path,
    *,
    ssh_alias: str,
    owner: str,
    name: str,
    default_branch: str,
    runner: GitRunner | None = None,
) -> str:
    """Clone when absent and verify origin plus checked-out default branch."""
    run = runner or _run_git
    expected_remote = forgejo_ssh_url(ssh_alias, owner, name)
    created = False
    if path.exists():
        if not path.is_dir() or not (path / ".git").exists():
            raise SafetyError(
                f"local path collision: {path} exists but is not a Git working tree"
            )
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        _checked(
            run,
            ["git", "clone", "--origin", "origin", expected_remote, str(path)],
            None,
        )
        created = True

    actual_remote = _checked(run, ["git", "remote", "get-url", "origin"], path)
    if actual_remote != expected_remote:
        raise SafetyError(
            "origin collision: existing clone points somewhere else; "
            f"expected {expected_remote!r}, found {redact(actual_remote)!r}"
        )
    branch = _checked(run, ["git", "branch", "--show-current"], path)
    if branch != default_branch:
        raise SafetyError(
            f"default-branch verification failed: expected {default_branch!r}, found {branch!r}"
        )
    return "created" if created else "existing"
