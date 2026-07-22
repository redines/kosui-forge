# ruff: noqa: F401 -- deliberately forbidden import-contract fixture
import importlib as module_loader
from importlib import import_module as load_module

OUTER_ONE = module_loader.import_module("kosui_forge.infrastructure.cli")
OUTER_TWO = load_module("kosui_forge.presentation.cli.doctor")
load_outer = module_loader.import_module
OUTER_THREE = load_outer("kosui_forge.infrastructure.desktop")
RELATIVE_OUTER = module_loader.import_module("..presentation.cli", __package__)
load_builtin = __import__
BUILTIN_OUTER = load_builtin("kosui_forge.adapters.doctor")
load_by_getattr = getattr(module_loader, "import_module")
OUTER_FOUR = load_by_getattr("kosui_forge.infrastructure.doctor")
runtime_target = "decimal"
UNPROVABLE = load_module(runtime_target)
