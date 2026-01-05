"""Exceptions for Jebao library."""


class JebaoError(Exception):
    """Base exception for Jebao library."""


class JebaoConnectionError(JebaoError):
    """Connection error."""


class JebaoAuthenticationError(JebaoError):
    """Authentication failed."""


class JebaoCommandError(JebaoError):
    """Command execution failed."""


class JebaoTimeoutError(JebaoError):
    """Operation timed out."""


class JebaoInvalidStateError(JebaoError):
    """Device is in invalid state for operation."""
