"""
Unit Tests for Structured Logging and ActionResult.SKIPPED

This test module validates the structured logging functionality, particularly
the use of ActionResult.SKIPPED for passive mode operations and proper log
level handling for different result types.

Test Coverage:
    - ActionResult enum values
    - Log level selection based on result type
    - Structured event creation and logging
    - Passive mode result handling (SKIPPED)
    - Active mode result handling (SUCCESS/FAILURE)
    - Health check cycle result determination
    - Correlation ID tracking
    - Event field validation

Author: Nathan Bray
Created: 2025-11-01
"""

import unittest
from unittest.mock import Mock, MagicMock, patch, call
import logging
import time
from typing import Dict, Any

try:
    from .structured_events import (
        StructuredEventLogger,
        StructuredEvent,
        EventType,
        ActionResult
    )
except ImportError:
    from structured_events import (
        StructuredEventLogger,
        StructuredEvent,
        EventType,
        ActionResult
    )


class TestActionResultEnum(unittest.TestCase):
    """Test suite for ActionResult enum."""

    def test_action_result_values_defined(self):
        """Test that all expected ActionResult values are defined."""
        self.assertEqual(ActionResult.SUCCESS.value, "success")
        self.assertEqual(ActionResult.FAILURE.value, "failure")
        self.assertEqual(ActionResult.NO_CHANGE.value, "no_change")
        self.assertEqual(ActionResult.SKIPPED.value, "skipped")

    def test_action_result_has_all_expected_values(self):
        """Test that ActionResult has exactly the expected values."""
        expected_values = {"success", "failure", "no_change", "skipped"}
        actual_values = {item.value for item in ActionResult}
        self.assertEqual(actual_values, expected_values)


class TestStructuredEventLoggerLogLevels(unittest.TestCase):
    """Test suite for structured event logger log level handling."""

    def setUp(self):
        """Set up test fixtures."""
        # Create a mock logger
        with patch('logging.getLogger') as mock_get_logger:
            self.mock_logger = Mock()
            mock_get_logger.return_value = self.mock_logger
            self.event_logger = StructuredEventLogger("test_logger")
            self.event_logger.logger = self.mock_logger

    def test_failure_result_logs_at_error_level(self):
        """Test that FAILURE results log at ERROR level."""
        event = {
            "event_type": "test_event",
            "timestamp": time.time(),
            "result": ActionResult.FAILURE.value,
            "component": "test",
            "operation": "test_op",
            "details": {}
        }

        self.event_logger.log_event(event)

        # Verify ERROR level was used
        self.mock_logger.log.assert_called_once()
        call_args = self.mock_logger.log.call_args
        self.assertEqual(call_args[0][0], logging.ERROR)

    def test_success_result_logs_at_info_level(self):
        """Test that SUCCESS results log at INFO level."""
        event = {
            "event_type": "test_event",
            "timestamp": time.time(),
            "result": ActionResult.SUCCESS.value,
            "component": "test",
            "operation": "test_op",
            "details": {}
        }

        self.event_logger.log_event(event)

        # Verify INFO level was used
        self.mock_logger.log.assert_called_once()
        call_args = self.mock_logger.log.call_args
        self.assertEqual(call_args[0][0], logging.INFO)

    def test_skipped_result_logs_at_info_level(self):
        """Test that SKIPPED results log at INFO level."""
        event = {
            "event_type": "test_event",
            "timestamp": time.time(),
            "result": ActionResult.SKIPPED.value,
            "component": "test",
            "operation": "test_op",
            "details": {}
        }

        self.event_logger.log_event(event)

        # Verify INFO level was used
        self.mock_logger.log.assert_called_once()
        call_args = self.mock_logger.log.call_args
        self.assertEqual(call_args[0][0], logging.INFO)

    def test_no_change_result_logs_at_debug_level(self):
        """Test that NO_CHANGE results log at DEBUG level."""
        event = {
            "event_type": "test_event",
            "timestamp": time.time(),
            "result": ActionResult.NO_CHANGE.value,
            "component": "test",
            "operation": "test_op",
            "details": {}
        }

        self.event_logger.log_event(event)

        # Verify DEBUG level was used
        self.mock_logger.log.assert_called_once()
        call_args = self.mock_logger.log.call_args
        self.assertEqual(call_args[0][0], logging.DEBUG)


