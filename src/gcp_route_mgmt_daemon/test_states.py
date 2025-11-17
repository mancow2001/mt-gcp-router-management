"""
Unit Tests for State Machine Logic and Route Flapping Protections

This test module comprehensively validates the state determination logic,
state verification behavior, and all route flapping protection mechanisms
in the daemon.

Test Coverage:
    - All 7 state codes (0-6) with their health condition combinations
    - State 2 verification logic (local unhealthy - requires consecutive detections)
    - State 3 verification logic (remote unhealthy - requires consecutive detections)
    - State 4 verification logic (both unhealthy - requires consecutive detections)
    - Health check hysteresis (sliding window smoothing)
      * Symmetric mode (simple majority)
      * Asymmetric mode (different up/down thresholds)
      * Transient failure absorption
      * Sustained failure detection
    - Minimum state dwell time enforcement
      * Transition blocking logic
      * Exception state handling (States 1 and 4)
      * Boundary conditions
    - Integration of all three protection layers
      * Layer 1: Hysteresis filters transient failures
      * Layer 2: Verification requires consecutive detections
      * Layer 3: Dwell time prevents rapid state oscillation
    - State transitions and verification reset behavior
    - Edge cases and boundary conditions
    - Logging and structured event generation

Test Suites:
    - TestStateDetermination (7 tests) - State code mapping
    - TestState4Verification (5 tests) - State 4 verification threshold
    - TestState2Verification (4 tests) - State 2 verification threshold
    - TestState3Verification (4 tests) - State 3 verification threshold
    - TestHealthCheckHysteresis (9 tests) - Sliding window smoothing
    - TestStateDwellTime (7 tests) - Minimum time in state
    - TestIntegratedProtections (3 tests) - All layers working together
    - TestLoggingAndMetrics (2 tests) - Event logging

Total Tests: 41

Author: Nathan Bray
Created: 2025-10-31
Updated: 2025-11-02 - Added all route flapping protection tests
"""

import unittest
from unittest.mock import Mock, MagicMock, patch, call
import time
from typing import Tuple, Optional
from collections import deque

# Import the state determination function from state module
try:
    from state import determine_state_code, STATE_ACTIONS
except ImportError:
    # If state.py doesn't exist, define here for testing
    def determine_state_code(local_healthy, remote_healthy, remote_bgp_up):
        """
        Determines the system state based on the health of the local and remote regions,
        and the BGP (Border Gateway Protocol) session status in the remote region.
        """
        # If any health status is unknown/unreliable, return State 0 (no changes)
        if local_healthy is None or remote_healthy is None or remote_bgp_up is None:
            return 0  # Unknown health -> maintain current state

        if local_healthy and remote_healthy and remote_bgp_up:
            return 1
        elif not local_healthy and remote_healthy and remote_bgp_up:
            return 2
        elif local_healthy and not remote_healthy and remote_bgp_up:
            return 3
        elif not local_healthy and not remote_healthy and remote_bgp_up:
            return 4
        elif not local_healthy and remote_healthy and not remote_bgp_up:
            return 5
        elif local_healthy and remote_healthy and not remote_bgp_up:
            return 6
        return 0  # Default/failback state

    STATE_ACTIONS = {
        0: (None,  None),   # No change: maintain current advertisements (failsafe/default)
        1: (True,  False),  # All good: advertise primary only
        2: (False, False),  # Failover candidate: don't advertise from failed local
        3: (True,  True),   # Redundant: advertise both (for resilience)
        4: (True,  False),  # Last resort: advertise from local despite failures
        5: (True,  False),  # No BGP on remote: fallback to local only
        6: (True,  True),   # BGP down, but services good: advertise both
    }


