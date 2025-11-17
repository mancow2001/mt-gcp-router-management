"""
Unit Tests for Circuit Breaker Pattern Implementation

This test module comprehensively validates the CircuitBreaker class and
exponential_backoff_retry function, ensuring correct behavior across all
states and edge cases.

Test Coverage:
    - Circuit breaker initialization and validation
    - CLOSED state behavior (normal operation)
    - Failure tracking and threshold detection
    - OPEN state behavior (call blocking)
    - HALF_OPEN state behavior (recovery testing)
    - State transitions (CLOSED → OPEN → HALF_OPEN → CLOSED)
    - Circuit breaker reset functionality
    - Exponential backoff retry logic
    - Thread safety validation
    - Error handling and edge cases

Author: Nathan Bray
Created: 2025-11-01
"""

import unittest
from unittest.mock import Mock, MagicMock, patch, call
import time
from datetime import datetime, timedelta
import threading

# Import the circuit breaker components
try:
    from .circuit import CircuitBreaker, exponential_backoff_retry
except ImportError:
    from circuit import CircuitBreaker, exponential_backoff_retry


class TestCircuitBreakerInitialization(unittest.TestCase):
    """Test suite for CircuitBreaker initialization and validation."""

    def test_init_with_defaults(self):
        """Test circuit breaker initialization with default parameters."""
        cb = CircuitBreaker()
        self.assertEqual(cb.threshold, 5)
        self.assertEqual(cb.timeout, 300)
        self.assertEqual(cb.service_name, "unknown")
        self.assertEqual(cb.failure_count, 0)
        self.assertIsNone(cb.last_failure)
        self.assertEqual(cb.state, "CLOSED")
        self.assertIsNotNone(cb.lock)

    def test_init_with_custom_parameters(self):
        """Test circuit breaker initialization with custom parameters."""
        cb = CircuitBreaker(threshold=3, timeout=60, service_name="test_service")
        self.assertEqual(cb.threshold, 3)
        self.assertEqual(cb.timeout, 60)
        self.assertEqual(cb.service_name, "test_service")
        self.assertEqual(cb.state, "CLOSED")

    def test_init_with_structured_logger(self):
        """Test circuit breaker initialization with structured logger."""
        mock_logger = Mock()
        cb = CircuitBreaker(threshold=5, timeout=300,
                           service_name="test", structured_logger=mock_logger)
        self.assertEqual(cb.structured_logger, mock_logger)

    def test_init_invalid_threshold_zero(self):
        """Test that threshold of 0 raises ValueError."""
        with self.assertRaises(ValueError) as context:
            CircuitBreaker(threshold=0)
        self.assertIn("Threshold must be >= 1", str(context.exception))

    def test_init_invalid_threshold_negative(self):
        """Test that negative threshold raises ValueError."""
        with self.assertRaises(ValueError) as context:
            CircuitBreaker(threshold=-1)
        self.assertIn("Threshold must be >= 1", str(context.exception))

    def test_init_invalid_timeout_zero(self):
        """Test that timeout of 0 raises ValueError."""
        with self.assertRaises(ValueError) as context:
            CircuitBreaker(timeout=0)
        self.assertIn("Timeout must be >= 1", str(context.exception))

    def test_init_invalid_timeout_negative(self):
        """Test that negative timeout raises ValueError."""
        with self.assertRaises(ValueError) as context:
            CircuitBreaker(timeout=-1)
        self.assertIn("Timeout must be >= 1", str(context.exception))

    def test_init_minimum_valid_values(self):
        """Test circuit breaker with minimum valid threshold and timeout."""
        cb = CircuitBreaker(threshold=1, timeout=1)
        self.assertEqual(cb.threshold, 1)
        self.assertEqual(cb.timeout, 1)


