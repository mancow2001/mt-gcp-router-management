"""
Unit Tests for Configuration Management

This test module validates the configuration loading, validation, and
backward compatibility for the GCP Route Management Daemon.

Test Coverage:
    - Configuration loading from environment variables
    - Per-service retry configuration
    - State 2/3/4 verification threshold configuration
    - Route flapping protection configuration (hysteresis, dwell time)
    - Configuration validation (ranges, types, required fields, constraints)
    - Backward compatibility with legacy settings
    - Default values

Author: Nathan Bray
Created: 2025-11-02
Updated: 2025-11-02 - Added route flapping protection configuration tests
"""

import unittest
import os
import sys
from unittest.mock import patch
from importlib import reload

# Import config module with fallback for different import contexts
try:
    from . import config as config_module
except ImportError:
    import config as config_module


class TestConfigLoading(unittest.TestCase):
    """Test configuration loading from environment variables."""

    def setUp(self):
        """Save original environment and clear test variables."""
        self.original_env = os.environ.copy()

    def tearDown(self):
        """Restore original environment."""
        os.environ.clear()
        os.environ.update(self.original_env)

    def test_default_state_4_threshold(self):
        """Test State 4 verification threshold has correct default."""
        reload(config_module)
        cfg = config_module.Config()
        self.assertEqual(cfg.state_4_verification_threshold, 2,
                        "Default State 4 threshold should be 2")

    def test_custom_state_4_threshold(self):
        """Test State 4 threshold loads from environment variable."""
        test_values = [1, 3, 5, 10]
        for value in test_values:
            with self.subTest(threshold=value):
                os.environ['STATE_4_VERIFICATION_THRESHOLD'] = str(value)
                reload(config_module)
                cfg = config_module.Config()
                self.assertEqual(cfg.state_4_verification_threshold, value,
                               f"Threshold should load as {value}")

    def test_default_per_service_retries(self):
        """Test per-service retry configuration has correct defaults."""
        reload(config_module)
        cfg = config_module.Config()

        self.assertEqual(cfg.max_retries_health_check, 5,
                        "Health check retries should default to 5")
        self.assertEqual(cfg.max_retries_bgp_check, 4,
                        "BGP check retries should default to 4")
        self.assertEqual(cfg.max_retries_bgp_update, 2,
                        "BGP update retries should default to 2")
        self.assertEqual(cfg.max_retries_cloudflare, 3,
                        "Cloudflare retries should default to 3")
        self.assertEqual(cfg.max_retries, 3,
                        "Legacy max_retries should default to 3")

    def test_custom_per_service_retries(self):
        """Test per-service retries load from environment variables."""
        os.environ['MAX_RETRIES_HEALTH_CHECK'] = '8'
        os.environ['MAX_RETRIES_BGP_CHECK'] = '6'
        os.environ['MAX_RETRIES_BGP_UPDATE'] = '1'
        os.environ['MAX_RETRIES_CLOUDFLARE'] = '5'

        reload(config_module)
        cfg = config_module.Config()

        self.assertEqual(cfg.max_retries_health_check, 8)
        self.assertEqual(cfg.max_retries_bgp_check, 6)
        self.assertEqual(cfg.max_retries_bgp_update, 1)
        self.assertEqual(cfg.max_retries_cloudflare, 5)