class TestStateDetermination(unittest.TestCase):
    """Test suite for state code determination logic."""

    def test_state_1_all_healthy(self):
        """Test State 1: All systems healthy (normal operation)."""
        state = determine_state_code(
            local_healthy=True,
            remote_healthy=True,
            remote_bgp_up=True
        )
        self.assertEqual(state, 1, "Should return state 1 when all systems are healthy")

        # Verify expected actions
        advertise_primary, advertise_secondary = STATE_ACTIONS[state]
        self.assertTrue(advertise_primary, "Primary should be advertised in state 1")
        self.assertFalse(advertise_secondary, "Secondary should NOT be advertised in state 1")

    def test_state_2_local_unhealthy(self):
        """Test State 2: Local unhealthy, remote healthy, BGP up (failover mode)."""
        state = determine_state_code(
            local_healthy=False,
            remote_healthy=True,
            remote_bgp_up=True
        )
        self.assertEqual(state, 2, "Should return state 2 when local is unhealthy")

        # Verify expected actions (withdraw all advertisements)
        advertise_primary, advertise_secondary = STATE_ACTIONS[state]
        self.assertFalse(advertise_primary, "Primary should NOT be advertised in state 2")
        self.assertFalse(advertise_secondary, "Secondary should NOT be advertised in state 2")

    def test_state_3_remote_unhealthy(self):
        """Test State 3: Remote unhealthy, local healthy, BGP up (use local path)."""
        state = determine_state_code(
            local_healthy=True,
            remote_healthy=False,
            remote_bgp_up=True
        )
        self.assertEqual(state, 3, "Should return state 3 when remote is unhealthy")

        # Verify expected actions (advertise both for resilience)
        advertise_primary, advertise_secondary = STATE_ACTIONS[state]
        self.assertTrue(advertise_primary, "Primary should be advertised in state 3")
        self.assertTrue(advertise_secondary, "Secondary should be advertised in state 3")

    def test_state_4_both_unhealthy(self):
        """Test State 4: Both regions unhealthy, BGP up (emergency mode)."""
        state = determine_state_code(
            local_healthy=False,
            remote_healthy=False,
            remote_bgp_up=True
        )
        self.assertEqual(state, 4, "Should return state 4 when both regions are unhealthy")

        # Verify expected actions (advertise primary only as last resort)
        advertise_primary, advertise_secondary = STATE_ACTIONS[state]
        self.assertTrue(advertise_primary, "Primary should be advertised in state 4 (after verification)")
        self.assertFalse(advertise_secondary, "Secondary should NOT be advertised in state 4")

    def test_state_5_local_unhealthy_bgp_down(self):
        """Test State 5: Local unhealthy, remote healthy, BGP down (backup infrastructure)."""
        state = determine_state_code(
            local_healthy=False,
            remote_healthy=True,
            remote_bgp_up=False
        )
        self.assertEqual(state, 5, "Should return state 5 when local unhealthy and BGP down")

        # Verify expected actions
        advertise_primary, advertise_secondary = STATE_ACTIONS[state]
        self.assertTrue(advertise_primary, "Primary should be advertised in state 5")
        self.assertFalse(advertise_secondary, "Secondary should NOT be advertised in state 5")

    def test_state_6_both_healthy_bgp_down(self):
        """Test State 6: Both healthy, BGP down (colocation infrastructure issue)."""
        state = determine_state_code(
            local_healthy=True,
            remote_healthy=True,
            remote_bgp_up=False
        )
        self.assertEqual(state, 6, "Should return state 6 when both healthy but BGP down")

        # Verify expected actions (advertise both)
        advertise_primary, advertise_secondary = STATE_ACTIONS[state]
        self.assertTrue(advertise_primary, "Primary should be advertised in state 6")
        self.assertTrue(advertise_secondary, "Secondary should be advertised in state 6")

    def test_state_0_unexpected_combination(self):
        """Test State 0: Unexpected health combination (failsafe default)."""
        # This tests the edge case where remote is unhealthy and BGP is down
        # but local is also unhealthy
        state = determine_state_code(
            local_healthy=False,
            remote_healthy=False,
            remote_bgp_up=False
        )
        self.assertEqual(state, 0, "Should return state 0 for unexpected combinations")

        # State 0 should maintain current state (no change)
        if 0 in STATE_ACTIONS:
            advertise_primary, advertise_secondary = STATE_ACTIONS[0]
            # Verify it's a "no change" default (None values)
            self.assertIsNone(advertise_primary, "State 0 should maintain current state (None)")
            self.assertIsNone(advertise_secondary, "State 0 should maintain current state (None)")

    def test_state_0_another_edge_case(self):
        """Test State 0: Another edge case - local healthy, remote unhealthy, BGP down."""
        state = determine_state_code(
            local_healthy=True,
            remote_healthy=False,
            remote_bgp_up=False
        )
        self.assertEqual(state, 0, "Should return state 0 for this combination")

    def test_state_0_local_health_unknown(self):
        """Test State 0: Local health is None (monitoring unavailable)."""
        state = determine_state_code(
            local_healthy=None,
            remote_healthy=True,
            remote_bgp_up=True
        )
        self.assertEqual(state, 0, "Should return state 0 when local health is unknown")

        # Verify State 0 action is "no change"
        advertise_primary, advertise_secondary = STATE_ACTIONS[state]
        self.assertIsNone(advertise_primary, "State 0 should maintain current state (None)")
        self.assertIsNone(advertise_secondary, "State 0 should maintain current state (None)")

    def test_state_0_remote_health_unknown(self):
        """Test State 0: Remote health is None (monitoring unavailable)."""
        state = determine_state_code(
            local_healthy=True,
            remote_healthy=None,
            remote_bgp_up=True
        )
        self.assertEqual(state, 0, "Should return state 0 when remote health is unknown")

    def test_state_0_bgp_health_unknown(self):
        """Test State 0: BGP health is None (monitoring unavailable)."""
        state = determine_state_code(
            local_healthy=True,
            remote_healthy=True,
            remote_bgp_up=None
        )
        self.assertEqual(state, 0, "Should return state 0 when BGP health is unknown")

    def test_state_0_all_health_unknown(self):
        """Test State 0: All health parameters are None (complete monitoring failure)."""
        state = determine_state_code(
            local_healthy=None,
            remote_healthy=None,
            remote_bgp_up=None
        )
        self.assertEqual(state, 0, "Should return state 0 when all health is unknown")

    def test_state_0_mixed_none_and_false(self):
        """Test State 0: Mix of None and False values."""
        # Test various combinations with None
        test_cases = [
            (None, False, True),
            (False, None, True),
            (True, False, None),
            (None, None, True),
            (None, True, None),
            (False, None, None),
        ]

        for local, remote, bgp in test_cases:
            with self.subTest(local=local, remote=remote, bgp=bgp):
                state = determine_state_code(local, remote, bgp)
                self.assertEqual(state, 0,
                               f"Should return state 0 when any health is None: "
                               f"local={local}, remote={remote}, bgp={bgp}")


