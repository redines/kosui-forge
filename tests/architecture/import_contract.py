"""AST-based Clean Architecture import contract checks."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from importlib.util import resolve_name
from pathlib import Path
import re
import sys

LAYERS = (
    "domain",
    "application",
    "ports",
    "adapters",
    "presentation",
    "infrastructure",
)
_ALLOWED_LAYER_IMPORTS = {
    "domain": frozenset({"domain"}),
    "application": frozenset({"domain", "application", "ports"}),
    "ports": frozenset({"domain", "ports"}),
    "adapters": frozenset({"domain", "application", "ports", "adapters"}),
    "presentation": frozenset({"domain", "application", "presentation"}),
    "infrastructure": frozenset(LAYERS),
}
_INNER_LAYERS = frozenset({"domain", "application", "ports"})
_FORBIDDEN_INNER_STDLIB = frozenset(
    {"fileinput", "glob", "mmap", "os", "shutil", "subprocess", "tempfile"}
)
_FORBIDDEN_INNER_FILE_APIS = frozenset({"builtins.open", "io.open"})
_PATH_IO_METHODS = frozenset(
    {
        "absolute",
        "chmod",
        "cwd",
        "exists",
        "expanduser",
        "glob",
        "group",
        "hardlink_to",
        "home",
        "is_block_device",
        "is_char_device",
        "is_dir",
        "is_fifo",
        "is_file",
        "is_junction",
        "is_mount",
        "is_socket",
        "is_symlink",
        "iterdir",
        "lchmod",
        "link_to",
        "lstat",
        "mkdir",
        "open",
        "owner",
        "read_bytes",
        "read_text",
        "readlink",
        "rename",
        "replace",
        "resolve",
        "rglob",
        "rmdir",
        "samefile",
        "stat",
        "symlink_to",
        "touch",
        "unlink",
        "walk",
        "write_bytes",
        "write_text",
    }
)
_PATH_VALUE_METHODS = frozenset(
    {
        "absolute",
        "cwd",
        "expanduser",
        "home",
        "joinpath",
        "readlink",
        "relative_to",
        "rename",
        "replace",
        "resolve",
        "with_name",
        "with_stem",
        "with_suffix",
    }
)
_CREDENTIAL_WORDS = frozenset(
    {
        "credential",
        "credentials",
        "passwd",
        "password",
        "secret",
        "secrets",
    }
)
_TOKEN_WORDS = frozenset({"token", "tokens"})
_NON_CREDENTIAL_TOKEN_WORDS = frozenset(
    {
        "bucket",
        "cancellation",
        "continuation",
        "cursor",
        "operation",
        "page",
        "pagination",
    }
)
_CREDENTIAL_TOKEN_QUALIFIERS = frozenset(
    {
        "access",
        "api",
        "auth",
        "authentication",
        "bearer",
        "forgejo",
        "github",
        "oauth",
        "refresh",
        "session",
    }
)
_SAFE_CREDENTIAL_REFERENCE_WORDS = frozenset(
    {
        "available",
        "configured",
        "env",
        "fingerprint",
        "id",
        "identifier",
        "label",
        "metadata",
        "name",
        "present",
        "ref",
        "reference",
        "status",
        "variable",
    }
)
_SAFE_CREDENTIAL_CAPABILITY_WORDS = frozenset(
    {"backend", "port", "protocol", "provider", "source", "store"}
)


@dataclass(frozen=True, slots=True)
class ImportViolation:
    path: Path
    line: int
    layer: str
    imported: str
    reason: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.layer}] {self.imported}: {self.reason}"


@dataclass(frozen=True, slots=True)
class _ImportReference:
    imported: str
    line: int


def _identifier_words(name: str) -> frozenset[str]:
    acronym_split = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    snake_case = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", acronym_split).lower()
    return frozenset(part for part in re.split(r"[^a-z0-9]+", snake_case) if part)


def _names_credential_material(name: str, *, capability: bool = False) -> bool:
    words = _identifier_words(name)
    names_token = bool(words & _TOKEN_WORDS) and (
        not bool(words & _NON_CREDENTIAL_TOKEN_WORDS)
        or bool(words & _CREDENTIAL_TOKEN_QUALIFIERS)
    )
    sensitive = (
        bool(words & _CREDENTIAL_WORDS)
        or names_token
        or {"private", "key"}.issubset(words)
        or {"authorization", "header"}.issubset(words)
        or {"authenticated", "url"}.issubset(words)
        or {"api", "key"}.issubset(words)
    )
    if not sensitive or words & _SAFE_CREDENTIAL_REFERENCE_WORDS:
        return False
    return not capability or not bool(words & _SAFE_CREDENTIAL_CAPABILITY_WORDS)


def _function_arguments(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[ast.arg, ...]:
    arguments = (
        *node.args.posonlyargs,
        *node.args.args,
        *node.args.kwonlyargs,
    )
    if node.args.vararg is not None:
        arguments = (*arguments, node.args.vararg)
    if node.args.kwarg is not None:
        arguments = (*arguments, node.args.kwarg)
    return arguments


def _port_credential_references(tree: ast.Module) -> tuple[_ImportReference, ...]:
    """Return public port names that appear to carry reusable secret values."""
    references: list[_ImportReference] = []
    capability_aliases = {"ABC", "Protocol"}
    for statement in tree.body:
        if isinstance(statement, ast.ImportFrom) and statement.module in {
            "abc",
            "typing",
        }:
            capability_aliases.update(
                alias.asname or alias.name
                for alias in statement.names
                if alias.name in {"ABC", "Protocol"}
            )

    def inspect_field(name: str, line: int) -> None:
        if not name.startswith("_") and _names_credential_material(name):
            references.append(_ImportReference(f"port field {name}", line))

    def inspect_function(
        node: ast.FunctionDef | ast.AsyncFunctionDef, *, kind: str
    ) -> None:
        if not node.name.startswith("_") and _names_credential_material(node.name):
            references.append(_ImportReference(f"port {kind} {node.name}", node.lineno))
        for argument in _function_arguments(node):
            if (
                argument.arg not in {"self", "cls"}
                and not argument.arg.startswith("_")
                and _names_credential_material(argument.arg)
            ):
                references.append(
                    _ImportReference(f"port parameter {argument.arg}", argument.lineno)
                )

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            inspect_function(node, kind="function")
            continue
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            inspect_field(node.target.id, node.lineno)
            continue
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    inspect_field(target.id, node.lineno)
            continue
        if not isinstance(node, ast.ClassDef):
            continue
        is_capability = any(
            (isinstance(base, ast.Name) and base.id in capability_aliases)
            or (isinstance(base, ast.Attribute) and base.attr in {"ABC", "Protocol"})
            for base in node.bases
        )
        if not node.name.startswith("_") and _names_credential_material(
            node.name, capability=is_capability
        ):
            references.append(_ImportReference(f"port class {node.name}", node.lineno))
        for member in node.body:
            if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inspect_function(member, kind="method")
            elif isinstance(member, ast.AnnAssign) and isinstance(
                member.target, ast.Name
            ):
                inspect_field(member.target.id, member.lineno)
            elif isinstance(member, ast.Assign):
                for target in member.targets:
                    if isinstance(target, ast.Name):
                        inspect_field(target.id, member.lineno)
    return tuple(references)


class _ImportCollector(ast.NodeVisitor):
    """Collect static imports and reviewed statically resolvable call edges."""

    def __init__(self, *, module: str, is_package: bool) -> None:
        self.module = module
        self.is_package = is_package
        self.aliases: dict[str, str] = {}
        self.path_values: set[str] = set()
        self.shadowed_names: set[str] = set()
        self.imports: list[_ImportReference] = []
        self.cycle_candidates: list[str] = []

    def _qualified_name(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            if node.id in self.path_values:
                return "pathlib.Path"
            if node.id in self.shadowed_names:
                return node.id
            if node.id == "open" and node.id not in self.aliases:
                return "builtins.open"
            if node.id == "__import__" and node.id not in self.aliases:
                return "builtins.__import__"
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            value = self._qualified_name(node.value)
            return f"{value}.{node.attr}" if value is not None else None
        if isinstance(node, ast.Call):
            called = self._qualified_name(node.func)
            if called == "pathlib.Path" or (
                called is not None
                and called.startswith("pathlib.Path.")
                and called.rpartition(".")[2] in _PATH_VALUE_METHODS
            ):
                return "pathlib.Path"
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            if self._qualified_name(node.left) == "pathlib.Path":
                return "pathlib.Path"
        return None

    @staticmethod
    def _literal_argument(node: ast.Call, position: int, keyword: str) -> str | None:
        value: ast.expr | None = None
        if len(node.args) > position:
            value = node.args[position]
        else:
            value = next(
                (item.value for item in node.keywords if item.arg == keyword), None
            )
        return (
            value.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
            else None
        )

    def _record_dynamic_import(self, node: ast.Call, called: str) -> None:
        if called not in {"importlib.import_module", "builtins.__import__"}:
            return
        imported = self._literal_argument(node, 0, "name")
        if imported is None:
            self.imports.append(_ImportReference(f"{called}(<dynamic>)", node.lineno))
            return
        if imported.startswith("."):
            package = self._literal_argument(node, 1, "package")
            if package is None:
                package_node = (
                    node.args[1]
                    if len(node.args) > 1
                    else next(
                        (item.value for item in node.keywords if item.arg == "package"),
                        None,
                    )
                )
                if (
                    isinstance(package_node, ast.Name)
                    and package_node.id == "__package__"
                ):
                    package = (
                        self.module
                        if self.is_package
                        else self.module.rpartition(".")[0]
                    )
                else:
                    self.imports.append(
                        _ImportReference(f"{called}(<dynamic>)", node.lineno)
                    )
                    return
            try:
                imported = resolve_name(imported, package)
            except (ImportError, ValueError):
                self.imports.append(
                    _ImportReference(f"{called}(<dynamic>)", node.lineno)
                )
                return
        self.imports.append(_ImportReference(imported, node.lineno))
        self.cycle_candidates.append(imported)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(_ImportReference(alias.name, node.lineno))
            self.cycle_candidates.append(alias.name)
            local_name = alias.asname or alias.name.split(".", 1)[0]
            self.aliases[local_name] = alias.name if alias.asname else local_name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        imported = _resolve_import(node, module=self.module, is_package=self.is_package)
        self.imports.append(_ImportReference(imported, node.lineno))
        self.cycle_candidates.append(imported)
        for alias in node.names:
            qualified = f"{imported}.{alias.name}" if imported else alias.name
            self.cycle_candidates.append(qualified)
            self.aliases[alias.asname or alias.name] = qualified

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)

        aliases = self.aliases.copy()
        path_values = self.path_values.copy()
        shadowed_names = self.shadowed_names.copy()
        self.aliases.pop(node.name, None)
        self.path_values.discard(node.name)
        self.shadowed_names.add(node.name)
        arguments = (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        )
        if node.args.vararg is not None:
            arguments = (*arguments, node.args.vararg)
        if node.args.kwarg is not None:
            arguments = (*arguments, node.args.kwarg)
        for argument in arguments:
            self.aliases.pop(argument.arg, None)
            self.shadowed_names.add(argument.arg)
            if argument.annotation is not None and (
                self._qualified_name(argument.annotation) == "pathlib.Path"
            ):
                self.path_values.add(argument.arg)
        for statement in node.body:
            self.visit(statement)
        self.aliases = aliases
        self.path_values = path_values
        self.shadowed_names = shadowed_names
        self.aliases.pop(node.name, None)
        self.path_values.discard(node.name)
        self.shadowed_names.add(node.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        assigned = self._qualified_name(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                if assigned == "pathlib.Path":
                    self.path_values.add(target.id)
                    self.aliases.pop(target.id, None)
                    self.shadowed_names.discard(target.id)
                elif assigned in {
                    "builtins.__import__",
                    "builtins.open",
                    "importlib.import_module",
                    "io.open",
                }:
                    self.aliases[target.id] = assigned
                    self.path_values.discard(target.id)
                    self.shadowed_names.discard(target.id)
                else:
                    self.path_values.discard(target.id)
                    self.aliases.pop(target.id, None)
                    self.shadowed_names.add(target.id)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
        if isinstance(node.target, ast.Name) and (
            self._qualified_name(node.annotation) == "pathlib.Path"
            or (
                node.value is not None
                and self._qualified_name(node.value) == "pathlib.Path"
            )
        ):
            self.path_values.add(node.target.id)

    def visit_Call(self, node: ast.Call) -> None:
        called = self._qualified_name(node.func)
        if called is not None:
            self._record_dynamic_import(node, called)
            if called in _FORBIDDEN_INNER_FILE_APIS or (
                called.startswith("pathlib.Path.")
                and called.rpartition(".")[2] in _PATH_IO_METHODS
            ):
                self.imports.append(_ImportReference(called, node.lineno))
        self.generic_visit(node)


def _collect_imports(
    tree: ast.AST, *, module: str, is_package: bool
) -> _ImportCollector:
    collector = _ImportCollector(module=module, is_package=is_package)
    collector.visit(tree)
    return collector


def _module_name(path: Path, root: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(("kosui_forge", *parts))


def _resolve_import(node: ast.ImportFrom, *, module: str, is_package: bool) -> str:
    imported = node.module or ""
    if node.level == 0:
        return imported
    package = module if is_package else module.rpartition(".")[0]
    parts = package.split(".")
    keep = len(parts) - node.level + 1
    prefix = parts[: max(keep, 0)]
    return ".".join((*prefix, imported)) if imported else ".".join(prefix)


def _target_layer(imported: str) -> str | None:
    parts = imported.split(".")
    if len(parts) >= 2 and parts[0] == "kosui_forge" and parts[1] in LAYERS:
        return parts[1]
    return None


def _is_stdlib(imported: str) -> bool:
    return imported.split(".", 1)[0] in sys.stdlib_module_names


def _violation_reason(layer: str, imported: str) -> str | None:
    target = _target_layer(imported)
    if imported.endswith("(<dynamic>)"):
        return "layer code cannot use a dynamic import target that cannot be proven"
    if imported == "kosui_forge":
        return (
            "layer code must import an explicit inward module, not the package facade"
        )
    if imported.startswith("kosui_forge.") and target is None:
        return "layer code cannot depend on an unreviewed Kosui Forge package"
    if target is not None and target not in _ALLOWED_LAYER_IMPORTS[layer]:
        return f"{layer} cannot depend on outward layer {target}"
    if layer in _INNER_LAYERS:
        top_level = imported.split(".", 1)[0]
        if imported in _FORBIDDEN_INNER_FILE_APIS or (
            imported.startswith("pathlib.Path.")
            and imported.rpartition(".")[2] in _PATH_IO_METHODS
        ):
            return f"{layer} cannot call direct filesystem API {imported}"
        if top_level in _FORBIDDEN_INNER_STDLIB:
            return f"{layer} cannot depend on process or filesystem infrastructure"
        if target is None and top_level != "kosui_forge" and not _is_stdlib(imported):
            return (
                f"{layer} must use only the standard library and allowed inward layers"
            )
    if layer == "adapters" and target in {"presentation", "infrastructure"}:
        return f"adapters cannot depend on outward layer {target}"
    if layer == "presentation":
        if target in {"ports", "adapters", "infrastructure"}:
            return f"presentation cannot bypass application through {target}"
        if imported == "repo_bootstrap" or imported.startswith("repo_bootstrap."):
            return "presentation cannot depend on the compatibility implementation"
    return None


def find_import_violations(root: Path) -> tuple[ImportViolation, ...]:
    """Return forbidden imports below a package-shaped layer root."""
    violations: list[ImportViolation] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if not relative.parts or relative.parts[0] not in LAYERS:
            continue
        layer = relative.parts[0]
        module = _module_name(path, root)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        collector = _collect_imports(
            tree, module=module, is_package=path.name == "__init__.py"
        )
        for reference in collector.imports:
            reason = _violation_reason(layer, reference.imported)
            if reason is not None:
                violations.append(
                    ImportViolation(
                        relative,
                        reference.line,
                        layer,
                        reference.imported,
                        reason,
                    )
                )
        if layer == "ports":
            for reference in _port_credential_references(tree):
                violations.append(
                    ImportViolation(
                        relative,
                        reference.line,
                        layer,
                        reference.imported,
                        "ports cannot expose credential material; use a non-secret "
                        "reference or capability instead",
                    )
                )
    return tuple(sorted(violations, key=str))


def find_import_cycles(root: Path) -> tuple[tuple[str, ...], ...]:
    """Return strongly connected components in the internal module graph."""
    paths = sorted(root.rglob("*.py"))
    modules = {_module_name(path, root): path for path in paths}
    graph: dict[str, set[str]] = {module: set() for module in modules}
    for module, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        collector = _collect_imports(
            tree, module=module, is_package=path.name == "__init__.py"
        )
        for candidate in collector.cycle_candidates:
            target = next(
                (
                    name
                    for name in sorted(modules, key=len, reverse=True)
                    if candidate == name or candidate.startswith(f"{name}.")
                ),
                None,
            )
            if target is not None:
                graph[module].add(target)

    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[tuple[str, ...]] = []

    def visit(module: str) -> None:
        nonlocal index
        indices[module] = index
        lowlinks[module] = index
        index += 1
        stack.append(module)
        on_stack.add(module)
        for target in sorted(graph[module]):
            if target not in indices:
                visit(target)
                lowlinks[module] = min(lowlinks[module], lowlinks[target])
            elif target in on_stack:
                lowlinks[module] = min(lowlinks[module], indices[target])
        if lowlinks[module] != indices[module]:
            return
        component: list[str] = []
        while True:
            target = stack.pop()
            on_stack.remove(target)
            component.append(target)
            if target == module:
                break
        if len(component) > 1 or module in graph[module]:
            components.append(tuple(sorted(component)))

    for module in sorted(graph):
        if module not in indices:
            visit(module)
    return tuple(sorted(components))
