"""
Circuit Breaker Pattern Implementation with Structured Logging

This module implements the Circuit Breaker pattern to prevent cascading failures
in distributed systems. It provides automatic failure detection, service isolation,
and recovery capabilities with comprehensive structured logging for observability.

The Circuit Breaker pattern helps systems fail fast and recover gracefully by:
1. Monitoring service calls for failures
2. Opening the circuit when failure threshold is reached
3. Periodically testing for service recovery
4. Automatically closing the circuit when service recovers

Circuit Breaker States:
- CLOSED: Normal operation, all calls are allowed through
- OPEN: Service is failing, all calls are blocked (fail fast)
- HALF_OPEN: Testing recovery, single call allowed to test service health

Usage Example:
    from .circuit import CircuitBreaker
    from .structured_events import StructuredEventLogger
    
    logger = StructuredEventLogger("my_service")
    cb = CircuitBreaker(threshold=3, timeout=60, service_name="api_service", structured_logger=logger)
    
    try:
        result = cb.call(my_risky_function, arg1, arg2)
    except Exception as e:
        # Circuit breaker will handle failure counting and state transitions
        print(f"Call failed: {e}")

Thread Safety:
    All circuit breaker operations are thread-safe using threading.Lock.
    Multiple threads can safely use the same circuit breaker instance.

Integration with Structured Logging:
    This implementation integrates with the structured_events module to provide
    detailed observability into circuit breaker behavior, including:
    - State transitions with timestamps
    - Failure counts and error messages
    - Performance metrics and timing
    - Service recovery events

Author: MT GCP Daemon Team
Version: 1.1
Last Modified: 2025
"""

from datetime import datetime, timedelta
import threading
import time
import logging
import os
from typing import Callable, Any, Optional
from .structured_events import StructuredEventLogger

# Setup logger for the circuit breaker module
# Uses environment variable for logger name with fallback to default
logger = logging.getLogger(os.getenv("LOGGER_NAME", "HEALTH_CHECK_DAEMON"))