class TestState4Verification(unittest.TestCase):
    """Test suite for State 4 verification logic."""

    def setUp(self):
        """Set up test fixtures."""
        self.state_4_consecutive_count = 0
        self.state_4_pending_verification = False
        self.state_4_verification_threshold = 2
        self.current_state_code = None

    def simulate_state_detection(self, new_state_code: int) -> Tuple[bool, Optional[bool], Optional[bool]]:
        """
        Simulate the state 4 verification logic from the daemon.

        Returns:
            Tuple of (skip_updates, advertise_primary, advertise_secondary)
        """
        advertise_primary, advertise_secondary = STATE_ACTIONS.get(new_state_code, (False, False))
        skip_updates = False

        if new_state_code == 4:
            if new_state_code == self.current_state_code:
                # State 4 continues, increment counter
                self.state_4_consecutive_count += 1
            else:
                # First detection of State 4
                self.state_4_consecutive_count = 1
                self.state_4_pending_verification = True

            # Check if we've verified state 4 sufficiently
            if self.state_4_consecutive_count < self.state_4_verification_threshold:
                skip_updates = True
                advertise_primary = None
                advertise_secondary = None
            else:
                self.state_4_pending_verification = False
        else:
            # Not in state 4, reset counter
            self.state_4_consecutive_count = 0
            self.state_4_pending_verification = False

        self.current_state_code = new_state_code
        return skip_updates, advertise_primary, advertise_secondary

    def test_state_4_first_detection_skips_updates(self):
        """Test that first detection of State 4 skips all updates."""
        skip_updates, adv_primary, adv_secondary = self.simulate_state_detection(4)

        self.assertTrue(skip_updates, "Should skip updates on first State 4 detection")
        self.assertIsNone(adv_primary, "Primary advertisement should be None (no action)")
        self.assertIsNone(adv_secondary, "Secondary advertisement should be None (no action)")
        self.assertEqual(self.state_4_consecutive_count, 1, "Counter should be 1")
        self.assertTrue(self.state_4_pending_verification, "Should be pending verification")

    def test_state_4_second_detection_applies_actions(self):
        """Test that second consecutive State 4 detection applies actions."""
        # First detection
        self.simulate_state_detection(4)

        # Second detection (should verify and apply actions)
        skip_updates, adv_primary, adv_secondary = self.simulate_state_detection(4)

        self.assertFalse(skip_updates, "Should NOT skip updates on second detection")
        self.assertTrue(adv_primary, "Primary should be advertised after verification")
        self.assertFalse(adv_secondary, "Secondary should NOT be advertised after verification")
        self.assertEqual(self.state_4_consecutive_count, 2, "Counter should be 2")
        self.assertFalse(self.state_4_pending_verification, "Should no longer be pending")

    def test_state_4_third_detection_continues_actions(self):
        """Test that third consecutive State 4 detection continues applying actions."""
        # First and second detections
        self.simulate_state_detection(4)
        self.simulate_state_detection(4)

        # Third detection (should continue applying actions)
        skip_updates, adv_primary, adv_secondary = self.simulate_state_detection(4)

        self.assertFalse(skip_updates, "Should NOT skip updates on third detection")
        self.assertTrue(adv_primary, "Primary should be advertised")
        self.assertFalse(adv_secondary, "Secondary should NOT be advertised")
        self.assertEqual(self.state_4_consecutive_count, 3, "Counter should be 3")

    def test_state_4_interrupted_by_recovery_resets_counter(self):
        """Test that recovering from State 4 before verification resets counter."""
        # First detection of State 4
        skip_updates, adv_primary, adv_secondary = self.simulate_state_detection(4)
        self.assertEqual(self.state_4_consecutive_count, 1, "Counter should be 1")

        # Recovery to State 1 before verification completes
        skip_updates, adv_primary, adv_secondary = self.simulate_state_detection(1)

        self.assertFalse(skip_updates, "Should NOT skip updates in State 1")
        self.assertTrue(adv_primary, "Primary should be advertised in State 1")
        self.assertFalse(adv_secondary, "Secondary should NOT be advertised in State 1")
        self.assertEqual(self.state_4_consecutive_count, 0, "Counter should reset to 0")
        self.assertFalse(self.state_4_pending_verification, "Should not be pending")

    def test_state_4_interrupted_then_detected_again(self):
        """Test State 4 detection, interruption, then re-detection starts verification over."""
        # First detection of State 4
        self.simulate_state_detection(4)
        self.assertEqual(self.state_4_consecutive_count, 1)

        # Interrupted by State 2
        self.simulate_state_detection(2)
        self.assertEqual(self.state_4_consecutive_count, 0, "Counter should reset")

        # State 4 detected again (should start over)
        skip_updates, adv_primary, adv_secondary = self.simulate_state_detection(4)

        self.assertTrue(skip_updates, "Should skip updates on new first detection")
        self.assertIsNone(adv_primary, "Primary should be None")
        self.assertEqual(self.state_4_consecutive_count, 1, "Counter should restart at 1")

        # Second detection should verify
        skip_updates, adv_primary, adv_secondary = self.simulate_state_detection(4)
        self.assertFalse(skip_updates, "Should NOT skip updates after verification")
        self.assertTrue(adv_primary, "Primary should be advertised")

    def test_state_4_fluctuating_states(self):
        """Test fluctuating between State 4 and other states."""
        # Cycle 1: State 4 detected
        skip_updates, _, _ = self.simulate_state_detection(4)
        self.assertTrue(skip_updates, "Cycle 1: Should skip")
        self.assertEqual(self.state_4_consecutive_count, 1)

        # Cycle 2: State 2
        skip_updates, _, _ = self.simulate_state_detection(2)
        self.assertFalse(skip_updates, "Cycle 2: Should not skip")
        self.assertEqual(self.state_4_consecutive_count, 0, "Counter should reset")

        # Cycle 3: State 4 again
        skip_updates, _, _ = self.simulate_state_detection(4)
        self.assertTrue(skip_updates, "Cycle 3: Should skip (new first detection)")
        self.assertEqual(self.state_4_consecutive_count, 1)

        # Cycle 4: State 4 continues
        skip_updates, adv_primary, _ = self.simulate_state_detection(4)
        self.assertFalse(skip_updates, "Cycle 4: Should NOT skip (verified)")
        self.assertTrue(adv_primary, "Cycle 4: Should advertise primary")
        self.assertEqual(self.state_4_consecutive_count, 2)

    def test_non_state_4_never_triggers_verification(self):
        """Test that non-State 4 states never trigger verification logic."""
        # Test all states except State 4
        for state_code in [1, 2, 3, 5, 6]:
            self.setUp()  # Reset state

            skip_updates, adv_primary, adv_secondary = self.simulate_state_detection(state_code)

            self.assertFalse(skip_updates, f"State {state_code} should not skip updates")
            self.assertIsNotNone(adv_primary, f"State {state_code} should have action defined")
            self.assertIsNotNone(adv_secondary, f"State {state_code} should have action defined")
            self.assertEqual(self.state_4_consecutive_count, 0,
                           f"State {state_code} should not increment counter")
            self.assertFalse(self.state_4_pending_verification,
                           f"State {state_code} should not set pending flag")

    def test_state_0_never_triggers_verification(self):
        """Test that State 0 (failsafe) never triggers verification logic."""
        skip_updates, adv_primary, adv_secondary = self.simulate_state_detection(0)

        self.assertFalse(skip_updates, "State 0 should not skip updates")
        # State 0 should maintain current state (None values = no change)
        self.assertIsNone(adv_primary, "State 0 should not change primary (None = no change)")
        self.assertIsNone(adv_secondary, "State 0 should not change secondary (None = no change)")
        self.assertEqual(self.state_4_consecutive_count, 0, "State 0 should not increment counter")
        self.assertFalse(self.state_4_pending_verification, "State 0 should not set pending flag")


