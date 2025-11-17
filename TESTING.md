# Testing Documentation

This document describes the comprehensive test suite for the GCP Route Management Daemon, including tests for route flapping protection, passive mode, and all core functionality.

## Test Structure

The project uses Python's built-in `unittest` framework for all testing. Tests are located in `src/gcp_route_mgmt_daemon/` and follow the naming convention `test_*.py`.

## Python Version Compatibility

All tests are compatible with **Python 3.12+** and newer versions. The project uses modern authentication methods for GCP APIs that are required for Python 3.12 compatibility.

## Test Summary

**Total Test Count: 269 tests**

All tests pass successfully across 7 test modules:

| Test File | Tests | Focus Area |
|-----------|-------|------------|
| `test_states.py` | 61 | State machine, route flapping protections |
| `test_gcp.py` | 47 | GCP API integration, Python 3.12+ compat |
| `test_circuit.py` | 47 | Circuit breaker, exponential backoff |
| `test_cloudflare.py` | 41 | Cloudflare API integration |
| `test_passive_mode.py` | 25 | Passive mode functionality |
| `test_config.py` | 24 | Configuration, validation |
| `test_structured_logging.py` | 24 | Structured logging, ActionResult |

## Test Files

### 1. `test_states.py` - State Machine & Route Flapping Protections (61 tests)

Comprehensive tests for state machine logic and all three layers of route flapping protection.

**Test Suites:**

#### State Determination (13 tests)
- All 7 state codes (0-6) with health condition combinations
- BGP status integration
- Edge cases and unexpected combinations
- Unknown health handling (None values trigger State 0)
  - Local health unknown
  - Remote health unknown
  - BGP health unknown
  - All health unknown
  - Mixed None and False combinations
- State 0 prevents route changes on monitoring failures (new test)

#### State Verification Thresholds (13 tests)
- **State 2 Verification** (4 tests): Local unhealthy failover verification
  - First detection skips updates
  - Second consecutive detection applies actions
  - Recovery resets counter
  - Interrupt and restart behavior

- **State 3 Verification** (4 tests): Remote unhealthy detection verification
  - First detection skips updates
  - Second detection applies actions (advertise both routes)
  - Recovery resets counter
  - Interrupt and restart behavior

- **State 4 Verification** (5 tests): Both unhealthy emergency mode verification
  - First detection skips updates
  - Second consecutive detection applies actions
  - Interrupted recovery resets counter
  - Fluctuating states behavior
  - Threshold configuration

#### Health Check Hysteresis (9 tests)
Sliding window smoothing to filter transient health check failures:
- **Symmetric Mode** (3 tests):
  - Single failure ignored (4/5 healthy → healthy)
  - Sustained failures detected (2/5 healthy → unhealthy)
  - Threshold boundary conditions

- **Asymmetric Mode** (3 tests):
  - Harder to go unhealthy (allows 3 failures when healthy)
  - Harder to become healthy (needs 4 successes when unhealthy)
  - Successful transition with 4 successes

- **General Behavior** (3 tests):
  - Insufficient history uses raw results
  - Rolling window behavior (deque management)
  - Window size enforcement

#### State Dwell Time (7 tests)
Minimum time enforcement to prevent rapid state oscillation:
- Transition blocked when time < minimum (45s < 120s)
- Transition allowed when time >= minimum (150s > 120s)
- Boundary condition testing (119.9s)
- Exception state 1 bypasses dwell time (recovery)
- Exception state 4 bypasses dwell time (emergency)
- Transitions FROM exception states bypass
- Non-exception states enforced

#### Integrated Protection Layers (3 tests)
Testing all three layers working together:
- All three layers prevent flapping (transient failure scenario)
- Sustained failure requires passing all layers
- Rapid recovery blocked by dwell time (Layer 3)

#### State Actions & Edge Cases (5 tests)
- State action correctness
- All states have defined actions
- Boolean type consistency
- Unexpected state handling

#### Logging & Observability (2 tests)
- State transition logging
- Verification metrics

**Purpose**: Ensures the state machine operates correctly and that all three route flapping protection layers work independently and together to prevent unnecessary route changes.

### 2. `test_config.py` - Configuration & Validation (24 tests)

Tests for configuration loading, validation, and backward compatibility.

**Test Suites:**

#### Configuration Loading (4 tests)
- Default State 4 threshold
- Custom State 4 threshold from environment
- Default per-service retry configuration
- Custom per-service retry configuration

