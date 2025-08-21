from datetime import datetime, timedelta
import threading
import time
import logging
import os

# Setup logger for the circuit breaker module
logger = logging.getLogger(os.getenv("LOGGER_NAME", "HEALTH_CHECK_DAEMON"))



class CircuitBreaker:
    """
    Implements a simple thread-safe circuit breaker pattern to prevent
    repeated calls to failing services or components.

    States:
        - CLOSED: All calls are allowed.
        - OPEN: All calls are blocked for a period (timeout).
        - HALF_OPEN: A single call is allowed to test recovery.

    Attributes:
        threshold (int): Number of consecutive failures to trigger OPEN state.
        timeout (int): Duration in seconds the breaker remains OPEN before testing HALF_OPEN.
        failure_count (int): Current count of consecutive failures.
        last_failure (datetime): Timestamp of the last failure.
        state (str): One of "CLOSED", "OPEN", or "HALF_OPEN".
        lock (threading.Lock): Ensures thread-safe state transitions.
    """

    def __init__(self, threshold: int = 5, timeout: int = 300):
        """
        Initialize the circuit breaker.

        Args:
            threshold (int): Failure count before opening the circuit.
            timeout (int): Time in seconds to remain open before transitioning to HALF_OPEN.
        """
        self.threshold = threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure: datetime | None = None
        self.state = "CLOSED"
        self.lock = threading.Lock()

    def call(self, func, *args, **kwargs):
        """
        Execute a function wrapped in circuit breaker logic.

        - If in OPEN state, will block unless timeout has expired.
        - If in HALF_OPEN and the call succeeds, resets to CLOSED.
        - If the call fails, records a failure and may transition to OPEN.

        Args:
            func (callable): Function to execute.
            *args, **kwargs: Arguments to pass to the function.

        Returns:
            Result of the function call if successful.

        Raises:
            Exception: Propagates exceptions raised by `func` or from OPEN state.
        """
        with self.lock:
            if self.state == "OPEN":
                # If timeout expired, allow test call (HALF_OPEN)
                if self.last_failure and (datetime.now() - self.last_failure) > timedelta(seconds=self.timeout):
                    self.state = "HALF_OPEN"
                    logger.info(f"Circuit breaker transitioning to HALF_OPEN for {getattr(func,'__name__','func')}")
                else:
                    raise Exception("Circuit breaker OPEN")

        try:
            result = func(*args, **kwargs)
            if self.state == "HALF_OPEN":
                self.reset()
            return result
        except Exception:
            self.record_failure()
            raise

    def record_failure(self):
        """
        Record a failure and update circuit state if threshold is reached.
        """
        self.failure_count += 1
        self.last_failure = datetime.now()
        if self.failure_count >= self.threshold:
            self.state = "OPEN"
            logger.warning(f"Circuit breaker OPEN after {self.failure_count} failures")

    def reset(self):
        """
        Reset the breaker to the CLOSED state (used after a successful call in HALF_OPEN).
        """
        self.failure_count = 0
        self.last_failure = None
        self.state = "CLOSED"
        logger.info("Circuit breaker CLOSED - service recovered")


def exponential_backoff_retry(func, max_retries=3, initial_delay=1.0, max_delay=60.0, backoff_factor=2.0):
    """
    Retry a function using exponential backoff in case of exceptions.

    Args:
        func (callable): Function to call.
        max_retries (int): Maximum number of retry attempts.
        initial_delay (float): Initial wait time in seconds.
        max_delay (float): Maximum wait time between retries.
        backoff_factor (float): Multiplier for delay growth.

    Returns:
        Result of the function if it eventually succeeds.

    Raises:
        Exception: If all retries fail, the last exception is raised.
    """
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            if attempt == max_retries:
                raise
            delay = min(initial_delay * (backoff_factor ** attempt), max_delay)
            logger.warning(
                f"Attempt {attempt+1} failed for {getattr(func,'__name__','func')}: {e}. Retrying in {delay:.2f}s..."
            )
            time.sleep(delay)