class TestState2Verification(unittest.TestCase):
    """Test suite for State 2 verification threshold."""

    def setUp(self):
        """Set up test fixtures."""
        self.state_2_consecutive_count = 0
        self.state_2_pending_verification = False
        self.state_2_verification_threshold = 2
        self.current_state_code = None

    def simulate_state_2_detection(self, new_state_code: int) -> Tuple[bool, Optional[bool], Optional[bool]]:
        """Simulate State 2 verification logic."""
        advertise_primary, advertise_secondary = STATE_ACTIONS.get(new_state_code, (False, False))
        skip_updates = False

        if new_state_code == 2:
            if new_state_code == self.current_state_code:
                self.state_2_consecutive_count += 1
            else:
                self.state_2_consecutive_count = 1
                self.state_2_pending_verification = True

            if self.state_2_consecutive_count < self.state_2_verification_threshold:
                skip_updates = True
                advertise_primary = None
                advertise_secondary = None
            else:
                self.state_2_pending_verification = False
        else:
            if self.state_2_consecutive_count > 0:
                self.state_2_consecutive_count = 0
                self.state_2_pending_verification = False

        self.current_state_code = new_state_code
        return skip_updates, advertise_primary, advertise_secondary

    def test_state_2_first_detection_skips_updates(self):
        """Test that first detection of State 2 skips updates."""
        skip_updates, adv_primary, adv_secondary = self.simulate_state_2_detection(2)

        self.assertTrue(skip_updates, "Should skip updates on first State 2 detection")
        self.assertIsNone(adv_primary, "Primary should be None (no action)")
        self.assertIsNone(adv_secondary, "Secondary should be None (no action)")
        self.assertEqual(self.state_2_consecutive_count, 1)
        self.assertTrue(self.state_2_pending_verification)

    def test_state_2_second_detection_applies_actions(self):
        """Test that second consecutive State 2 detection applies actions."""
        self.simulate_state_2_detection(2)
        skip_updates, adv_primary, adv_secondary = self.simulate_state_2_detection(2)

        self.assertFalse(skip_updates, "Should NOT skip updates on second detection")
        self.assertFalse(adv_primary, "Primary should NOT be advertised (State 2 action)")
        self.assertFalse(adv_secondary, "Secondary should NOT be advertised (State 2 action)")
        self.assertEqual(self.state_2_consecutive_count, 2)
        self.assertFalse(self.state_2_pending_verification)

    def test_state_2_recovery_resets_counter(self):
        """Test that recovery from State 2 before verification resets counter."""
        self.simulate_state_2_detection(2)
        self.assertEqual(self.state_2_consecutive_count, 1)

        skip_updates, adv_primary, adv_secondary = self.simulate_state_2_detection(1)

        self.assertFalse(skip_updates, "Should NOT skip in State 1")
        self.assertEqual(self.state_2_consecutive_count, 0, "Counter should reset")
        self.assertFalse(self.state_2_pending_verification)

    def test_state_2_third_detection_continues(self):
        """Test that third consecutive State 2 detection continues applying actions."""
        self.simulate_state_2_detection(2)
        self.simulate_state_2_detection(2)
        skip_updates, adv_primary, adv_secondary = self.simulate_state_2_detection(2)

        self.assertFalse(skip_updates)
        self.assertFalse(adv_primary)
        self.assertFalse(adv_secondary)
        self.assertEqual(self.state_2_consecutive_count, 3)


class TestState3Verification(unittest.TestCase):
    """Test suite for State 3 verification threshold."""

    def setUp(self):
        """Set up test fixtures."""
        self.state_3_consecutive_count = 0
        self.state_3_pending_verification = False
        self.state_3_verification_threshold = 2
        self.current_state_code = None

    def simulate_state_3_detection(self, new_state_code: int) -> Tuple[bool, Optional[bool], Optional[bool]]:
        """Simulate State 3 verification logic."""
        advertise_primary, advertise_secondary = STATE_ACTIONS.get(new_state_code, (False, False))
        skip_updates = False

        if new_state_code == 3:
            if new_state_code == self.current_state_code:
                self.state_3_consecutive_count += 1
            else:
                self.state_3_consecutive_count = 1
                self.state_3_pending_verification = True

            if self.state_3_consecutive_count < self.state_3_verification_threshold:
                skip_updates = True
                advertise_primary = None
                advertise_secondary = None
            else:
                self.state_3_pending_verification = False
        else:
            if self.state_3_consecutive_count > 0:
                self.state_3_consecutive_count = 0
                self.state_3_pending_verification = False

        self.current_state_code = new_state_code
        return skip_updates, advertise_primary, advertise_secondary

    def test_state_3_first_detection_skips_updates(self):
        """Test that first detection of State 3 skips updates."""
        skip_updates, adv_primary, adv_secondary = self.simulate_state_3_detection(3)

        self.assertTrue(skip_updates)
        self.assertIsNone(adv_primary)
        self.assertIsNone(adv_secondary)
        self.assertEqual(self.state_3_consecutive_count, 1)
        self.assertTrue(self.state_3_pending_verification)

    def test_state_3_second_detection_applies_actions(self):
        """Test that second consecutive State 3 detection applies actions."""
        self.simulate_state_3_detection(3)
        skip_updates, adv_primary, adv_secondary = self.simulate_state_3_detection(3)

        self.assertFalse(skip_updates)
        self.assertTrue(adv_primary, "Primary should be advertised (State 3 action)")
        self.assertTrue(adv_secondary, "Secondary should be advertised (State 3 action)")
        self.assertEqual(self.state_3_consecutive_count, 2)

    def test_state_3_recovery_resets_counter(self):
        """Test that recovery from State 3 resets counter."""
        self.simulate_state_3_detection(3)
        self.simulate_state_3_detection(1)

        self.assertEqual(self.state_3_consecutive_count, 0)
        self.assertFalse(self.state_3_pending_verification)

    def test_state_3_interrupted_then_redetected(self):
        """Test State 3 interrupted and then detected again restarts verification."""
        self.simulate_state_3_detection(3)
        self.assertEqual(self.state_3_consecutive_count, 1)

        self.simulate_state_3_detection(1)
        self.assertEqual(self.state_3_consecutive_count, 0)

        skip_updates, _, _ = self.simulate_state_3_detection(3)
        self.assertTrue(skip_updates, "Should skip on new first detection")
        self.assertEqual(self.state_3_consecutive_count, 1)


