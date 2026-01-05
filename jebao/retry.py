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
                        device_id = ""
                        if args and hasattr(args[0], 'device_identifier'):
                            device_id = args[0].device_identifier + " "
                        _LOGGER.error(
                            f"{device_id}{func.__name__} failed after {max_attempts} attempts: {e}"
                        )
                        raise

                    # Check if error message indicates specific issues
                    error_msg = str(e).lower()
                    is_garbage_issue = (
                        "synchronization" in error_msg
                        or "garbage" in error_msg
                        or "invalid header" in error_msg
                    )
                    is_not_connected = "not connected" in error_msg or "connection closed" in error_msg

                    # Attempt reconnection if disconnected
                    if is_not_connected and args and hasattr(args[0], 'connect'):
                        device = args[0]
                        # Check if device has is_connected property
                        if hasattr(device, 'is_connected') and not device.is_connected:
                            device_id = device.device_identifier if hasattr(device, 'device_identifier') else ""
                            _LOGGER.info(
                                f"{device_id} {func.__name__} attempt {attempt}/{max_attempts}: "
                                f"Connection lost, attempting to reconnect..."
                            )
                            try:
                                await device.connect()
                                _LOGGER.info(f"{device_id} Reconnected successfully, retrying operation")
                                # Don't sleep after successful reconnection - retry immediately
                                attempt += 1
                                continue
                            except Exception as reconnect_err:
                                _LOGGER.warning(
                                    f"{device_id} Reconnection failed: {reconnect_err}, "
                                    f"will retry in {current_delay}s"
                                )

                    device_id = ""
                    if args and hasattr(args[0], 'device_identifier'):
                        device_id = args[0].device_identifier + " "

                    if is_garbage_issue:
                        _LOGGER.warning(
                            f"{device_id}{func.__name__} attempt {attempt}/{max_attempts} failed "
                            f"due to garbage bytes, retrying in {current_delay}s: {e}"
                        )
                    else:
                        _LOGGER.warning(
                            f"{device_id}{func.__name__} attempt {attempt}/{max_attempts} failed, "
                            f"retrying in {current_delay}s: {e}"
                        )

                    await asyncio.sleep(current_delay)
                    attempt += 1
                    current_delay *= backoff

            # This should never be reached due to raise in loop
            return None

        return wrapper

    return decorator
