"""Retry logic for handling transient failures."""
import asyncio
import functools
import logging
from typing import Callable, Tuple, Type

from .exceptions import JebaoAuthenticationError, JebaoConnectionError, JebaoTimeoutError

_LOGGER = logging.getLogger(__name__)


def async_retry(
    max_attempts: int = 3,
    delay: float = 0.5,
    backoff: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (
        JebaoConnectionError,
        JebaoTimeoutError,
        JebaoAuthenticationError,
    ),
):
    """Retry an async method on transient errors.

    Only use this on idempotent operations (e.g. status reads). Every
    operation opens a fresh session, so no reconnect step is needed here.
    Intermediate failures are logged at debug level; the caller decides how
    loudly to report the final one.
    """

    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            device_id = getattr(args[0], "device_identifier", "") if args else ""
            current_delay = delay
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as err:
                    if attempt == max_attempts:
                        _LOGGER.debug(
                            "%s %s failed after %d attempts: %s",
                            device_id, func.__name__, max_attempts, err,
                        )
                        raise
                    _LOGGER.debug(
                        "%s %s attempt %d/%d failed, retrying in %.1fs: %s",
                        device_id, func.__name__, attempt, max_attempts, current_delay, err,
                    )
                    await asyncio.sleep(current_delay)
                    current_delay *= backoff
            return None  # pragma: no cover

        return wrapper

    return decorator