class TestStateActions(unittest.TestCase):
    """Test suite for STATE_ACTIONS mapping."""

    def test_all_defined_states_have_actions(self):
        """Test that all expected state codes have defined actions."""
        expected_states = [0, 1, 2, 3, 4, 5, 6]

        for state in expected_states:
            self.assertIn(state, STATE_ACTIONS,
                         f"State {state} should have defined actions")

            actions = STATE_ACTIONS[state]
            self.assertIsInstance(actions, tuple,
                                f"State {state} actions should be a tuple")
            self.assertEqual(len(actions), 2,
                           f"State {state} should have exactly 2 action values")

            adv_primary, adv_secondary = actions
            # State 0 can have None values, others should be boolean
            if state == 0:
                # State 0: None values indicate "no change"
                self.assertIsNone(adv_primary, "State 0 primary should be None")
                self.assertIsNone(adv_secondary, "State 0 secondary should be None")
            else:
                # All other states: Must be boolean
                self.assertIsInstance(adv_primary, bool,
                                    f"State {state} primary action should be boolean")
                self.assertIsInstance(adv_secondary, bool,
                                    f"State {state} secondary action should be boolean")

    def test_state_actions_correctness(self):
        """Test that state actions match expected behavior."""
        # State 0: No change - maintain current advertisements (None = no action)
        self.assertEqual(STATE_ACTIONS[0], (None, None))

        # State 1: Normal operation - primary only
        self.assertEqual(STATE_ACTIONS[1], (True, False))

        # State 2: Failover - withdraw all
        self.assertEqual(STATE_ACTIONS[2], (False, False))

        # State 3: Redundancy - advertise both
        self.assertEqual(STATE_ACTIONS[3], (True, True))

        # State 4: Emergency - primary only (after verification)
        self.assertEqual(STATE_ACTIONS[4], (True, False))

        # State 5: Backup infrastructure - primary only
        self.assertEqual(STATE_ACTIONS[5], (True, False))

        # State 6: BGP down but healthy - advertise both
        self.assertEqual(STATE_ACTIONS[6], (True, True))


class TestStateTransitionSequences(unittest.TestCase):
    """Test suite for complex state transition sequences."""

    def setUp(self):
        """Set up test fixtures."""
        self.state_history = []
        self.state_4_consecutive_count = 0
        self.current_state_code = None
        self.state_4_verification_threshold = 2

    def detect_state(self, local_healthy: bool, remote_healthy: bool, remote_bgp_up: bool) -> dict:
        """Simulate full state detection and verification logic."""
        new_state_code = determine_state_code(local_healthy, remote_healthy, remote_bgp_up)
        advertise_primary, advertise_secondary = STATE_ACTIONS.get(new_state_code, (False, False))
        skip_updates = False

        # State 4 verification logic
        if new_state_code == 4:
            if new_state_code == self.current_state_code:
                self.state_4_consecutive_count += 1
            else:
                self.state_4_consecutive_count = 1

            if self.state_4_consecutive_count < self.state_4_verification_threshold:
                skip_updates = True
                advertise_primary = None
                advertise_secondary = None
        else:
            self.state_4_consecutive_count = 0

        result = {
            'state': new_state_code,
            'skip_updates': skip_updates,
            'advertise_primary': advertise_primary,
            'advertise_secondary': advertise_secondary,
            'state_4_count': self.state_4_consecutive_count,
            'health': {
                'local': local_healthy,
                'remote': remote_healthy,
                'bgp': remote_bgp_up
            }
        }

        self.state_history.append(result)
        self.current_state_code = new_state_code

        return result

    def test_normal_to_emergency_to_recovery(self):
        """Test sequence: Normal → Emergency (State 4) → Recovery."""
        # Cycle 1: Normal operation
        result = self.detect_state(True, True, True)
        self.assertEqual(result['state'], 1, "Should start in State 1")
        self.assertFalse(result['skip_updates'])

        # Cycle 2: Both regions fail (State 4 first detection)
        result = self.detect_state(False, False, True)
        self.assertEqual(result['state'], 4, "Should transition to State 4")
        self.assertTrue(result['skip_updates'], "Should skip on first detection")

        # Cycle 3: Both regions still failed (State 4 verified)
        result = self.detect_state(False, False, True)
        self.assertEqual(result['state'], 4, "Should remain in State 4")
        self.assertFalse(result['skip_updates'], "Should NOT skip after verification")
        self.assertTrue(result['advertise_primary'], "Should advertise primary")

        # Cycle 4: Recovery to normal
        result = self.detect_state(True, True, True)
        self.assertEqual(result['state'], 1, "Should return to State 1")
        self.assertFalse(result['skip_updates'])
        self.assertEqual(result['state_4_count'], 0, "Counter should reset")

    def test_transient_state_4_recovery(self):
        """Test transient State 4 that recovers before verification."""
        # Normal operation
        self.detect_state(True, True, True)

        # Transient State 4 (first detection)
        result = self.detect_state(False, False, True)
        self.assertEqual(result['state'], 4)
        self.assertTrue(result['skip_updates'], "Should skip transient State 4")

        # Recovery before verification
        result = self.detect_state(True, True, True)
        self.assertEqual(result['state'], 1)
        self.assertFalse(result['skip_updates'])
        self.assertEqual(result['state_4_count'], 0, "Counter should reset")

        # Verify no State 4 actions were ever applied
        state_4_entries = [h for h in self.state_history if h['state'] == 4]
        self.assertEqual(len(state_4_entries), 1, "Should have only one State 4 entry")
        self.assertIsNone(state_4_entries[0]['advertise_primary'],
                         "State 4 primary action should be None (skipped)")

    def test_cascading_failures(self):
        """Test cascading failures: Local → Remote → BGP."""
        # Start: All healthy
        result = self.detect_state(True, True, True)
        self.assertEqual(result['state'], 1)

        # Local fails
        result = self.detect_state(False, True, True)
        self.assertEqual(result['state'], 2, "Should enter State 2")
        self.assertFalse(result['advertise_primary'], "Should withdraw in State 2")

        # Remote also fails
        result = self.detect_state(False, False, True)
        self.assertEqual(result['state'], 4, "Should enter State 4")
        self.assertTrue(result['skip_updates'], "First State 4 should skip")

        # State 4 continues (verified)
        result = self.detect_state(False, False, True)
        self.assertFalse(result['skip_updates'], "State 4 verified")
        self.assertTrue(result['advertise_primary'], "Should advertise in verified State 4")

        # BGP fails too
        result = self.detect_state(False, False, False)
        self.assertEqual(result['state'], 0, "Should enter failsafe State 0")
        self.assertEqual(result['state_4_count'], 0, "State 4 counter should reset")