class TestConfigValidation(unittest.TestCase):
    """Test configuration validation logic."""

    def setUp(self):
        """Save original environment."""
        self.original_env = os.environ.copy()

    def tearDown(self):
        """Restore original environment."""
        os.environ.clear()
        os.environ.update(self.original_env)

    def test_state_4_threshold_validation_range(self):
        """Test State 4 threshold validation rejects out-of-range values."""
        invalid_values = [
            ('0', 'below minimum'),
            ('11', 'above maximum'),
            ('-1', 'negative'),
        ]

        for value_str, reason in invalid_values:
            with self.subTest(value=value_str, reason=reason):
                os.environ['STATE_4_VERIFICATION_THRESHOLD'] = value_str
                reload(config_module)
                cfg = config_module.Config()
                errors = config_module.validate_configuration(cfg)

                threshold_errors = [e for e in errors
                                   if 'STATE_4_VERIFICATION_THRESHOLD' in e]
                self.assertGreater(len(threshold_errors), 0,
                                 f"Should reject {value_str} ({reason})")

    def test_state_4_threshold_validation_valid_range(self):
        """Test State 4 threshold validation accepts valid range."""
        valid_values = [1, 2, 5, 10]

        for value in valid_values:
            with self.subTest(value=value):
                os.environ['STATE_4_VERIFICATION_THRESHOLD'] = str(value)
                # Clear other required vars to focus on threshold
                for var in ['GCP_PROJECT', 'GOOGLE_APPLICATION_CREDENTIALS']:
                    if var in os.environ:
                        del os.environ[var]

                reload(config_module)
                cfg = config_module.Config()
                errors = config_module.validate_configuration(cfg)

                threshold_errors = [e for e in errors
                                   if 'STATE_4_VERIFICATION_THRESHOLD' in e]
                self.assertEqual(len(threshold_errors), 0,
                               f"Should accept valid value {value}")

    def test_retry_validation_range(self):
        """Test retry configuration validation."""
        os.environ['MAX_RETRIES_HEALTH_CHECK'] = '15'  # Too high

        # Clear required vars
        for var in ['GCP_PROJECT', 'GOOGLE_APPLICATION_CREDENTIALS']:
            if var in os.environ:
                del os.environ[var]

        reload(config_module)
        cfg = config_module.Config()
        errors = config_module.validate_configuration(cfg)

        retry_errors = [e for e in errors if 'MAX_RETRIES_HEALTH_CHECK' in e]
        self.assertGreater(len(retry_errors), 0,
                         "Should reject out-of-range retry value")

    def test_retry_validation_valid_range(self):
        """Test retry validation accepts valid range."""
        valid_values = {'MAX_RETRIES_HEALTH_CHECK': '5',
                       'MAX_RETRIES_BGP_UPDATE': '2'}

        for var, value in valid_values.items():
            with self.subTest(variable=var):
                os.environ[var] = value

                # Clear required vars
                for req_var in ['GCP_PROJECT', 'GOOGLE_APPLICATION_CREDENTIALS']:
                    if req_var in os.environ:
                        del os.environ[req_var]

                reload(config_module)
                cfg = config_module.Config()
                errors = config_module.validate_configuration(cfg)

                var_errors = [e for e in errors if var in e]
                self.assertEqual(len(var_errors), 0,
                               f"Should accept valid value for {var}")


class TestBackwardCompatibility(unittest.TestCase):
    """Test backward compatibility with legacy configuration."""

    def setUp(self):
        """Save original environment and clear test variables."""
        self.original_env = os.environ.copy()

        # Clear all retry-related vars
        for key in list(os.environ.keys()):
            if 'RETRY' in key or 'STATE_4' in key:
                del os.environ[key]

    def tearDown(self):
        """Restore original environment."""
        os.environ.clear()
        os.environ.update(self.original_env)

    def test_legacy_max_retries_still_works(self):
        """Test that legacy MAX_RETRIES setting still works."""
        os.environ['MAX_RETRIES'] = '3'

        reload(config_module)
        cfg = config_module.Config()

        self.assertEqual(cfg.max_retries, 3,
                        "Legacy MAX_RETRIES should still work")

    def test_new_fields_have_defaults_with_legacy_config(self):
        """Test new fields have defaults even with only legacy config."""
        os.environ['MAX_RETRIES'] = '3'

        reload(config_module)
        cfg = config_module.Config()

        # New fields should have their own defaults
        self.assertEqual(cfg.max_retries_health_check, 5,
                        "Health check retries should have default")
        self.assertEqual(cfg.max_retries_bgp_update, 2,
                        "BGP update retries should have default")
        self.assertEqual(cfg.state_4_verification_threshold, 2,
                        "State 4 threshold should have default")

    def test_no_config_uses_all_defaults(self):
        """Test that no configuration uses sensible defaults."""
        # Clear all retry and state config
        for key in list(os.environ.keys()):
            if 'RETRY' in key or 'STATE_4' in key:
                del os.environ[key]

        reload(config_module)
        cfg = config_module.Config()

        # All should have defaults
        self.assertEqual(cfg.max_retries, 3)
        self.assertEqual(cfg.max_retries_health_check, 5)
        self.assertEqual(cfg.max_retries_bgp_check, 4)
        self.assertEqual(cfg.max_retries_bgp_update, 2)
        self.assertEqual(cfg.max_retries_cloudflare, 3)
        self.assertEqual(cfg.state_4_verification_threshold, 2)

    def test_new_config_overrides_defaults(self):
        """Test that new config properly overrides defaults."""
        os.environ['MAX_RETRIES_HEALTH_CHECK'] = '8'
        os.environ['MAX_RETRIES_BGP_UPDATE'] = '1'
        os.environ['STATE_4_VERIFICATION_THRESHOLD'] = '5'

        reload(config_module)
        cfg = config_module.Config()

        # Custom values should be used
        self.assertEqual(cfg.max_retries_health_check, 8)
        self.assertEqual(cfg.max_retries_bgp_update, 1)
        self.assertEqual(cfg.state_4_verification_threshold, 5)

        # Non-overridden should still have defaults
        self.assertEqual(cfg.max_retries_bgp_check, 4)
        self.assertEqual(cfg.max_retries_cloudflare, 3)