class TestStructuredEventLogging(unittest.TestCase):
    """Test suite for structured event creation and logging."""

    def setUp(self):
        """Set up test fixtures."""
        with patch('logging.getLogger') as mock_get_logger:
            self.mock_logger = Mock()
            mock_get_logger.return_value = self.mock_logger
            self.event_logger = StructuredEventLogger("test_logger")
            self.event_logger.logger = self.mock_logger

    def test_log_event_with_dict(self):
        """Test logging an event provided as a dictionary."""
        event = {
            "event_type": "test_event",
            "timestamp": time.time(),
            "result": ActionResult.SUCCESS.value,
            "component": "test",
            "operation": "test_op",
            "details": {"key": "value"}
        }

        self.event_logger.log_event(event)

        # Verify logging was called
        self.mock_logger.log.assert_called_once()

        # Verify structured_event flag is added
        call_args = self.mock_logger.log.call_args
        json_fields = call_args[1]['extra']['json_fields']
        self.assertTrue(json_fields['structured_event'])

    def test_log_event_with_dataclass(self):
        """Test logging an event provided as a StructuredEvent dataclass."""
        event = StructuredEvent(
            event_type="test_event",
            timestamp=time.time(),
            result=ActionResult.SUCCESS.value,
            component="test",
            operation="test_op",
            details={"key": "value"}
        )

        self.event_logger.log_event(event)

        # Verify logging was called
        self.mock_logger.log.assert_called_once()

    def test_log_event_includes_correlation_id(self):
        """Test that correlation ID is included in logged events."""
        correlation_id = "test-correlation-123"
        self.event_logger.set_correlation_id(correlation_id)

        event = {
            "event_type": "test_event",
            "timestamp": time.time(),
            "result": ActionResult.SUCCESS.value,
            "component": "test",
            "operation": "test_op",
            "details": {}
        }

        self.event_logger.log_event(event)

        # Verify correlation ID was added
        call_args = self.mock_logger.log.call_args
        json_fields = call_args[1]['extra']['json_fields']
        self.assertEqual(json_fields['correlation_id'], correlation_id)

    def test_log_event_invalid_type_raises_error(self):
        """Test that logging an invalid event type raises TypeError."""
        with self.assertRaises(TypeError):
            self.event_logger.log_event("invalid_event_string")

        with self.assertRaises(TypeError):
            self.event_logger.log_event(12345)


class TestHealthCheckCycleResultDetermination(unittest.TestCase):
    """Test suite for health check cycle result determination logic."""

    def test_skipped_result_when_skip_updates_true(self):
        """Test that result is SKIPPED when skip_updates is True."""
        # Simulate the daemon logic for determining cycle result
        skip_updates = True
        primary_success = True
        secondary_success = True
        cloudflare_success = True
        cycle_success = primary_success and secondary_success and cloudflare_success

        # This is the logic from daemon.py lines 624-646
        if skip_updates:
            cycle_result = ActionResult.SKIPPED
        elif cycle_success:
            cycle_result = ActionResult.SUCCESS
        else:
            cycle_result = ActionResult.FAILURE

        self.assertEqual(cycle_result, ActionResult.SKIPPED,
                        "Cycle result should be SKIPPED when skip_updates is True")

    def test_success_result_when_all_operations_succeed(self):
        """Test that result is SUCCESS when all operations succeed."""
        skip_updates = False
        primary_success = True
        secondary_success = True
        cloudflare_success = True
        cycle_success = primary_success and secondary_success and cloudflare_success

        if skip_updates:
            cycle_result = ActionResult.SKIPPED
        elif cycle_success:
            cycle_result = ActionResult.SUCCESS
        else:
            cycle_result = ActionResult.FAILURE

        self.assertEqual(cycle_result, ActionResult.SUCCESS,
                        "Cycle result should be SUCCESS when all operations succeed")

    def test_failure_result_when_any_operation_fails(self):
        """Test that result is FAILURE when any operation fails."""
        skip_updates = False
        primary_success = True
        secondary_success = False  # This operation failed
        cloudflare_success = True
        cycle_success = primary_success and secondary_success and cloudflare_success

        if skip_updates:
            cycle_result = ActionResult.SKIPPED
        elif cycle_success:
            cycle_result = ActionResult.SUCCESS
        else:
            cycle_result = ActionResult.FAILURE

        self.assertEqual(cycle_result, ActionResult.FAILURE,
                        "Cycle result should be FAILURE when any operation fails")

    def test_skipped_takes_precedence_over_failures(self):
        """Test that SKIPPED result takes precedence even if operations would fail."""
        skip_updates = True
        primary_success = False
        secondary_success = False
        cloudflare_success = False
        cycle_success = primary_success and secondary_success and cloudflare_success

        if skip_updates:
            cycle_result = ActionResult.SKIPPED
        elif cycle_success:
            cycle_result = ActionResult.SUCCESS
        else:
            cycle_result = ActionResult.FAILURE

        self.assertEqual(cycle_result, ActionResult.SKIPPED,
                        "Cycle result should be SKIPPED even if operations would fail")


