import unittest

from kosui_forge.adapters.forgejo import ForgejoClient
from kosui_forge.adapters.git import GitResult, clone_and_verify
from kosui_forge.adapters.github import GitHubClient
from kosui_forge.adapters.persistence.configuration import (
    load_config,
    serialize_config,
    write_config,
)
from kosui_forge.adapters.persistence.journal import StageJournal
from kosui_forge.domain.configuration import Config, validate_config
from kosui_forge.domain.errors import (
    ApiError,
    ConfigError,
    PartialFailure,
    RepoToolingError,
    SafetyError,
)
from kosui_forge.domain.redaction import redact
from repo_bootstrap.config import Config as CompatibilityConfig
from repo_bootstrap.config import load_config as compatibility_load_config
from repo_bootstrap.config import serialize_config as compatibility_serialize_config
from repo_bootstrap.config import validate_config as compatibility_validate_config
from repo_bootstrap.config import write_config as compatibility_write_config
from repo_bootstrap.errors import ApiError as CompatibilityApiError
from repo_bootstrap.errors import ConfigError as CompatibilityConfigError
from repo_bootstrap.errors import PartialFailure as CompatibilityPartialFailure
from repo_bootstrap.errors import RepoToolingError as CompatibilityRepoToolingError
from repo_bootstrap.errors import SafetyError as CompatibilitySafetyError
from repo_bootstrap.forgejo import ForgejoClient as CompatibilityForgejoClient
from repo_bootstrap.github import GitHubClient as CompatibilityGitHubClient
from repo_bootstrap.gitops import GitResult as CompatibilityGitResult
from repo_bootstrap.gitops import clone_and_verify as compatibility_clone_and_verify
from repo_bootstrap.journal import StageJournal as CompatibilityStageJournal
from repo_bootstrap.redaction import redact as compatibility_redact


class CoreMigrationBoundaryTests(unittest.TestCase):
    def test_configuration_compatibility_module_forwards_to_owned_boundaries(self):
        self.assertIs(CompatibilityConfig, Config)
        self.assertIs(compatibility_validate_config, validate_config)
        self.assertIs(compatibility_load_config, load_config)
        self.assertIs(compatibility_serialize_config, serialize_config)
        self.assertIs(compatibility_write_config, write_config)

    def test_error_and_redaction_compatibility_modules_forward_to_domain(self):
        self.assertIs(CompatibilityRepoToolingError, RepoToolingError)
        self.assertIs(CompatibilityConfigError, ConfigError)
        self.assertIs(CompatibilitySafetyError, SafetyError)
        self.assertIs(CompatibilityApiError, ApiError)
        self.assertIs(CompatibilityPartialFailure, PartialFailure)
        self.assertIs(compatibility_redact, redact)

    def test_concrete_integrations_are_owned_by_adapter_layer(self):
        self.assertIs(CompatibilityForgejoClient, ForgejoClient)
        self.assertIs(CompatibilityGitHubClient, GitHubClient)
        self.assertIs(CompatibilityGitResult, GitResult)
        self.assertIs(compatibility_clone_and_verify, clone_and_verify)
        self.assertIs(CompatibilityStageJournal, StageJournal)


if __name__ == "__main__":
    unittest.main()