class TestRouteFlapProtectionConfig(unittest.TestCase):
    """Test configuration for route flapping protection features."""

    def setUp(self):
        """Save original environment."""
        self.original_env = os.environ.copy()

    def tearDown(self):
        """Restore original environment."""
        os.environ.clear()
        os.environ.update(self.original_env)

    def test_state_2_3_verification_threshold_defaults(self):
        """Test State 2 and 3 verification thresholds have correct defaults."""
        reload(config_module)
        cfg = config_module.Config()
        self.assertEqual(cfg.state_2_verification_threshold, 2,
                        "Default State 2 threshold should be 2")
        self.assertEqual(cfg.state_3_verification_threshold, 2,
                        "Default State 3 threshold should be 2")

    def test_state_2_verification_threshold_validation_invalid(self):
        """Test State 2 threshold validation rejects out-of-range values."""
        invalid_values = [('0', 'below minimum'), ('11', 'above maximum')]

        for value_str, reason in invalid_values:
            with self.subTest(value=value_str, reason=reason):
                os.environ['STATE_2_VERIFICATION_THRESHOLD'] = value_str
                for var in ['GCP_PROJECT', 'GOOGLE_APPLICATION_CREDENTIALS']:
                    if var in os.environ:
                        del os.environ[var]

                reload(config_module)
                cfg = config_module.Config()
                errors = config_module.validate_configuration(cfg)

                threshold_errors = [e for e in errors if 'STATE_2_VERIFICATION_THRESHOLD' in e]
                self.assertGreater(len(threshold_errors), 0,
                                 f"Should reject {value_str} ({reason})")

    def test_state_3_verification_threshold_validation_invalid(self):
        """Test State 3 threshold validation rejects out-of-range values."""
        invalid_values = [('0', 'below minimum'), ('11', 'above maximum')]

        for value_str, reason in invalid_values:
            with self.subTest(value=value_str, reason=reason):
                os.environ['STATE_3_VERIFICATION_THRESHOLD'] = value_str
                for var in ['GCP_PROJECT', 'GOOGLE_APPLICATION_CREDENTIALS']:
                    if var in os.environ:
                        del os.environ[var]

                reload(config_module)
                cfg = config_module.Config()
                errors = config_module.validate_configuration(cfg)

                threshold_errors = [e for e in errors if 'STATE_3_VERIFICATION_THRESHOLD' in e]
                self.assertGreater(len(threshold_errors), 0,
                                 f"Should reject {value_str} ({reason})")

    def test_health_check_hysteresis_defaults(self):
        """Test health check hysteresis has correct defaults."""
        reload(config_module)
        cfg = config_module.Config()
        self.assertEqual(cfg.health_check_window, 5,
                        "Default window should be 5")
        self.assertEqual(cfg.health_check_threshold, 3,
                        "Default threshold should be 3")
        self.assertFalse(cfg.asymmetric_hysteresis,
                        "Default asymmetric should be False")

    def test_health_check_window_validation_invalid(self):
        """Test health check window validation rejects out-of-range values."""
        invalid_values = [('2', 'below minimum'), ('11', 'above maximum')]

        for value_str, reason in invalid_values:
            with self.subTest(value=value_str, reason=reason):
                os.environ['HEALTH_CHECK_WINDOW'] = value_str
                for var in ['GCP_PROJECT', 'GOOGLE_APPLICATION_CREDENTIALS']:
                    if var in os.environ:
                        del os.environ[var]

                reload(config_module)
                cfg = config_module.Config()
                errors = config_module.validate_configuration(cfg)

                window_errors = [e for e in errors if 'HEALTH_CHECK_WINDOW' in e]
                self.assertGreater(len(window_errors), 0,
                                 f"Should reject {value_str} ({reason})")

    def test_health_check_threshold_less_than_window_validation(self):
        """Test that health check threshold must be less than window."""
        # Set threshold equal to window (invalid)
        os.environ['HEALTH_CHECK_WINDOW'] = '5'
        os.environ['HEALTH_CHECK_THRESHOLD'] = '5'

        for var in ['GCP_PROJECT', 'GOOGLE_APPLICATION_CREDENTIALS']:
            if var in os.environ:
                del os.environ[var]

        reload(config_module)
        cfg = config_module.Config()
        errors = config_module.validate_configuration(cfg)

        constraint_errors = [e for e in errors if 'must be less than' in e]
        self.assertGreater(len(constraint_errors), 0,
                         "Should reject threshold >= window")

    def test_health_check_threshold_less_than_window_valid(self):
        """Test that valid threshold < window passes validation."""
        os.environ['HEALTH_CHECK_WINDOW'] = '5'
        os.environ['HEALTH_CHECK_THRESHOLD'] = '3'

        for var in ['GCP_PROJECT', 'GOOGLE_APPLICATION_CREDENTIALS']:
            if var in os.environ:
                del os.environ[var]

        reload(config_module)
        cfg = config_module.Config()
        errors = config_module.validate_configuration(cfg)

        constraint_errors = [e for e in errors if 'must be less than' in e]
        self.assertEqual(len(constraint_errors), 0,
                        "Should accept threshold < window")

    def test_min_dwell_time_defaults(self):
        """Test minimum state dwell time has correct default."""
        reload(config_module)
        cfg = config_module.Config()
        self.assertEqual(cfg.min_state_dwell_time, 120,
                        "Default dwell time should be 120s")
        self.assertIn(1, cfg.dwell_time_exception_states,
                     "State 1 should be in exception list")
        self.assertIn(4, cfg.dwell_time_exception_states,
                     "State 4 should be in exception list")

    def test_min_dwell_time_validation_invalid(self):
        """Test dwell time validation rejects out-of-range values."""
        invalid_values = [('20', 'below minimum'), ('700', 'above maximum')]

        for value_str, reason in invalid_values:
            with self.subTest(value=value_str, reason=reason):
                os.environ['MIN_STATE_DWELL_TIME'] = value_str
                for var in ['GCP_PROJECT', 'GOOGLE_APPLICATION_CREDENTIALS']:
                    if var in os.environ:
                        del os.environ[var]

                reload(config_module)
                cfg = config_module.Config()
                errors = config_module.validate_configuration(cfg)

                dwell_errors = [e for e in errors if 'MIN_STATE_DWELL_TIME' in e]
                self.assertGreater(len(dwell_errors), 0,
                                 f"Should reject {value_str} ({reason})")

    def test_min_dwell_time_validation_valid(self):
        """Test dwell time validation accepts valid range."""
        valid_values = [60, 120, 180, 300, 600]

        for value in valid_values:
            with self.subTest(value=value):
                os.environ['MIN_STATE_DWELL_TIME'] = str(value)
                for var in ['GCP_PROJECT', 'GOOGLE_APPLICATION_CREDENTIALS']:
                    if var in os.environ:
                        del os.environ[var]

                reload(config_module)
                cfg = config_module.Config()
                errors = config_module.validate_configuration(cfg)

                dwell_errors = [e for e in errors if 'MIN_STATE_DWELL_TIME' in e]
                self.assertEqual(len(dwell_errors), 0,
                               f"Should accept valid value {value}")