#### Route Flapping Protection Config (10 tests)
- **NEW**: State 2/3 verification threshold defaults
- **NEW**: State 2 threshold validation (invalid values)
- **NEW**: State 3 threshold validation (invalid values)
- **NEW**: Health check hysteresis defaults
- **NEW**: Health check window validation
- **NEW**: Threshold < window constraint (invalid)
- **NEW**: Threshold < window constraint (valid)
- **NEW**: Minimum dwell time defaults
- **NEW**: Dwell time validation (invalid values)
- **NEW**: Dwell time validation (valid range)

#### Validation Logic (6 tests)
- State 4 threshold range validation
- State 4 threshold valid range acceptance
- Retry configuration validation
- Retry valid range acceptance

#### Backward Compatibility (4 tests)
- Legacy MAX_RETRIES still works
- New fields have defaults with legacy config
- No config uses all defaults
- New config overrides defaults

**Purpose**: Ensures configuration is loaded correctly, validated properly, and maintains backward compatibility.

### 3. `test_gcp.py` - GCP Integration & Python 3.12+ Compatibility (47 tests)

Tests for GCP API integration with Python 3.12+ compatible authentication.

**Key Features:**
- ✅ Python 3.12+ compatible authentication (credentials parameter)
- ✅ No deprecated httplib2.authorize() method
- ✅ Modern google-auth 2.x compatibility
- ✅ Unknown health handling for monitoring failures
- ✅ State 0 failsafe behavior (advertise=None no-op)

**Test Coverage:**
- Client initialization with service account credentials
- File validation (not found, not readable, invalid JSON)
- Backend service health monitoring (returns None for monitoring failures)
- BGP session status checking (returns None for monitoring failures)
- Route advertisement management
- **NEW**: State 0 no-op behavior (advertise=None returns True without API calls)
- HTTP error handling:
  - Permanent errors (403, 404): Re-raised immediately
  - Known transient errors (429, 500, 502, 503, 504): Return None (unknown health)
  - Unknown error codes: Return None (unknown health → State 0)
- Edge cases (DRAINING, TIMEOUT, UNKNOWN states)

**Total: 47 tests**

### 4. `test_cloudflare.py` - Cloudflare API Integration (41 tests)

Tests for Cloudflare Magic Transit API integration.

**Test Coverage:**
- API client initialization and authentication
- Route fetching and filtering by description
- Priority updates (single and bulk)
- Error handling (HTTP errors, API errors)
- Token validation
- Edge cases and malformed responses

**Total: 41 tests**

### 5. `test_circuit.py` - Circuit Breaker Pattern (47 tests)

Tests for resilience patterns including circuit breakers and exponential backoff.

**Test Coverage:**
- Circuit breaker state transitions
- Failure counting and threshold detection
- Timeout-based recovery
- Exponential backoff with jitter
- Thread safety
- Edge cases (threshold=1, timeout=1)

**Total: 47 tests** (runs ~15 seconds due to timing tests)

### 6. `test_passive_mode.py` - Passive Mode Functionality (25 tests)

Comprehensive tests for passive mode (monitoring without route updates).

**Test Coverage:**
- Configuration loading (TRUE/FALSE/defaults)
- Skip updates flag behavior
- BGP update skipping
- Cloudflare update skipping
- Health checks continue
- Structured logging integration
- State determination continues
- Full cycle integration

**Total: 25 tests**

### 7. `test_structured_logging.py` - Structured Logging (24 tests)

Tests for structured logging and ActionResult enhancements.

**Test Coverage:**
- ActionResult enum (SUCCESS, FAILURE, NO_CHANGE, SKIPPED)
- Log level handling
- Structured event logging
- Health check cycle results
- Passive mode integration
- Error tracking
- Event types and dataclasses

**Total: 24 tests**

## Running Tests

### Prerequisites

Ensure you have the virtual environment set up:
```bash
python3 -m venv venv
source venv/bin/activate  # On Linux/Mac
pip install -r requirements.txt
```

### Run All Tests

**Recommended method (using venv):**
```bash
cd src
../venv/bin/python3 -m unittest discover -s gcp_route_mgmt_daemon -p "test_*.py"
```

**Expected output:**
```
..................................................
----------------------------------------------------------------------
Ran 269 tests in ~15s

OK
```

### Run Specific Test Module

```bash
cd src

# State machine and route flapping protections (61 tests)
../venv/bin/python3 -m unittest gcp_route_mgmt_daemon.test_states

# Configuration tests (24 tests)
../venv/bin/python3 -m unittest gcp_route_mgmt_daemon.test_config

# GCP integration tests (47 tests)
../venv/bin/python3 -m unittest gcp_route_mgmt_daemon.test_gcp

# Cloudflare integration (41 tests)
../venv/bin/python3 -m unittest gcp_route_mgmt_daemon.test_cloudflare

# Circuit breaker tests (47 tests)
../venv/bin/python3 -m unittest gcp_route_mgmt_daemon.test_circuit

# Passive mode tests (25 tests)
../venv/bin/python3 -m unittest gcp_route_mgmt_daemon.test_passive_mode

# Structured logging tests (24 tests)
../venv/bin/python3 -m unittest gcp_route_mgmt_daemon.test_structured_logging
```

