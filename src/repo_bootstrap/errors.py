class RepoToolingError(Exception):
    """Base class for expected command failures."""


class ConfigError(RepoToolingError):
    """Configuration is missing or unsafe."""


class SafetyError(RepoToolingError):
    """An operation failed a safety gate."""


class ApiError(RepoToolingError):
    """A remote API operation failed."""


class PartialFailure(RepoToolingError):
    """An operation stopped after creating some resources."""