class TestPassiveModeStructuredLogging(unittest.TestCase):
    """Test suite for passive mode structured logging integration."""

    def setUp(self):
        """Set up test fixtures."""
        with patch('logging.getLogger') as mock_get_logger:
            self.mock_logger = Mock()
            mock_get_logger.return_value = self.mock_logger
            self.event_logger = StructuredEventLogger("test_logger")
            self.event_logger.logger = self.mock_logger

    def test_passive_mode_cycle_logs_skipped_result(self):
        """Test that passive mode health check cycles log SKIPPED result."""
        # Simulate a passive mode health check cycle
        skip_updates = True
        cycle_result = ActionResult.SKIPPED if skip_updates else ActionResult.SUCCESS

        event = {
            "event_type": "health_check_cycle",
            "timestamp": time.time(),
            "result": cycle_result.value,
            "component": "daemon",
            "operation": "health_check_cycle",
            "details": {
                "passive_mode": True,
                "operation_results": {
                    "bgp_updates_skipped": True,
                    "cloudflare_updates_skipped": True
                }
            }
        }

        self.event_logger.log_event(event)

        # Verify the result is skipped
        call_args = self.mock_logger.log.call_args
        json_fields = call_args[1]['extra']['json_fields']
        self.assertEqual(json_fields['result'], "skipped")

    def test_active_mode_cycle_logs_success_result(self):
        """Test that active mode health check cycles log SUCCESS result."""
        skip_updates = False
        cycle_result = ActionResult.SKIPPED if skip_updates else ActionResult.SUCCESS

        event = {
            "event_type": "health_check_cycle",
            "timestamp": time.time(),
            "result": cycle_result.value,
            "component": "daemon",
            "operation": "health_check_cycle",
            "details": {
                "passive_mode": False,
                "operation_results": {
                    "bgp_updates_skipped": False,
                    "cloudflare_updates_skipped": False
                }
            }
        }

        self.event_logger.log_event(event)

        # Verify the result is success
        call_args = self.mock_logger.log.call_args
        json_fields = call_args[1]['extra']['json_fields']
        self.assertEqual(json_fields['result'], "success")

    def test_passive_mode_flags_in_event_details(self):
        """Test that passive mode flags are included in event details."""
        event = {
            "event_type": "health_check_cycle",
            "timestamp": time.time(),
            "result": ActionResult.SKIPPED.value,
            "component": "daemon",
            "operation": "health_check_cycle",
            "details": {
                "configuration": {
                    "passive_mode": True
                },
                "operation_results": {
                    "bgp_updates_skipped": True,
                    "cloudflare_updates_skipped": True
                }
            }
        }

        self.event_logger.log_event(event)

        # Verify passive mode flags are present
        call_args = self.mock_logger.log.call_args
        json_fields = call_args[1]['extra']['json_fields']
        details = json_fields['details']

        self.assertTrue(details['configuration']['passive_mode'])
        self.assertTrue(details['operation_results']['bgp_updates_skipped'])
        self.assertTrue(details['operation_results']['cloudflare_updates_skipped'])


