"""Custom exceptions for this charm."""


class ConfigurationError(Exception):
    """Base exception for configuration errors."""

    pass


class ConfigurationBlockingError(ConfigurationError):
    """Raised when a configuration error should result in a Blocked status."""

    pass


class ConfigurationWaitingError(ConfigurationError):
    """Raised when a configuration error should result in a Waiting status."""

    pass