class TestConfigurationTypes(unittest.TestCase):
    """Test configuration value types and conversions."""

    def setUp(self):
        """Save original environment."""
        self.original_env = os.environ.copy()

    def tearDown(self):
        """Restore original environment."""
        os.environ.clear()
        os.environ.update(self.original_env)

    def test_state_4_threshold_is_integer(self):
        """Test State 4 threshold is converted to integer."""
        os.environ['STATE_4_VERIFICATION_THRESHOLD'] = '5'

        reload(config_module)
        cfg = config_module.Config()

        self.assertIsInstance(cfg.state_4_verification_threshold, int,
                            "Threshold should be integer type")
        self.assertEqual(cfg.state_4_verification_threshold, 5)

    def test_retry_configs_are_integers(self):
        """Test all retry configurations are integers."""
        os.environ['MAX_RETRIES_HEALTH_CHECK'] = '8'
        os.environ['MAX_RETRIES_BGP_UPDATE'] = '2'

        reload(config_module)
        cfg = config_module.Config()

        self.assertIsInstance(cfg.max_retries_health_check, int)
        self.assertIsInstance(cfg.max_retries_bgp_check, int)
        self.assertIsInstance(cfg.max_retries_bgp_update, int)
        self.assertIsInstance(cfg.max_retries_cloudflare, int)


if __name__ == '__main__':
    unittest.main()
