# ruff: noqa: F401 -- deliberately forbidden import-contract fixture
import builtins as python_builtins
import fileinput as input_files
from glob import glob as expand_glob
import io as streams
import mmap as mapped_files
import pathlib as paths
from pathlib import Path as BoundaryPath
from builtins import open as open_file


def read_files() -> tuple[object, ...]:
    assigned_path = BoundaryPath("repository.txt")
    return (
        BoundaryPath("repository.txt").read_text(),
        BoundaryPath("repository").with_suffix(".txt").write_text("secret"),
        assigned_path.read_bytes(),
        paths.Path("repository.txt").open(),
        open("repository.txt"),
        python_builtins.open("repository.txt"),
        open_file("repository.txt"),
        streams.open("repository.txt"),
    )


def inspect_typed_path(path: BoundaryPath) -> bool:
    return path.exists()