class TestCircuitBreakerClosedState(unittest.TestCase):
    """Test suite for circuit breaker behavior in CLOSED state."""

    def test_call_success_in_closed_state(self):
        """Test successful function call in CLOSED state."""
        cb = CircuitBreaker(threshold=3, timeout=60)

        def successful_func():
            return "success"

        result = cb.call(successful_func)
        self.assertEqual(result, "success")
        self.assertEqual(cb.state, "CLOSED")
        self.assertEqual(cb.failure_count, 0)

    def test_call_with_args_and_kwargs(self):
        """Test function call with positional and keyword arguments."""
        cb = CircuitBreaker(threshold=3, timeout=60)

        def func_with_args(a, b, c=None):
            return f"{a}-{b}-{c}"

        result = cb.call(func_with_args, "x", "y", c="z")
        self.assertEqual(result, "x-y-z")

    def test_single_failure_below_threshold(self):
        """Test that single failure below threshold doesn't open circuit."""
        cb = CircuitBreaker(threshold=3, timeout=60)

        def failing_func():
            raise Exception("Test failure")

        with self.assertRaises(Exception):
            cb.call(failing_func)

        self.assertEqual(cb.state, "CLOSED")
        self.assertEqual(cb.failure_count, 1)
        self.assertIsNotNone(cb.last_failure)

    def test_multiple_failures_below_threshold(self):
        """Test multiple failures below threshold keep circuit closed."""
        cb = CircuitBreaker(threshold=5, timeout=60)

        def failing_func():
            raise Exception("Test failure")

        # Fail 4 times (below threshold of 5)
        for i in range(4):
            with self.assertRaises(Exception):
                cb.call(failing_func)

        self.assertEqual(cb.state, "CLOSED")
        self.assertEqual(cb.failure_count, 4)


class TestCircuitBreakerFailureTracking(unittest.TestCase):
    """Test suite for failure counting and threshold detection."""

    def test_failure_count_increments(self):
        """Test that failure count increments correctly."""
        cb = CircuitBreaker(threshold=5, timeout=60)

        def failing_func():
            raise Exception("Test failure")

        for i in range(3):
            try:
                cb.call(failing_func)
            except:
                pass

        self.assertEqual(cb.failure_count, 3)

    def test_threshold_reached_opens_circuit(self):
        """Test that reaching threshold opens the circuit."""
        cb = CircuitBreaker(threshold=3, timeout=60)

        def failing_func():
            raise Exception("Test failure")

        # Fail exactly threshold times
        for i in range(3):
            try:
                cb.call(failing_func)
            except:
                pass

        self.assertEqual(cb.state, "OPEN")
        self.assertEqual(cb.failure_count, 3)

    def test_record_failure_with_error_message(self):
        """Test recording failure with error message."""
        mock_logger = Mock()
        cb = CircuitBreaker(threshold=3, timeout=60,
                           service_name="test", structured_logger=mock_logger)

        cb.record_failure("Custom error message")

        self.assertEqual(cb.failure_count, 1)
        # Verify structured logger was called
        mock_logger.log_circuit_breaker_event.assert_called_once()

    def test_last_failure_timestamp_updated(self):
        """Test that last_failure timestamp is updated on failure."""
        cb = CircuitBreaker(threshold=5, timeout=60)

        before_time = datetime.now()
        cb.record_failure("Test error")
        after_time = datetime.now()

        self.assertIsNotNone(cb.last_failure)
        self.assertGreaterEqual(cb.last_failure, before_time)
        self.assertLessEqual(cb.last_failure, after_time)


