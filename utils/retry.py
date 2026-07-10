"""
Retry utility — exponential backoff with jitter for tool calls.
Handles transient failures (timeouts, service errors) gracefully.
"""

import asyncio
import random
import logging

logger = logging.getLogger("agent.retry")


class ToolTimeoutError(Exception):
    """Tool call timed out."""
    pass


class ToolServiceError(Exception):
    """Tool service returned an error."""
    pass


class ToolValidationError(Exception):
    """Tool input or output failed validation."""
    pass


async def retry_with_backoff(
    func,
    *args,
    max_retries: int = 3,
    base_delay: float = 0.05,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: tuple = (ToolTimeoutError, ToolServiceError),
    **kwargs
) -> dict:
    """
    Execute an async function with exponential backoff retry logic.
    
    Args:
        func: Async callable to execute
        max_retries: Maximum retry attempts (0 = no retries)
        base_delay: Initial delay in seconds
        backoff_factor: Multiplier for each subsequent delay
        jitter: Add randomness to delay to prevent thundering herd
        retryable_exceptions: Exception types that trigger a retry
    
    Returns:
        dict with 'result', 'retries', 'success'
    """
    last_error = None
    retries_used = 0

    for attempt in range(max_retries + 1):
        try:
            result = await func(*args, **kwargs)
            return {
                "result": result,
                "retries": retries_used,
                "success": True
            }
        except retryable_exceptions as e:
            last_error = e
            retries_used = attempt + 1
            if attempt < max_retries:
                delay = base_delay * (backoff_factor ** attempt)
                if jitter:
                    delay *= (0.5 + random.random())
                logger.warning(
                    f"Retry {attempt + 1}/{max_retries} after {type(e).__name__}: {e}. "
                    f"Waiting {delay:.3f}s"
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    f"All {max_retries} retries exhausted for {func.__name__}: {e}"
                )
        except ToolValidationError:
            # Validation errors are not retryable
            raise
        except Exception as e:
            # Unexpected errors — don't retry
            logger.error(f"Unexpected error in {func.__name__}: {type(e).__name__}: {e}")
            raise

    return {
        "result": None,
        "retries": retries_used,
        "success": False,
        "error": str(last_error),
        "error_type": type(last_error).__name__
    }
