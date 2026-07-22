from __future__ import annotations

from pathlib import Path
import unittest

from tests.architecture.import_contract import (
    LAYERS,
    find_import_cycles,
    find_import_violations,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = PROJECT_ROOT / "src/kosui_forge"
FIXTURES = Path(__file__).with_name("fixtures")
ARCHITECTURE_DECISION = PROJECT_ROOT / "docs/architecture/clean-architecture.md"


class PackageBoundaryTests(unittest.TestCase):
    def test_reviewed_layers_are_real_packages(self):
        missing = [
            layer
            for layer in LAYERS
            if not (PACKAGE_ROOT / layer / "__init__.py").is_file()
        ]

        self.assertEqual(missing, [])
        self.assertEqual(
            {
                path.name
                for path in PACKAGE_ROOT.iterdir()
                if path.is_dir() and (path / "__init__.py").is_file()
            },
            set(LAYERS),
        )
        self.assertTrue((PACKAGE_ROOT / "presentation/cli/__init__.py").is_file())
        self.assertTrue((PACKAGE_ROOT / "infrastructure/cli.py").is_file())

    def test_current_source_obeys_import_contract(self):
        self.assertEqual(find_import_violations(PACKAGE_ROOT), ())
        self.assertEqual(find_import_cycles(PACKAGE_ROOT), ())

    def test_allowed_import_fixture_is_accepted(self):
        self.assertEqual(find_import_violations(FIXTURES / "allowed"), ())
        self.assertEqual(find_import_cycles(FIXTURES / "allowed"), ())

    def test_cyclic_fixture_proves_module_cycles_are_rejected(self):
        self.assertEqual(
            find_import_cycles(FIXTURES / "cyclic"),
            (("kosui_forge.domain.one", "kosui_forge.domain.two"),),
        )

    def test_relative_cycle_fixture_proves_relative_cycles_are_rejected(self):
        self.assertEqual(
            find_import_cycles(FIXTURES / "relative_cyclic"),
            (("kosui_forge.domain.one", "kosui_forge.domain.two"),),
        )

    def test_literal_dynamic_imports_participate_in_cycle_detection(self):
        self.assertEqual(
            find_import_cycles(FIXTURES / "dynamic_cyclic"),
            (("kosui_forge.domain.one", "kosui_forge.domain.two"),),
        )

    def test_unreviewed_source_packages_are_rejected(self):
        violations = find_import_violations(FIXTURES / "forbidden")

        self.assertTrue(
            any(
                violation.path == Path("experimental/bad.py")
                and violation.imported == "kosui_forge.experimental.bad"
                and "outside the reviewed" in violation.reason
                for violation in violations
            )
        )

    def test_relative_dynamic_imports_resolve_the_current_module_package(self):
        violations = find_import_violations(FIXTURES / "forbidden")

        self.assertTrue(
            any(
                violation.path == Path("domain/dynamic.py")
                and violation.imported == "kosui_forge.presentation.cli"
                for violation in violations
            )
        )
        self.assertFalse(
            any(
                violation.path == Path("domain/dynamic.py")
                for violation in find_import_violations(FIXTURES / "allowed")
            )
        )

    def test_parent_relative_imports_to_inward_layers_are_accepted(self):
        self.assertFalse(
            any(
                violation.path == Path("application/relative_parent.py")
                for violation in find_import_violations(FIXTURES / "allowed")
            )
        )

    def test_parent_relative_imports_to_outward_layers_are_rejected(self):
        self.assertTrue(
            any(
                violation.path == Path("domain/relative.py")
                and violation.imported == "kosui_forge.infrastructure"
                and "outward layer infrastructure" in violation.reason
                for violation in find_import_violations(FIXTURES / "forbidden")
            )
        )

    def test_package_facades_and_builtin_dynamic_aliases_are_rejected(self):
        violations = find_import_violations(FIXTURES / "forbidden")
        observed = {(violation.path, violation.imported) for violation in violations}

        self.assertIn((Path("domain/bad.py"), "kosui_forge"), observed)
        self.assertIn(
            (Path("domain/dynamic.py"), "kosui_forge.adapters.doctor"), observed
        )
        self.assertIn(
            (Path("domain/dynamic.py"), "kosui_forge.infrastructure.doctor"), observed
        )

    def test_forbidden_import_fixture_proves_each_boundary_is_enforced(self):
        violations = find_import_violations(FIXTURES / "forbidden")
        layers = {
            violation.layer for violation in violations if violation.layer in LAYERS
        }
        imports = {violation.imported for violation in violations}

        self.assertEqual(
            layers,
            {
                "domain",
                "application",
                "ports",
                "adapters",
                "presentation",
                "infrastructure",
            },
        )
        self.assertTrue(
            {
                "PySide6.QtWidgets",
                "builtins.open",
                "fileinput",
                "glob",
                "githubkit",
                "io.open",
                "keyring",
                "kosui_forge",
                "kosui_forge.adapters.doctor",
                "kosui_forge.application.doctor",
                "kosui_forge.experimental",
                "kosui_forge.experimental.bad",
                "kosui_forge.infrastructure",
                "kosui_forge.infrastructure.cli",
                "kosui_forge.infrastructure.doctor",
                "kosui_forge.infrastructure.desktop",
                "kosui_forge.presentation.cli",
                "kosui_forge.presentation.cli.doctor",
                "mmap",
                "pathlib.Path.exists",
                "pathlib.Path.open",
                "pathlib.Path.read_bytes",
                "pathlib.Path.read_text",
                "pathlib.Path.write_text",
                "repo_bootstrap.preflight",
                "subprocess",
                "importlib.import_module(<dynamic>)",
            }.issubset(imports)
        )

    def test_representative_diagnostics_name_importer_target_and_rule(self):
        violations = find_import_violations(FIXTURES / "forbidden")
        observed = {
            (violation.path, violation.imported): violation.reason
            for violation in violations
        }
        expected = {
            (Path("domain/bad.py"), "keyring"): "only the standard library",
            (
                Path("application/bad.py"),
                "kosui_forge.adapters.doctor",
            ): "outward layer adapters",
            (
                Path("ports/bad.py"),
                "kosui_forge.application.doctor",
            ): "outward layer application",
            (
                Path("adapters/bad.py"),
                "kosui_forge.presentation.cli",
            ): "outward layer presentation",
            (
                Path("presentation/bad.py"),
                "kosui_forge.infrastructure.cli",
            ): "outward layer infrastructure",
            (
                Path("infrastructure/bad.py"),
                "kosui_forge",
            ): "package facade",
            (
                Path("domain/filesystem.py"),
                "builtins.open",
            ): "direct filesystem API",
            (
                Path("domain/dynamic.py"),
                "kosui_forge.infrastructure.doctor",
            ): "outward layer infrastructure",
            (
                Path("ports/credentials.py"),
                "port field access_token",
            ): "credential material",
            (
                Path("ports/credential_annotations.py"),
                "port parameter value annotation AccessToken",
            ): "credential material",
        }

        for edge, rule in expected.items():
            with self.subTest(importer=edge[0], target=edge[1], rule=rule):
                self.assertIn(edge, observed)
                self.assertIn(rule, observed[edge])

    def test_filesystem_fixture_rejects_each_reviewed_escape(self):
        observed = {
            (violation.path, violation.imported)
            for violation in find_import_violations(FIXTURES / "forbidden")
        }
        expected = {
            (Path("domain/filesystem.py"), imported)
            for imported in {
                "builtins.open",
                "fileinput",
                "glob",
                "io.open",
                "mmap",
                "pathlib.Path.exists",
                "pathlib.Path.open",
                "pathlib.Path.read_bytes",
                "pathlib.Path.read_text",
                "pathlib.Path.write_text",
            }
        }

        self.assertTrue(expected.issubset(observed))

    def test_allowed_shadowing_does_not_look_like_forbidden_io_or_imports(self):
        self.assertFalse(
            any(
                violation.path == Path("domain/dynamic.py")
                for violation in find_import_violations(FIXTURES / "allowed")
            )
        )

    def test_presentation_cannot_import_credential_integrations(self):
        self.assertTrue(
            any(
                violation.path == Path("presentation/bad.py")
                and violation.imported == "keyring"
                and "credential integrations" in violation.reason
                for violation in find_import_violations(FIXTURES / "forbidden")
            )
        )

    def test_ports_fixture_rejects_credential_values_but_allows_references(self):
        forbidden = find_import_violations(FIXTURES / "forbidden")
        credential_violations = {
            violation.imported
            for violation in forbidden
            if "credential material" in violation.reason
        }

        self.assertEqual(
            credential_violations,
            {
                "port class APIKey",
                "port class AccessToken",
                "port class ProviderCredentials",
                "port field access_token",
                "port field value annotation AccessToken",
                "port field private_key",
                "port field refresh_token",
                "port method get_secret",
                "port parameter password",
                "port parameter value annotation AccessToken",
                "port return authenticate annotation AccessToken",
            },
        )
        self.assertEqual(find_import_violations(FIXTURES / "allowed"), ())

    def test_type_checking_imports_follow_the_same_dependency_matrix(self):
        violations = find_import_violations(FIXTURES / "forbidden")

        self.assertTrue(
            any(
                violation.path == Path("ports/credentials.py")
                and violation.imported == "kosui_forge.presentation.cli"
                for violation in violations
            )
        )
        self.assertFalse(
            any(
                violation.path == Path("ports/credentials.py")
                for violation in find_import_violations(FIXTURES / "allowed")
            )
        )

    def test_violations_are_actionable_and_deterministic(self):
        violations = find_import_violations(FIXTURES / "forbidden")
        rendered = tuple(str(violation) for violation in violations)

        self.assertEqual(rendered, tuple(sorted(rendered)))
        self.assertTrue(
            all(
                f"[{violation.layer}]" in message
                and str(violation.path) in message
                and violation.imported in message
                for violation, message in zip(violations, rendered, strict=True)
            )
        )

    def test_migration_map_covers_every_compatibility_module(self):
        decision = ARCHITECTURE_DECISION.read_text(encoding="utf-8")
        compatibility_modules = {
            f"repo_bootstrap.{path.stem}"
            for path in (PROJECT_ROOT / "src/repo_bootstrap").glob("*.py")
        }

        self.assertEqual(len(compatibility_modules), 15)
        self.assertEqual(
            {
                module
                for module in compatibility_modules
                if f"`{module}`" not in decision
            },
            set(),
        )

    def test_decision_names_both_composition_roots_and_compatibility_removals(self):
        decision = ARCHITECTURE_DECISION.read_text(encoding="utf-8")

        self.assertIn("`kosui_forge.infrastructure.cli`", decision)
        self.assertIn("`kosui_forge.infrastructure.desktop`", decision)
        self.assertIn("Removal criteria", decision)


if __name__ == "__main__":
    unittest.main()