class TestCircuitBreakerOpenState(unittest.TestCase):
    """Test suite for circuit breaker behavior in OPEN state."""

    def test_open_state_blocks_calls(self):
        """Test that OPEN state blocks function calls."""
        cb = CircuitBreaker(threshold=2, timeout=60)

        def failing_func():
            raise Exception("Test failure")

        # Open the circuit
        for i in range(2):
            try:
                cb.call(failing_func)
            except:
                pass

        self.assertEqual(cb.state, "OPEN")

        # Next call should be blocked
        def never_called_func():
            self.fail("Function should not be called when circuit is OPEN")

        with self.assertRaises(Exception) as context:
            cb.call(never_called_func)

        self.assertIn("Circuit breaker OPEN", str(context.exception))

    def test_open_state_with_structured_logging(self):
        """Test that OPEN state logs call_blocked event."""
        mock_logger = Mock()
        cb = CircuitBreaker(threshold=2, timeout=60,
                           service_name="test", structured_logger=mock_logger)

        # Open the circuit
        for i in range(2):
            cb.record_failure("Test")

        self.assertEqual(cb.state, "OPEN")

        # Try to call - should be blocked and logged
        with self.assertRaises(Exception):
            cb.call(lambda: "test")

        # Verify call_blocked event was logged
        calls = mock_logger.log_circuit_breaker_event.call_args_list
        event_names = [call[1]['event_name'] for call in calls]
        self.assertIn("call_blocked", event_names)

    def test_open_state_transition_to_half_open_after_timeout(self):
        """Test transition from OPEN to HALF_OPEN after timeout."""
        cb = CircuitBreaker(threshold=2, timeout=10)

        # Open the circuit
        for i in range(2):
            cb.record_failure("Test")

        self.assertEqual(cb.state, "OPEN")

        # Wait for timeout to pass (add small buffer for test reliability)
        import time
        time.sleep(10.1)

        # Call should now transition to HALF_OPEN
        def successful_func():
            return "success"

        result = cb.call(successful_func)

        self.assertEqual(result, "success")
        self.assertEqual(cb.state, "CLOSED")  # Should be closed after successful HALF_OPEN call


class TestCircuitBreakerHalfOpenState(unittest.TestCase):
    """Test suite for circuit breaker behavior in HALF_OPEN state."""

    def test_half_open_success_closes_circuit(self):
        """Test that successful call in HALF_OPEN closes the circuit."""
        cb = CircuitBreaker(threshold=2, timeout=1)  # Use 1 second timeout for faster test

        # Open the circuit
        for i in range(2):
            cb.record_failure("Test")

        self.assertEqual(cb.state, "OPEN")

        # Wait for timeout to pass
        import time
        time.sleep(1.1)

        # Successful call should close circuit
        result = cb.call(lambda: "success")

        self.assertEqual(result, "success")
        self.assertEqual(cb.state, "CLOSED")
        self.assertEqual(cb.failure_count, 0)

    def test_half_open_failure_reopens_circuit(self):
        """Test that failed call in HALF_OPEN reopens the circuit."""
        cb = CircuitBreaker(threshold=2, timeout=1)  # Use 1 second timeout for faster test

        # Open the circuit
        for i in range(2):
            cb.record_failure("Test")

        self.assertEqual(cb.state, "OPEN")

        # Wait for timeout to pass
        import time
        time.sleep(1.1)

        # Failed call should reopen circuit
        with self.assertRaises(Exception):
            cb.call(lambda: (_ for _ in ()).throw(Exception("Failure")))

        self.assertEqual(cb.state, "OPEN")
        self.assertGreater(cb.failure_count, 0)

    def test_half_open_logging(self):
        """Test that HALF_OPEN transition is logged."""
        mock_logger = Mock()
        cb = CircuitBreaker(threshold=2, timeout=1,  # Use 1 second timeout for faster test
                           service_name="test", structured_logger=mock_logger)

        # Open the circuit
        for i in range(2):
            cb.record_failure("Test")

        # Wait for timeout to pass
        import time
        time.sleep(1.1)

        # Trigger HALF_OPEN transition
        cb.call(lambda: "success")

        # Verify half_open event was logged
        calls = mock_logger.log_circuit_breaker_event.call_args_list
        event_names = [call[1]['event_name'] for call in calls]
        self.assertIn("half_open", event_names)