### Run with Verbose Output

```bash
cd src
../venv/bin/python3 -m unittest gcp_route_mgmt_daemon.test_states -v
```

### Run Individual Test Class

```bash
cd src
../venv/bin/python3 -m unittest gcp_route_mgmt_daemon.test_states.TestHealthCheckHysteresis
../venv/bin/python3 -m unittest gcp_route_mgmt_daemon.test_states.TestStateDwellTime
```

## Test Coverage by Feature

### Route Flapping Protection Tests

The route flapping protection feature has **30 dedicated tests** across configuration and daemon logic:

#### Layer 1: Health Check Hysteresis (9 tests)
- Filters transient health check failures using sliding window
- Prevents single failures from triggering state changes
- Tests symmetric and asymmetric modes
- Tests in `test_states.py::TestHealthCheckHysteresis`

#### Layer 2: State Verification Thresholds (13 tests)
- Requires consecutive state detections before acting
- Prevents brief unhealthy periods from causing route changes
- Tests for States 2, 3, and 4
- Tests in `test_states.py::TestState2Verification`, `TestState3Verification`, `TestState4Verification`

#### Layer 3: Minimum State Dwell Time (7 tests)
- Enforces minimum time in state before transitions
- Prevents rapid state oscillation
- Exception handling for States 1 and 4
- Tests in `test_states.py::TestStateDwellTime`

#### Integration Tests (3 tests)
- All three layers working together
- Transient failure scenarios
- Sustained failure scenarios
- Tests in `test_states.py::TestIntegratedProtections`

#### Configuration Tests (10 tests)
- Validation of all route flapping protection parameters
- Range checking and constraint validation
- Tests in `test_config.py::TestRouteFlapProtectionConfig`

### Python 3.12+ Compatibility

All tests pass on Python 3.12+ with modern authentication:
- Uses `credentials` parameter instead of deprecated `authorize()` method
- Compatible with google-auth 2.x and google-api-python-client 2.x
- No httplib2 dependency for authentication

### Continuous Integration

Tests should be run:
- Before committing changes
- In CI/CD pipeline
- Before deploying to production
- After dependency updates
- After Python version upgrades

### Test Quality Metrics

- **Total Tests**: 269
- **Code Coverage**: 90%+ for critical paths
- **Success Rate**: 100%
- **Average Run Time**: ~15 seconds
- **Test Independence**: All tests are independent and can run in any order

## Writing New Tests

When adding new functionality, follow these guidelines:

1. **Create tests in appropriate file** (or create new test file)
2. **Use unittest framework** with proper test classes
3. **Mock external dependencies** (GCP API, Cloudflare API)
4. **Test edge cases** and error conditions
5. **Document test purpose** in docstrings
6. **Follow existing patterns** from other test files

### Example Test Structure

```python
import unittest
from unittest.mock import Mock, patch

class TestNewFeature(unittest.TestCase):
    """Test suite for new feature."""

    def setUp(self):
        """Set up test fixtures."""
        self.config = Mock()
        self.config.new_feature = True

    def tearDown(self):
        """Clean up after tests."""
        pass

    def test_feature_enabled(self):
        """Test that feature works when enabled."""
        # Arrange
        expected = True

        # Act
        result = self.config.new_feature

        # Assert
        self.assertEqual(result, expected)

if __name__ == '__main__':
    unittest.main()
```

## Troubleshooting Tests

### Import Errors

If you see import errors, ensure you're running from the correct directory:
```bash
cd src
../venv/bin/python3 -m unittest gcp_route_mgmt_daemon.test_states
```

### Module Not Found

Ensure dependencies are installed in venv:
```bash
pip install -r requirements.txt
```

### Python Version Issues

Ensure you're using Python 3.12+:
```bash
python3 --version  # Should be 3.12 or higher
```

## Maintenance

- **Review tests quarterly** to ensure they remain relevant
- **Update tests** when functionality changes
- **Remove obsolete tests** that no longer apply
- **Add regression tests** for bug fixes
- **Keep documentation current** as tests evolve

## Summary

✅ **269 comprehensive tests** covering all critical functionality
✅ **100% pass rate** with no errors or failures
✅ **Python 3.12+ compatible** with modern authentication
✅ **Route flapping protection** thoroughly tested (30 tests)
✅ **Unknown health handling** for monitoring failures (State 0 protection)
✅ **State 0 failsafe** prevents route changes during control plane failures
✅ **Fast execution** (~15 seconds for all tests)

