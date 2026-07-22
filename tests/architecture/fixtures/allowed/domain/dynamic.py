# ruff: noqa: F401 -- import-contract fixture
from importlib import import_module as load_module

SAFE_MODULE = load_module("decimal")
DOMAIN_MODULE = load_module(".repository", __package__)
JSON_MODULE = __import__("json")


def call_domain_callback(open):
    """A caller-supplied callback named open is not the filesystem builtin."""
    return open("domain-value")
