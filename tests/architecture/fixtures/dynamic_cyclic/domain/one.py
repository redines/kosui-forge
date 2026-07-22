import importlib

load_module = getattr(importlib, "import_module")
TWO = load_module("kosui_forge.domain.two")
