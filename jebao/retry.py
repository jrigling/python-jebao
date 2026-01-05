"""Retry logic for handling transient failures."""
import asyncio
import functools
import logging
from typing import Callable, Type, Tuple

from .exceptions import JebaoConnectionError, JebaoTimeoutError

_LOGGER = logging.getLogger(__name__)


def async_retry(
    max_attempts: int = 3,
    delay: float = 0.5,
    backoff: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (JebaoConnectionError, JebaoTimeoutError),
):
    """Decorator to retry async functions on specific exceptions.

    Args:
        max_attempts: Maximum number of attempts (default 3)
        delay: Initial delay between retries in seconds (default 0.5)
        backoff: Multiplier for delay on each retry (default 2.0)
        exceptions: Tuple of exception types to retry on

    Example:
        @async_retry(max_attempts=3, delay=1.0)
        async def send_command(self):
            # Code that might fail due to garbage bytes
            pass
    """

    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            attempt = 1
            current_delay = delay

            while attempt <= max_attempts:
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_attempts:
                        _LOGGER.error(
                            f"{func.__name__} failed after {max_attempts} attempts: {e}"
                        )
                        raise

                    # Check if error message indicates garbage issue
                    error_msg = str(e).lower()
                    is_garbage_issue = (
                        "synchronization" in error_msg
                        or "garbage" in error_msg
                        or "invalid header" in error_msg
                    )

                    if is_garbage_issue:
                        _LOGGER.warning(
                            f"{func.__name__} attempt {attempt}/{max_attempts} failed "
                            f"due to garbage bytes, retrying in {current_delay}s: {e}"
                        )
                    else:
                        _LOGGER.warning(
                            f"{func.__name__} attempt {attempt}/{max_attempts} failed, "
                            f"retrying in {current_delay}s: {e}"
                        )

                    await asyncio.sleep(current_delay)
                    attempt += 1
                    current_delay *= backoff

            # This should never be reached due to raise in loop
            return None

        return wrapper

    return decorator