class CircuitBreaker:
    """
    Thread-safe Circuit Breaker implementation with structured logging integration.
    
    This class implements the Circuit Breaker pattern to prevent repeated calls to
    failing services, allowing systems to fail fast and recover gracefully.
    
    The circuit breaker maintains state and tracks failures across three states:
    - CLOSED: Normal operation, calls are allowed
    - OPEN: Service is failing, calls are blocked for a timeout period
    - HALF_OPEN: Testing recovery, single call allowed to test service health
    
    Attributes:
        threshold (int): Number of consecutive failures before opening the circuit
        timeout (int): Time in seconds to wait before testing recovery (OPEN -> HALF_OPEN)
        service_name (str): Human-readable name for the protected service
        structured_logger (StructuredEventLogger): Logger for structured events
        failure_count (int): Current count of consecutive failures
        last_failure (datetime): Timestamp of the most recent failure
        state (str): Current circuit breaker state ("CLOSED", "OPEN", "HALF_OPEN")
        lock (threading.Lock): Thread synchronization lock for state changes
    
    Thread Safety:
        All public methods are thread-safe. Multiple threads can safely call
        the same circuit breaker instance without external synchronization.
    """

    def __init__(self,
                 threshold: int = 5,
                 timeout: int = 300,
                 service_name: str = "unknown",
                 structured_logger: Optional[StructuredEventLogger] = None):
        """
        Initialize a new Circuit Breaker instance.
        
        Args:
            threshold (int, optional): Number of consecutive failures before opening
                the circuit. Must be >= 1. Defaults to 5.
            timeout (int, optional): Time in seconds to remain in OPEN state before
                transitioning to HALF_OPEN. Must be >= 1. Defaults to 300 (5 minutes).
            service_name (str, optional): Human-readable name for the protected service.
                Used in logs and error messages. Defaults to "unknown".
            structured_logger (StructuredEventLogger, optional): Logger instance for
                structured events. If None, only standard logging is performed.
                
        Raises:
            ValueError: If threshold < 1 or timeout < 1
            
        Example:
            # Basic circuit breaker
            cb = CircuitBreaker(threshold=3, timeout=60, service_name="payment_api")
            
            # With structured logging
            logger = StructuredEventLogger("payment_service")
            cb = CircuitBreaker(threshold=5, timeout=300, service_name="payment_api", 
                              structured_logger=logger)
        """
        if threshold < 1:
            raise ValueError("Threshold must be >= 1")
        if timeout < 1:
            raise ValueError("Timeout must be >= 1")
            
        self.threshold = threshold
        self.timeout = timeout
        self.service_name = service_name
        self.structured_logger = structured_logger
        
        # Circuit breaker state
        self.failure_count = 0
        self.last_failure: Optional[datetime] = None
        self.state = "CLOSED"
        
        # Thread safety
        self.lock = threading.Lock()
        
        logger.debug(f"Circuit breaker initialized for {service_name} "
                    f"(threshold={threshold}, timeout={timeout}s)")

    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute a function protected by the circuit breaker.
        
        This method wraps the execution of the provided function with circuit breaker
        logic. Depending on the current state:
        
        - CLOSED: Function is called normally
        - OPEN: If timeout has not expired, call is blocked with exception
                If timeout has expired, transition to HALF_OPEN and allow call
        - HALF_OPEN: Function is called, and based on result:
                     Success -> Reset to CLOSED state
                     Failure -> Record failure and potentially open circuit
        
        Args:
            func (Callable): The function to execute under circuit breaker protection
            *args: Positional arguments to pass to the function
            **kwargs: Keyword arguments to pass to the function
            
        Returns:
            Any: The return value of the protected function if successful
            
        Raises:
            Exception: Re-raises any exception from the protected function
            Exception: Raises "Circuit breaker OPEN" if circuit is open and timeout
                      has not expired
                      
        Thread Safety:
            This method is thread-safe. State checking and modification are
            protected by an internal lock.
            
        Example:
            def risky_api_call(user_id):
                # Some operation that might fail
                return external_service.get_user(user_id)
            
            cb = CircuitBreaker(threshold=3, timeout=60, service_name="user_api")
            
            try:
                user = cb.call(risky_api_call, user_id=123)
                print(f"Got user: {user}")
            except Exception as e:
                print(f"API call failed: {e}")
        """
        # Thread-safe state checking and transition logic
        with self.lock:
            if self.state == "OPEN":
                # Check if enough time has passed to test recovery
                if (self.last_failure and
                    (datetime.now() - self.last_failure) > timedelta(seconds=self.timeout)):
                    
                    # Transition to HALF_OPEN for recovery testing
                    old_state = self.state
                    self.state = "HALF_OPEN"
                    
                    logger.info(f"Circuit breaker transitioning to HALF_OPEN for "
                               f"{getattr(func, '__name__', 'func')} (service: {self.service_name})")
                    
                    # Log state transition event
                    if self.structured_logger:
                        self.structured_logger.log_circuit_breaker_event(
                            service=self.service_name,
                            event_name="half_open",
                            failure_count=self.failure_count
                        )
                else:
                    # Circuit is still open, block the call
                    if self.structured_logger:
                        time_remaining = int((timedelta(seconds=self.timeout) -
                                            (datetime.now() - self.last_failure)).total_seconds())
                        self.structured_logger.log_circuit_breaker_event(
                            service=self.service_name,
                            event_name="call_blocked",
                            failure_count=self.failure_count,
                            error_message=f"Circuit breaker OPEN, {time_remaining}s remaining"
                        )
                    
                    # Block the call by raising an exception
                    raise Exception(f"Circuit breaker OPEN for {self.service_name}")

        # Execute the protected function
        try:
            result = func(*args, **kwargs)
            
            # If we reach here, the call succeeded
            # If we were in HALF_OPEN state, reset to CLOSED
            if self.state == "HALF_OPEN":
                self.reset()
                
            return result
            
        except Exception as e:
            # Function call failed, record the failure
            self.record_failure(str(e))
            # Re-raise the original exception
            raise

    def record_failure(self, error_message: Optional[str] = None) -> None:
        """
        Record a failure and potentially open the circuit if threshold is reached.
        
        This method is called whenever a protected function call fails. It:
        1. Increments the failure counter
        2. Updates the last failure timestamp
        3. Checks if failure threshold is reached
        4. Opens the circuit if threshold is exceeded
        5. Logs structured events for observability
        
        Args:
            error_message (str, optional): Error message from the failed call.
                Used for structured logging and debugging.
                
        Thread Safety:
            This method modifies shared state and should be called with appropriate
            locking. The call() method handles this automatically.
            
        Side Effects:
            - Increments failure_count
            - Updates last_failure timestamp
            - May change state from CLOSED/HALF_OPEN to OPEN
            - Generates structured log events
            
        Example:
            # This is typically called internally by call() method
            # But can be used directly for manual failure recording
            cb.record_failure("Database connection timeout")
        """
        old_state = self.state
        self.failure_count += 1
        self.last_failure = datetime.now()
        
        # Check if we should open the circuit
        if self.failure_count >= self.threshold and self.state != "OPEN":
            self.state = "OPEN"
            
            logger.warning(f"Circuit breaker OPEN for {self.service_name} after "
                          f"{self.failure_count} failures")
            
            # Log circuit breaker opened event
            if self.structured_logger:
                self.structured_logger.log_circuit_breaker_event(
                    service=self.service_name,
                    event_name="opened",
                    failure_count=self.failure_count,
                    error_message=error_message
                )
        else:
            # Failure recorded but threshold not reached
            logger.debug(f"Circuit breaker failure recorded for {self.service_name} "
                        f"({self.failure_count}/{self.threshold})")
            
            # Log failure recorded event
            if self.structured_logger:
                self.structured_logger.log_circuit_breaker_event(
                    service=self.service_name,
                    event_name="failure_recorded",
                    failure_count=self.failure_count,
                    error_message=error_message
                )

    def reset(self) -> None:
        """
        Reset the circuit breaker to CLOSED state (healthy operation).
        
        This method is called when a service call succeeds while in HALF_OPEN state,
        indicating that the service has recovered. It:
        1. Resets failure count to 0
        2. Clears the last failure timestamp
        3. Sets state to CLOSED
        4. Logs the recovery event
        
        Thread Safety:
            This method modifies shared state and should be called with appropriate
            locking. The call() method handles this automatically.
            
        Side Effects:
            - Resets failure_count to 0
            - Clears last_failure timestamp
            - Changes state to CLOSED
            - Generates structured log events
            
        Note:
            This method is typically called automatically by call() when a function
            succeeds in HALF_OPEN state. Manual calls should be rare but may be
            useful for testing or administrative reset operations.
            
        Example:
            # Manual reset (rare)
            cb.reset()
            print(f"Circuit breaker manually reset for {cb.service_name}")
        """
        old_failure_count = self.failure_count
        
        # Reset circuit breaker state to healthy
        self.failure_count = 0
        self.last_failure = None
        self.state = "CLOSED"
        
        logger.info(f"Circuit breaker CLOSED for {self.service_name} - service recovered "
                   f"(was {old_failure_count} failures)")
        
        # Log recovery event
        if self.structured_logger:
            self.structured_logger.log_circuit_breaker_event(
                service=self.service_name,
                event_name="closed",
                failure_count=old_failure_count
            )

    def get_state(self) -> dict:
        """
        Get current circuit breaker state for monitoring and debugging.
        
        Returns a dictionary containing the current state of the circuit breaker,
        useful for health checks, monitoring dashboards, or debugging.
        
        Returns:
            dict: Dictionary containing:
                - state (str): Current state ("CLOSED", "OPEN", "HALF_OPEN")
                - failure_count (int): Current number of consecutive failures
                - threshold (int): Failure threshold for opening circuit
                - timeout (int): Timeout in seconds for OPEN state
                - service_name (str): Name of the protected service
                - last_failure (str or None): ISO timestamp of last failure
                - time_until_retry (int or None): Seconds until retry (if OPEN)
                
        Thread Safety:
            This method only reads state and is safe to call from any thread.
            
        Example:
            cb = CircuitBreaker(threshold=5, timeout=300, service_name="payment_api")
            state = cb.get_state()
            print(f"Circuit breaker state: {state['state']}")
            print(f"Failures: {state['failure_count']}/{state['threshold']}")
        """
        with self.lock:
            state_info = {
                "state": self.state,
                "failure_count": self.failure_count,
                "threshold": self.threshold,
                "timeout": self.timeout,
                "service_name": self.service_name,
                "last_failure": self.last_failure.isoformat() if self.last_failure else None
            }
            
            # Calculate time until retry for OPEN state
            if self.state == "OPEN" and self.last_failure:
                time_elapsed = (datetime.now() - self.last_failure).total_seconds()
                time_until_retry = max(0, self.timeout - int(time_elapsed))
                state_info["time_until_retry"] = time_until_retry
            else:
                state_info["time_until_retry"] = None
                
        return state_info


def exponential_backoff_retry(func: Callable,
                            max_retries: int = 3,
                            initial_delay: float = 1.0,
                            max_delay: float = 60.0,
                            backoff_factor: float = 2.0) -> Any:
    """
    Retry a function with exponential backoff on failure.
    
    This function implements an exponential backoff retry strategy, which is useful
    for handling transient failures in distributed systems. The delay between
    retries increases exponentially, helping to avoid overwhelming a recovering service.
    
    Retry Strategy:
        - First retry: initial_delay seconds
        - Second retry: initial_delay * backoff_factor seconds
        - Third retry: initial_delay * backoff_factor^2 seconds
        - ... and so on, capped at max_delay
    
    This function works well in combination with CircuitBreaker for comprehensive
    resilience patterns:
    - CircuitBreaker prevents calls to failing services
    - exponential_backoff_retry handles transient failures gracefully
    
    Args:
        func (Callable): The function to retry. Should be a callable with no arguments
            or a lambda/partial function with arguments already bound.
        max_retries (int, optional): Maximum number of retry attempts. Total attempts
            will be max_retries + 1 (initial attempt + retries). Must be >= 0.
            Defaults to 3.
        initial_delay (float, optional): Initial delay in seconds before first retry.
            Must be > 0. Defaults to 1.0.
        max_delay (float, optional): Maximum delay in seconds between retries.
            Must be >= initial_delay. Defaults to 60.0.
        backoff_factor (float, optional): Multiplier for delay calculation.
            Must be >= 1.0. Defaults to 2.0 (doubles delay each retry).
            
    Returns:
        Any: The return value of the function if it eventually succeeds
        
    Raises:
        Exception: The last exception raised by the function if all retries fail
        ValueError: If parameters are invalid (negative retries, invalid delays, etc.)
        
    Thread Safety:
        This function is thread-safe as long as the provided function is thread-safe.
        Each retry attempt is independent and no shared state is maintained.
        
    Examples:
        # Basic retry with defaults
        def flaky_api_call():
            response = requests.get("https://api.example.com/data")
            response.raise_for_status()
            return response.json()
        
        try:
            data = exponential_backoff_retry(flaky_api_call)
            print(f"Got data: {data}")
        except Exception as e:
            print(f"All retries failed: {e}")
        
        # Custom retry parameters
        def database_operation():
            return db.execute_query("SELECT * FROM users")
        
        result = exponential_backoff_retry(
            database_operation,
            max_retries=5,
            initial_delay=0.5,
            max_delay=30.0,
            backoff_factor=1.5
        )
        
        # With lambda for functions with arguments
        user_data = exponential_backoff_retry(
            lambda: api_client.get_user(user_id=123),
            max_retries=3
        )
        
        # Combined with CircuitBreaker
        cb = CircuitBreaker(threshold=5, timeout=300, service_name="api")
        result = cb.call(
            exponential_backoff_retry,
            lambda: external_service.call(),
            max_retries=3
        )
    
    Performance Considerations:
        - Total retry time can be significant: plan for timeout handling
        - With default parameters (3 retries, factor 2.0), total time could be:
          attempt + 1s + 2s + 4s = ~7+ seconds
        - Consider lower max_retries for user-facing operations
        - Consider higher max_retries for background/batch operations
        
    Error Handling Best Practices:
        - Log retry attempts for debugging (this function logs automatically)
        - Consider different retry strategies for different error types
        - Set reasonable timeouts on the underlying function calls
        - Monitor retry rates to detect systemic issues
    """
    # Parameter validation
    if max_retries < 0:
        raise ValueError("max_retries must be >= 0")
    if initial_delay <= 0:
        raise ValueError("initial_delay must be > 0")
    if max_delay < initial_delay:
        raise ValueError("max_delay must be >= initial_delay")
    if backoff_factor < 1.0:
        raise ValueError("backoff_factor must be >= 1.0")
    
    last_exception = None
    func_name = getattr(func, '__name__', 'anonymous_function')
    
    # Main retry loop
    for attempt in range(max_retries + 1):  # +1 for initial attempt
        try:
            # Attempt to call the function
            logger.debug(f"Attempting call to {func_name} (attempt {attempt + 1}/{max_retries + 1})")
            return func()
            
        except Exception as e:
            last_exception = e
            
            # If this was the last attempt, log final failure and re-raise
            if attempt == max_retries:
                logger.error(f"All {max_retries + 1} attempts failed for {func_name}: {e}")
                raise
            
            # Calculate delay for next attempt using exponential backoff
            # Formula: min(initial_delay * (backoff_factor ^ attempt), max_delay)
            delay = min(initial_delay * (backoff_factor ** attempt), max_delay)
            
            # Log the failure and upcoming retry
            logger.warning(f"Attempt {attempt + 1}/{max_retries + 1} failed for {func_name}: {e}. "
                          f"Retrying in {delay:.2f}s...")
            
            # Wait before next retry
            time.sleep(delay)
    
    # This should never be reached due to the raise in the exception handler,
    # but included for completeness
    if last_exception:
        raise last_exception
    else:
        raise RuntimeError(f"Unexpected state: no exception but all retries exhausted for {func_name}")


# Module-level constants for common circuit breaker configurations
# These can be imported and used as starting points for different use cases

# Conservative settings for critical services
CONSERVATIVE_CB_CONFIG = {
    "threshold": 3,      # Open after 3 failures
    "timeout": 300,      # Wait 5 minutes before retry
}

# Aggressive settings for non-critical services
AGGRESSIVE_CB_CONFIG = {
    "threshold": 10,     # Open after 10 failures
    "timeout": 60,       # Wait 1 minute before retry
}

# Quick recovery settings for fast services
QUICK_RECOVERY_CB_CONFIG = {
    "threshold": 5,      # Open after 5 failures
    "timeout": 30,       # Wait 30 seconds before retry
}

# Example usage documentation
if __name__ == "__main__":
    """
    Example usage and testing of circuit breaker functionality.
    
    This section demonstrates how to use the CircuitBreaker class
    and exponential_backoff_retry function in various scenarios.
    """
    
    def example_failing_function():
        """Simulate a function that fails occasionally"""
        import random
        if random.random() < 0.7:  # 70% failure rate
            raise Exception("Simulated service failure")
        return "Success!"
    
    def example_always_failing_function():
        """Simulate a function that always fails"""
        raise Exception("Service permanently down")
    
    # Example 1: Basic circuit breaker usage
    print("=== Example 1: Basic Circuit Breaker ===")
    cb = CircuitBreaker(threshold=3, timeout=10, service_name="example_service")
    
    for i in range(10):
        try:
            result = cb.call(example_failing_function)
            print(f"Call {i+1}: {result}")
        except Exception as e:
            print(f"Call {i+1}: Failed - {e}")
        time.sleep(1)
    
    # Example 2: Exponential backoff retry
    print("\n=== Example 2: Exponential Backoff Retry ===")
    try:
        result = exponential_backoff_retry(
            example_failing_function,
            max_retries=3,
            initial_delay=0.5
        )
        print(f"Retry succeeded: {result}")
    except Exception as e:
        print(f"All retries failed: {e}")
    
    # Example 3: Combined circuit breaker and retry
    print("\n=== Example 3: Combined Pattern ===")
    cb_combined = CircuitBreaker(threshold=2, timeout=5, service_name="combined_service")
    
    def retry_with_backoff():
        return exponential_backoff_retry(
            example_failing_function,
            max_retries=2,
            initial_delay=0.1
        )
    
    for i in range(5):
        try:
            result = cb_combined.call(retry_with_backoff)
            print(f"Combined call {i+1}: {result}")
        except Exception as e:
            print(f"Combined call {i+1}: Failed - {e}")
        time.sleep(2)