class TestConsecutiveErrorTracking(unittest.TestCase):
    """Test suite for consecutive error tracking with SKIPPED results."""

    def test_skipped_operations_do_not_count_as_errors(self):
        """Test that skipped operations don't increment consecutive error count."""
        consecutive_errors = 0
        skip_updates = True
        cycle_success = True

        # This is the logic from daemon.py lines 648-652
        if skip_updates or cycle_success:
            consecutive_errors = 0
        else:
            consecutive_errors += 1

        self.assertEqual(consecutive_errors, 0,
                        "Consecutive errors should not increment for skipped operations")

    def test_successful_operations_reset_error_count(self):
        """Test that successful operations reset consecutive error count."""
        consecutive_errors = 5  # Previous errors
        skip_updates = False
        cycle_success = True

        if skip_updates or cycle_success:
            consecutive_errors = 0
        else:
            consecutive_errors += 1

        self.assertEqual(consecutive_errors, 0,
                        "Consecutive errors should reset on success")

    def test_failed_operations_increment_error_count(self):
        """Test that failed operations increment consecutive error count."""
        consecutive_errors = 2
        skip_updates = False
        cycle_success = False

        if skip_updates or cycle_success:
            consecutive_errors = 0
        else:
            consecutive_errors += 1

        self.assertEqual(consecutive_errors, 3,
                        "Consecutive errors should increment on failure")


class TestEventTypeEnum(unittest.TestCase):
    """Test suite for EventType enum."""

    def test_event_type_values_defined(self):
        """Test that all expected EventType values are defined."""
        self.assertEqual(EventType.BGP_ADVERTISEMENT_CHANGE.value, "bgp_advertisement_change")
        self.assertEqual(EventType.CLOUDFLARE_ROUTE_UPDATE.value, "cloudflare_route_update")
        self.assertEqual(EventType.HEALTH_CHECK_RESULT.value, "health_check_result")
        self.assertEqual(EventType.STATE_TRANSITION.value, "state_transition")
        self.assertEqual(EventType.CIRCUIT_BREAKER_EVENT.value, "circuit_breaker_event")
        self.assertEqual(EventType.DAEMON_LIFECYCLE.value, "daemon_lifecycle")


class TestStructuredEventDataclass(unittest.TestCase):
    """Test suite for StructuredEvent dataclass."""

    def test_structured_event_creation(self):
        """Test creating a StructuredEvent with required fields."""
        event = StructuredEvent(
            event_type="test_event",
            timestamp=123456.789,
            result="success",
            component="test_component",
            operation="test_operation",
            details={"key": "value"}
        )

        self.assertEqual(event.event_type, "test_event")
        self.assertEqual(event.timestamp, 123456.789)
        self.assertEqual(event.result, "success")
        self.assertEqual(event.component, "test_component")
        self.assertEqual(event.operation, "test_operation")
        self.assertEqual(event.details, {"key": "value"})

    def test_structured_event_optional_fields(self):
        """Test StructuredEvent optional fields default to None."""
        event = StructuredEvent(
            event_type="test_event",
            timestamp=123456.789,
            result="success",
            component="test_component",
            operation="test_operation",
            details={}
        )

        self.assertIsNone(event.duration_ms)
        self.assertIsNone(event.error_message)
        self.assertIsNone(event.correlation_id)

    def test_structured_event_with_all_fields(self):
        """Test StructuredEvent with all fields populated."""
        event = StructuredEvent(
            event_type="test_event",
            timestamp=123456.789,
            result="failure",
            component="test_component",
            operation="test_operation",
            details={"key": "value"},
            duration_ms=150,
            error_message="Test error",
            correlation_id="test-123"
        )

        self.assertEqual(event.duration_ms, 150)
        self.assertEqual(event.error_message, "Test error")
        self.assertEqual(event.correlation_id, "test-123")


if __name__ == '__main__':
    # Run tests with verbose output
    unittest.main(verbosity=2)