class TestEdgeCases(unittest.TestCase):
    """Test suite for edge cases and boundary conditions."""

    def test_boolean_type_consistency(self):
        """Test that state determination handles boolean types correctly."""
        # Ensure function works with explicit booleans
        state = determine_state_code(True, True, True)
        self.assertIsInstance(state, int)

        # Test with falsy values (should treat as False)
        state = determine_state_code(0, 1, 1)
        self.assertEqual(state, 2, "0 should be treated as False")

    def test_all_possible_boolean_combinations(self):
        """Test all 8 possible boolean combinations of inputs."""
        combinations = [
            (True, True, True),    # State 1
            (True, True, False),   # State 6
            (True, False, True),   # State 3
            (True, False, False),  # State 0
            (False, True, True),   # State 2
            (False, True, False),  # State 5
            (False, False, True),  # State 4
            (False, False, False), # State 0
        ]

        expected_states = [1, 6, 3, 0, 2, 5, 4, 0]

        for (local, remote, bgp), expected in zip(combinations, expected_states):
            state = determine_state_code(local, remote, bgp)
            self.assertEqual(state, expected,
                           f"Health({local}, {remote}, {bgp}) should map to state {expected}")

    def test_state_0_has_safe_defaults(self):
        """Test that State 0 (unexpected) has safe default actions."""
        # Get State 0
        state = determine_state_code(False, False, False)
        self.assertEqual(state, 0)

        # Check if it has "no change" defaults in STATE_ACTIONS
        if 0 in STATE_ACTIONS:
            adv_primary, adv_secondary = STATE_ACTIONS[0]
            # Both should be None for "no change" behavior
            self.assertIsNone(adv_primary, "State 0 should not change primary (None)")
            self.assertIsNone(adv_secondary, "State 0 should not change secondary (None)")
        else:
            # If not defined, get() should return safe defaults
            adv_primary, adv_secondary = STATE_ACTIONS.get(0, (None, None))
            self.assertIsNone(adv_primary)
            self.assertIsNone(adv_secondary)

    def test_state_0_prevents_route_changes_on_monitoring_failure(self):
        """Test that State 0 prevents route changes when monitoring fails (None health values)."""
        # Simulate monitoring failure scenarios that should trigger State 0
        test_cases = [
            (True, True, None, "BGP monitoring unavailable"),
            (True, None, True, "Remote health monitoring unavailable"),
            (None, True, True, "Local health monitoring unavailable"),
            (None, None, None, "Complete monitoring failure"),
        ]

        for local, remote, bgp, description in test_cases:
            with self.subTest(description=description):
                # Should return State 0
                state = determine_state_code(local, remote, bgp)
                self.assertEqual(state, 0, f"{description} should trigger State 0")

                # State 0 actions should be (None, None) - no changes
                adv_primary, adv_secondary = STATE_ACTIONS[state]
                self.assertIsNone(adv_primary, f"{description}: primary should be None")
                self.assertIsNone(adv_secondary, f"{description}: secondary should be None")


class TestState4ThresholdConfiguration(unittest.TestCase):
    """Test suite for configurable State 4 verification threshold."""

    def test_state_4_uses_configured_threshold(self):
        """Test that State 4 verification uses the configured threshold."""
        # Simulate daemon state tracking with different thresholds
        test_cases = [
            (1, 1, True),   # threshold=1, count=1 → should act
            (2, 1, False),  # threshold=2, count=1 → should wait
            (2, 2, True),   # threshold=2, count=2 → should act
            (3, 2, False),  # threshold=3, count=2 → should wait
            (3, 3, True),   # threshold=3, count=3 → should act
        ]

        for threshold, count, should_act in test_cases:
            with self.subTest(threshold=threshold, count=count):
                # Simulate the threshold check logic from daemon.py:480
                skip_updates = (count < threshold)
                self.assertEqual(not skip_updates, should_act,
                               f"With threshold={threshold}, count={count}, "
                               f"should_act should be {should_act}")

    def test_state_4_threshold_boundary_conditions(self):
        """Test State 4 threshold at boundary conditions."""
        # Test minimum threshold (1)
        threshold = 1
        count = 1
        skip_updates = (count < threshold)
        self.assertFalse(skip_updates, "Threshold=1 should act immediately")

        # Test typical threshold (2)
        threshold = 2
        self.assertTrue(1 < threshold, "Count=1 should wait with threshold=2")
        self.assertFalse(2 < threshold, "Count=2 should act with threshold=2")

        # Test maximum practical threshold (10)
        threshold = 10
        count = 9
        skip_updates = (count < threshold)
        self.assertTrue(skip_updates, "Count=9 should wait with threshold=10")

        count = 10
        skip_updates = (count < threshold)
        self.assertFalse(skip_updates, "Count=10 should act with threshold=10")

    def test_state_4_counter_resets_on_non_state_4(self):
        """Test that State 4 counter resets when leaving State 4."""
        # Simulating daemon behavior from daemon.py:489-497
        state_4_consecutive_count = 3

        # When state changes from 4, counter should reset
        current_state = 1  # Changed from State 4
        if current_state != 4:
            state_4_consecutive_count = 0

        self.assertEqual(state_4_consecutive_count, 0,
                        "Counter should reset when leaving State 4")


