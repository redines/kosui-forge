# ruff: noqa: F401 -- import-contract fixture
import importlib as loader
from pathlib import Path as BoundaryPath

from ..domain.repository import validate_repo_name


def describe(path: BoundaryPath) -> tuple[str, str, object]:
    """Use Path only as a value and import an allowed literal target."""
    return (
        path.as_posix(),
        validate_repo_name(path.name),
        loader.import_module("decimal"),
    )