class TestCircuitBreakerReset(unittest.TestCase):
    """Test suite for circuit breaker reset functionality."""

    def test_reset_clears_failure_count(self):
        """Test that reset clears failure count."""
        cb = CircuitBreaker(threshold=5, timeout=60)

        # Record some failures
        for i in range(3):
            cb.record_failure("Test")

        self.assertEqual(cb.failure_count, 3)

        # Reset
        cb.reset()

        self.assertEqual(cb.failure_count, 0)

    def test_reset_clears_last_failure(self):
        """Test that reset clears last_failure timestamp."""
        cb = CircuitBreaker(threshold=5, timeout=60)

        cb.record_failure("Test")
        self.assertIsNotNone(cb.last_failure)

        cb.reset()
        self.assertIsNone(cb.last_failure)

    def test_reset_closes_circuit(self):
        """Test that reset changes state to CLOSED."""
        cb = CircuitBreaker(threshold=2, timeout=60)

        # Open the circuit
        for i in range(2):
            cb.record_failure("Test")

        self.assertEqual(cb.state, "OPEN")

        # Reset
        cb.reset()

        self.assertEqual(cb.state, "CLOSED")

    def test_reset_with_structured_logging(self):
        """Test that reset logs closed event."""
        mock_logger = Mock()
        cb = CircuitBreaker(threshold=2, timeout=60,
                           service_name="test", structured_logger=mock_logger)

        # Open and reset
        for i in range(2):
            cb.record_failure("Test")

        cb.reset()

        # Verify closed event was logged
        calls = mock_logger.log_circuit_breaker_event.call_args_list
        event_names = [call[1]['event_name'] for call in calls]
        self.assertIn("closed", event_names)


class TestCircuitBreakerGetState(unittest.TestCase):
    """Test suite for get_state() method."""

    def test_get_state_closed(self):
        """Test get_state returns correct info for CLOSED state."""
        cb = CircuitBreaker(threshold=5, timeout=300, service_name="test_service")

        state = cb.get_state()

        self.assertEqual(state['state'], "CLOSED")
        self.assertEqual(state['failure_count'], 0)
        self.assertEqual(state['threshold'], 5)
        self.assertEqual(state['timeout'], 300)
        self.assertEqual(state['service_name'], "test_service")
        self.assertIsNone(state['last_failure'])
        self.assertIsNone(state['time_until_retry'])

    def test_get_state_open_with_time_until_retry(self):
        """Test get_state returns time_until_retry for OPEN state."""
        cb = CircuitBreaker(threshold=2, timeout=60)

        # Open the circuit
        for i in range(2):
            cb.record_failure("Test")

        state = cb.get_state()

        self.assertEqual(state['state'], "OPEN")
        self.assertEqual(state['failure_count'], 2)
        self.assertIsNotNone(state['last_failure'])
        self.assertIsNotNone(state['time_until_retry'])
        self.assertGreaterEqual(state['time_until_retry'], 0)
        self.assertLessEqual(state['time_until_retry'], 60)

    def test_get_state_with_failures(self):
        """Test get_state shows failure count when circuit is still closed."""
        cb = CircuitBreaker(threshold=5, timeout=60)

        cb.record_failure("Test 1")
        cb.record_failure("Test 2")

        state = cb.get_state()

        self.assertEqual(state['state'], "CLOSED")
        self.assertEqual(state['failure_count'], 2)
        self.assertIsNotNone(state['last_failure'])