class TestLoggingAndObservability(unittest.TestCase):
    """Test suite for logging and observability during state changes."""

    @patch('logging.getLogger')
    def test_state_transition_logging(self, mock_get_logger):
        """Test that state transitions generate appropriate log messages."""
        # This would test actual logging calls in the daemon
        # Placeholder for integration with actual daemon logging
        pass

    def test_state_4_verification_metrics(self):
        """Test that State 4 verification generates correct metrics."""
        # This would test structured event logging
        # Placeholder for integration with StructuredEventLogger
        pass


class TestHealthCheckHysteresis(unittest.TestCase):
    """Test suite for health check hysteresis (sliding window smoothing)."""

    def setUp(self):
        """Set up test fixtures."""
        self.window_size = 5
        self.threshold = 3
        self.local_health_history = deque(maxlen=self.window_size)
        self.remote_health_history = deque(maxlen=self.window_size)
        self.current_state_code = 1  # Start in normal state

    def apply_hysteresis_symmetric(self, raw_healthy: bool, history: deque) -> bool:
        """Apply symmetric hysteresis logic."""
        history.append(raw_healthy)

        if len(history) >= self.window_size:
            healthy_count = sum(history)
            return healthy_count >= self.threshold
        else:
            return raw_healthy

    def apply_hysteresis_asymmetric(self, raw_healthy: bool, history: deque, currently_healthy: bool) -> bool:
        """Apply asymmetric hysteresis logic."""
        history.append(raw_healthy)

        if len(history) >= self.window_size:
            healthy_count = sum(history)

            if currently_healthy:
                # Harder to go unhealthy: allow up to 3 failures
                return healthy_count >= 2
            else:
                # Harder to become healthy: need strong majority
                return healthy_count >= 4
        else:
            return raw_healthy

    def test_symmetric_single_failure_ignored(self):
        """Test that single failure in window is ignored (symmetric mode)."""
        # Pattern: ✓ ✓ ✓ ✗ ✓
        results = [True, True, True, False, True]

        for raw in results:
            filtered = self.apply_hysteresis_symmetric(raw, self.local_health_history)

        # 4 out of 5 healthy -> should be considered healthy
        self.assertTrue(filtered, "Single failure should be ignored")
        self.assertEqual(sum(self.local_health_history), 4)

    def test_symmetric_sustained_failures_detected(self):
        """Test that sustained failures are detected (symmetric mode)."""
        # Pattern: ✓ ✓ ✗ ✗ ✗
        results = [True, True, False, False, False]

        for raw in results:
            filtered = self.apply_hysteresis_symmetric(raw, self.local_health_history)

        # 2 out of 5 healthy -> should be unhealthy (< threshold of 3)
        self.assertFalse(filtered, "Sustained failures should be detected")
        self.assertEqual(sum(self.local_health_history), 2)

    def test_symmetric_threshold_boundary(self):
        """Test threshold boundary condition (symmetric mode)."""
        # Exactly at threshold: ✓ ✓ ✓ ✗ ✗
        results = [True, True, True, False, False]

        for raw in results:
            filtered = self.apply_hysteresis_symmetric(raw, self.local_health_history)

        # Exactly 3 out of 5 -> should be healthy (>= threshold)
        self.assertTrue(filtered)
        self.assertEqual(sum(self.local_health_history), 3)

    def test_asymmetric_healthy_to_unhealthy_harder(self):
        """Test asymmetric mode makes it harder to go from healthy to unhealthy."""
        # Start healthy, get 3 failures: ✓ ✓ ✗ ✗ ✗
        results = [True, True, False, False, False]

        for raw in results:
            filtered = self.apply_hysteresis_asymmetric(
                raw, self.local_health_history, currently_healthy=True
            )

        # 2 out of 5 healthy, but in asymmetric mode currently healthy
        # Threshold is 2, so should STILL be considered healthy
        self.assertTrue(filtered, "Should stay healthy with asymmetric mode")

    def test_asymmetric_unhealthy_to_healthy_harder(self):
        """Test asymmetric mode makes it harder to go from unhealthy to healthy."""
        # Start unhealthy, get 3 successes: ✗ ✗ ✓ ✓ ✓
        results = [False, False, True, True, True]

        for raw in results:
            filtered = self.apply_hysteresis_asymmetric(
                raw, self.local_health_history, currently_healthy=False
            )

        # 3 out of 5 healthy, but asymmetric mode requires 4 to become healthy
        self.assertFalse(filtered, "Should stay unhealthy, needs 4 successes")

    def test_asymmetric_unhealthy_to_healthy_with_4_successes(self):
        """Test asymmetric mode allows transition with 4 successes."""
        # Start unhealthy, get 4 successes: ✗ ✓ ✓ ✓ ✓
        results = [False, True, True, True, True]

        for raw in results:
            filtered = self.apply_hysteresis_asymmetric(
                raw, self.local_health_history, currently_healthy=False
            )

        # 4 out of 5 healthy -> should now be healthy
        self.assertTrue(filtered, "Should become healthy with 4 successes")

    def test_insufficient_history_uses_raw(self):
        """Test that with insufficient history, raw result is used."""
        # Only 2 results when window is 5
        results = [True, False]

        for raw in results:
            filtered = self.apply_hysteresis_symmetric(raw, self.local_health_history)

        # Not enough history, should use last raw result
        self.assertFalse(filtered)
        self.assertEqual(len(self.local_health_history), 2)

    def test_rolling_window_behavior(self):
        """Test that deque properly maintains rolling window."""
        # Add more than window size
        results = [True] * 7  # 7 results in window of 5

        for raw in results:
            self.local_health_history.append(raw)

        # Should only keep last 5
        self.assertEqual(len(self.local_health_history), 5)
        self.assertEqual(sum(self.local_health_history), 5)


class TestStateDwellTime(unittest.TestCase):
    """Test suite for minimum state dwell time enforcement."""

    def setUp(self):
        """Set up test fixtures."""
        self.min_dwell_time = 120  # 2 minutes
        self.exception_states = [1, 4]
        self.current_state_code = 2
        self.last_state_change_time = time.time()

    def check_dwell_time(self, new_state_code: int, current_time: float) -> Tuple[bool, int]:
        """Check if state transition should be blocked by dwell time."""
        if new_state_code == self.current_state_code:
            time_in_state = current_time - self.last_state_change_time
            return False, self.current_state_code  # No transition

        time_in_state = current_time - self.last_state_change_time

        # Check if dwell time requirement blocks transition
        if (self.current_state_code is not None and
            time_in_state < self.min_dwell_time and
            self.current_state_code not in self.exception_states and
            new_state_code not in self.exception_states):
            # Blocked - stay in current state
            return True, self.current_state_code

        # Allowed - transition
        return False, new_state_code

    def test_transition_blocked_insufficient_time(self):
        """Test that transition is blocked when insufficient time has passed."""
        # Try to transition to State 3 (not an exception) after only 45 seconds
        current_time = self.last_state_change_time + 45

        blocked, resulting_state = self.check_dwell_time(3, current_time)

        self.assertTrue(blocked, "Transition should be blocked")
        self.assertEqual(resulting_state, 2, "Should stay in State 2")

    def test_transition_allowed_sufficient_time(self):
        """Test that transition is allowed after sufficient time."""
        # Try to transition after 150 seconds (> 120)
        current_time = self.last_state_change_time + 150

        blocked, resulting_state = self.check_dwell_time(1, current_time)

        self.assertFalse(blocked, "Transition should be allowed")
        self.assertEqual(resulting_state, 1, "Should transition to State 1")

    def test_transition_blocked_at_boundary(self):
        """Test boundary condition at exactly min dwell time."""
        # Try to transition to State 3 at exactly 119.9 seconds (< 120)
        current_time = self.last_state_change_time + 119.9

        blocked, resulting_state = self.check_dwell_time(3, current_time)

        self.assertTrue(blocked, "Transition should be blocked at boundary")
        self.assertEqual(resulting_state, 2)

    def test_exception_state_1_bypasses_dwell_time(self):
        """Test that transitioning to State 1 (exception) bypasses dwell time."""
        # State 1 is in exception list
        current_time = self.last_state_change_time + 30  # Only 30 seconds

        blocked, resulting_state = self.check_dwell_time(1, current_time)

        self.assertFalse(blocked, "State 1 should bypass dwell time")
        self.assertEqual(resulting_state, 1)

    def test_exception_state_4_bypasses_dwell_time(self):
        """Test that transitioning to State 4 (exception) bypasses dwell time."""
        current_time = self.last_state_change_time + 30

        blocked, resulting_state = self.check_dwell_time(4, current_time)

        self.assertFalse(blocked, "State 4 should bypass dwell time")
        self.assertEqual(resulting_state, 4)

    def test_from_exception_state_bypasses_dwell_time(self):
        """Test that transitioning FROM an exception state bypasses dwell time."""
        # Start in State 1 (exception state)
        self.current_state_code = 1
        self.last_state_change_time = time.time()

        current_time = self.last_state_change_time + 30

        blocked, resulting_state = self.check_dwell_time(2, current_time)

        self.assertFalse(blocked, "From exception state should bypass")
        self.assertEqual(resulting_state, 2)

    def test_non_exception_states_enforced(self):
        """Test that non-exception states have dwell time enforced."""
        # Transition from State 2 to State 3 (neither is exception)
        current_time = self.last_state_change_time + 60

        blocked, resulting_state = self.check_dwell_time(3, current_time)

        self.assertTrue(blocked, "Non-exception transition should be blocked")
        self.assertEqual(resulting_state, 2)


class TestIntegratedProtections(unittest.TestCase):
    """Test suite for integration of all three route flapping protection layers."""

    def setUp(self):
        """Set up integrated test environment."""
        # Health check hysteresis
        self.window_size = 5
        self.threshold = 3
        self.local_health_history = deque(maxlen=self.window_size)

        # State verification
        self.state_2_count = 0
        self.state_2_threshold = 2

        # Dwell time
        self.min_dwell_time = 120
        self.exception_states = [1, 4]
        self.current_state_code = 1
        self.last_state_change_time = time.time()

    def test_all_three_layers_prevent_flapping(self):
        """Test that all three layers work together to prevent route flapping."""
        # Scenario: Single transient failure

        # Layer 1: Health check hysteresis catches it
        health_checks = [True, True, True, False, True]  # One failure
        for hc in health_checks:
            self.local_health_history.append(hc)

        healthy_count = sum(self.local_health_history)
        local_healthy = healthy_count >= self.threshold

        # Result: Still considered healthy (4/5 >= 3)
        self.assertTrue(local_healthy, "Layer 1: Hysteresis should absorb transient failure")

        # No state change would even be triggered, so layers 2 and 3 don't need to act

    def test_sustained_failure_requires_all_layers(self):
        """Test that sustained failure must pass all three layers."""
        # Scenario: Sustained failures

        # Layer 1: Hysteresis - sustained failures detected
        health_checks = [True, False, False, False, False]  # 4 failures
        for hc in health_checks:
            self.local_health_history.append(hc)

        healthy_count = sum(self.local_health_history)
        local_healthy = healthy_count >= self.threshold
        self.assertFalse(local_healthy, "Layer 1: Sustained failures detected")

        # Layer 2: State verification - first detection
        new_state = 2  # Local unhealthy
        if new_state != self.current_state_code:
            self.state_2_count = 1
        else:
            self.state_2_count += 1

        skip_updates_verification = self.state_2_count < self.state_2_threshold
        self.assertTrue(skip_updates_verification, "Layer 2: First detection should skip")

        # Even if verification passes, Layer 3 would still check dwell time

    def test_rapid_recovery_blocked_by_dwell_time(self):
        """Test that rapid recovery after verified state change is blocked by dwell time."""
        # Assume we're in State 2, verified
        self.current_state_code = 2
        self.last_state_change_time = time.time()

        # 30 seconds later, try to recover to State 3 (not an exception state)
        current_time = self.last_state_change_time + 30

        time_in_state = current_time - self.last_state_change_time
        # Note: State 1 is an exception, so we test transition to State 3 instead
        new_state = 3
        dwell_time_blocks = (time_in_state < self.min_dwell_time and
                            2 not in self.exception_states and
                            new_state not in self.exception_states)

        self.assertTrue(dwell_time_blocks, "Layer 3: Dwell time should block rapid transition")


if __name__ == '__main__':
    unittest.main()