class TestExponentialBackoffRetry(unittest.TestCase):
    """Test suite for exponential_backoff_retry function."""

    def test_successful_first_attempt(self):
        """Test that successful first attempt returns immediately."""
        def successful_func():
            return "success"

        result = exponential_backoff_retry(successful_func, max_retries=3)
        self.assertEqual(result, "success")

    def test_retry_on_failure(self):
        """Test that function is retried on failure."""
        call_count = [0]

        def flaky_func():
            call_count[0] += 1
            if call_count[0] < 3:
                raise Exception("Transient failure")
            return "success"

        result = exponential_backoff_retry(flaky_func, max_retries=5, initial_delay=0.01)
        self.assertEqual(result, "success")
        self.assertEqual(call_count[0], 3)

    @patch('gcp_route_mgmt_daemon.circuit.time.sleep')
    def test_retry_delay_calculation(self, mock_sleep):
        """Test that retry delays follow exponential backoff."""
        call_count = [0]

        def always_fails():
            call_count[0] += 1
            raise Exception("Always fails")

        with self.assertRaises(Exception):
            exponential_backoff_retry(
                always_fails,
                max_retries=3,
                initial_delay=1.0,
                backoff_factor=2.0
            )

        # Verify sleep was called with correct delays: 1, 2, 4
        expected_delays = [1.0, 2.0, 4.0]
        actual_delays = [call[0][0] for call in mock_sleep.call_args_list]
        self.assertEqual(actual_delays, expected_delays)

    @patch('gcp_route_mgmt_daemon.circuit.time.sleep')
    def test_max_delay_enforcement(self, mock_sleep):
        """Test that delays are capped at max_delay."""
        def always_fails():
            raise Exception("Always fails")

        with self.assertRaises(Exception):
            exponential_backoff_retry(
                always_fails,
                max_retries=5,
                initial_delay=10.0,
                max_delay=20.0,
                backoff_factor=2.0
            )

        # All delays should be capped at 20.0
        for call_args in mock_sleep.call_args_list:
            self.assertLessEqual(call_args[0][0], 20.0)

    def test_all_retries_exhausted(self):
        """Test that exception is raised when all retries are exhausted."""
        def always_fails():
            raise Exception("Permanent failure")

        with self.assertRaises(Exception) as context:
            exponential_backoff_retry(always_fails, max_retries=2, initial_delay=0.01)

        self.assertIn("Permanent failure", str(context.exception))

    def test_zero_retries(self):
        """Test behavior with max_retries=0 (only initial attempt)."""
        call_count = [0]

        def counting_func():
            call_count[0] += 1
            raise Exception("Failure")

        with self.assertRaises(Exception):
            exponential_backoff_retry(counting_func, max_retries=0)

        self.assertEqual(call_count[0], 1)  # Only initial attempt

    def test_invalid_max_retries(self):
        """Test that negative max_retries raises ValueError."""
        with self.assertRaises(ValueError) as context:
            exponential_backoff_retry(lambda: None, max_retries=-1)
        self.assertIn("max_retries must be >= 0", str(context.exception))

    def test_invalid_initial_delay(self):
        """Test that zero or negative initial_delay raises ValueError."""
        with self.assertRaises(ValueError) as context:
            exponential_backoff_retry(lambda: None, initial_delay=0)
        self.assertIn("initial_delay must be > 0", str(context.exception))

        with self.assertRaises(ValueError) as context:
            exponential_backoff_retry(lambda: None, initial_delay=-1)
        self.assertIn("initial_delay must be > 0", str(context.exception))

    def test_invalid_max_delay(self):
        """Test that max_delay < initial_delay raises ValueError."""
        with self.assertRaises(ValueError) as context:
            exponential_backoff_retry(lambda: None, initial_delay=10, max_delay=5)
        self.assertIn("max_delay must be >= initial_delay", str(context.exception))

    def test_invalid_backoff_factor(self):
        """Test that backoff_factor < 1.0 raises ValueError."""
        with self.assertRaises(ValueError) as context:
            exponential_backoff_retry(lambda: None, backoff_factor=0.5)
        self.assertIn("backoff_factor must be >= 1.0", str(context.exception))

    def test_custom_backoff_factor(self):
        """Test custom backoff factor (e.g., 1.5 instead of 2.0)."""
        call_count = [0]

        def flaky_func():
            call_count[0] += 1
            if call_count[0] < 4:
                raise Exception("Failure")
            return "success"

        result = exponential_backoff_retry(
            flaky_func,
            max_retries=5,
            initial_delay=0.01,
            backoff_factor=1.5
        )
        self.assertEqual(result, "success")


class TestThreadSafety(unittest.TestCase):
    """Test suite for thread safety of circuit breaker."""

    def test_concurrent_calls_thread_safety(self):
        """Test that concurrent calls don't corrupt circuit breaker state."""
        cb = CircuitBreaker(threshold=10, timeout=60)
        results = []
        errors = []

        def concurrent_func(should_fail):
            try:
                result = cb.call(lambda: self._helper_func(should_fail))
                results.append(result)
            except Exception as e:
                errors.append(str(e))

        # Launch multiple threads
        threads = []
        for i in range(20):
            should_fail = (i % 3 == 0)  # Fail every 3rd call
            thread = threading.Thread(target=concurrent_func, args=(should_fail,))
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify state is consistent
        self.assertEqual(cb.state, "CLOSED")  # Should still be closed with threshold=10
        # Verify we got reasonable results
        self.assertGreater(len(results), 0)
        self.assertGreater(len(errors), 0)

    def _helper_func(self, should_fail):
        """Helper function for thread safety test."""
        if should_fail:
            raise Exception("Intentional failure")
        return "success"

    def test_get_state_thread_safety(self):
        """Test that get_state is thread-safe."""
        cb = CircuitBreaker(threshold=5, timeout=60)
        states = []

        def get_state_repeatedly():
            for _ in range(100):
                state = cb.get_state()
                states.append(state)

        # Launch multiple threads reading state
        threads = []
        for _ in range(5):
            thread = threading.Thread(target=get_state_repeatedly)
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        # All state reads should succeed
        self.assertEqual(len(states), 500)
        # All should show CLOSED state
        for state in states:
            self.assertEqual(state['state'], "CLOSED")


class TestCircuitBreakerEdgeCases(unittest.TestCase):
    """Test suite for edge cases and boundary conditions."""

    def test_threshold_of_one(self):
        """Test circuit breaker with threshold of 1."""
        cb = CircuitBreaker(threshold=1, timeout=60)

        def failing_func():
            raise Exception("Failure")

        # First failure should open circuit
        with self.assertRaises(Exception):
            cb.call(failing_func)

        self.assertEqual(cb.state, "OPEN")

    def test_timeout_of_one_second(self):
        """Test circuit breaker with very short timeout."""
        cb = CircuitBreaker(threshold=2, timeout=1)

        # Open the circuit
        for i in range(2):
            cb.record_failure("Test")

        self.assertEqual(cb.state, "OPEN")

        # Wait for timeout
        time.sleep(1.1)

        # Should transition to HALF_OPEN
        result = cb.call(lambda: "success")
        self.assertEqual(result, "success")
        self.assertEqual(cb.state, "CLOSED")

    def test_function_returns_none(self):
        """Test that circuit breaker handles functions returning None."""
        cb = CircuitBreaker(threshold=3, timeout=60)

        def returns_none():
            return None

        result = cb.call(returns_none)
        self.assertIsNone(result)

    def test_function_with_no_name(self):
        """Test circuit breaker with lambda (no __name__ attribute)."""
        cb = CircuitBreaker(threshold=3, timeout=60)

        result = cb.call(lambda: "lambda result")
        self.assertEqual(result, "lambda result")

    def test_exception_preserves_original_type(self):
        """Test that original exception type is preserved."""
        cb = CircuitBreaker(threshold=3, timeout=60)

        class CustomException(Exception):
            pass

        def raises_custom():
            raise CustomException("Custom error")

        with self.assertRaises(CustomException):
            cb.call(raises_custom)


if __name__ == "__main__":
    unittest.main()
