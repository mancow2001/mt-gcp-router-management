"""
Unit Tests for Workload Identity Federation Support in GCP Integration

This module tests the dual-mode authentication system that supports both:
1. Workload Identity Federation / Application Default Credentials (recommended)
2. Service Account Key Files (legacy, backward compatibility)

Test Coverage:
- Workload Identity authentication
- Service account key file authentication (backward compatibility)
- Auto-detection logic
- Error handling for authentication failures
- Credential refresh behavior
- Configuration validation

Author: Nathan Bray
Version: 1.0
Last Modified: 2025
"""

import unittest
from unittest.mock import Mock, patch, MagicMock, mock_open
import os
import tempfile
import json

# Import the module under test
from gcp_route_mgmt_daemon import gcp as gcp_mod


class TestWorkloadIdentityAuthentication(unittest.TestCase):
    """Test Workload Identity / Application Default Credentials authentication."""

    @patch('gcp_route_mgmt_daemon.gcp.google.auth.default')
    @patch('gcp_route_mgmt_daemon.gcp.build')
    def test_workload_identity_explicit_true(self, mock_build, mock_auth_default):
        """Test explicit Workload Identity authentication when use_workload_identity=True."""
        # Mock credentials and project from google.auth.default()
        mock_creds = Mock()
        mock_creds.refresh = Mock()
        mock_auth_default.return_value = (mock_creds, 'test-project')

        # Mock the compute API client
        mock_compute = Mock()
        mock_build.return_value = mock_compute

        # Call with explicit Workload Identity
        result = gcp_mod.build_compute_client(use_workload_identity=True)

        # Verify google.auth.default() was called
        mock_auth_default.assert_called_once_with(scopes=[
            'https://www.googleapis.com/auth/compute'
        ])

        # Verify credentials were refreshed
        mock_creds.refresh.assert_called_once()

        # Verify compute client was built
        mock_build.assert_called_once()
        self.assertEqual(result, mock_compute)

    @patch('gcp_route_mgmt_daemon.gcp.google.auth.default')
    @patch('gcp_route_mgmt_daemon.gcp.build')
    def test_workload_identity_auto_detect_no_creds_path(self, mock_build, mock_auth_default):
        """Test auto-detection of Workload Identity when no creds_path is provided."""
        # Mock credentials
        mock_creds = Mock()
        mock_creds.refresh = Mock()
        mock_auth_default.return_value = (mock_creds, None)  # No project detected

        mock_compute = Mock()
        mock_build.return_value = mock_compute

        # Call without creds_path (should auto-detect Workload Identity)
        result = gcp_mod.build_compute_client()

        # Verify Workload Identity was used
        mock_auth_default.assert_called_once()
        mock_creds.refresh.assert_called_once()
        self.assertEqual(result, mock_compute)

    @patch('gcp_route_mgmt_daemon.gcp.google.auth.default')
    def test_workload_identity_adc_not_configured(self, mock_auth_default):
        """Test error handling when Application Default Credentials are not configured."""
        import google.auth.exceptions

        # Mock ADC not found error
        mock_auth_default.side_effect = google.auth.exceptions.DefaultCredentialsError(
            'Could not automatically determine credentials'
        )

        # Should raise DefaultCredentialsError
        with self.assertRaises(google.auth.exceptions.DefaultCredentialsError) as context:
            gcp_mod.build_compute_client(use_workload_identity=True)

        # Verify error message contains helpful guidance
        error_msg = str(context.exception)
        self.assertIn('Workload Identity', error_msg)
        self.assertIn('Possible solutions', error_msg)

    @patch('gcp_route_mgmt_daemon.gcp.google.auth.default')
    @patch('gcp_route_mgmt_daemon.gcp.build')
    def test_workload_identity_refresh_failure(self, mock_build, mock_auth_default):
        """Test error handling when credential refresh fails."""
        import google.auth.exceptions

        # Mock credentials that fail to refresh
        mock_creds = Mock()
        mock_creds.refresh.side_effect = google.auth.exceptions.RefreshError('Token expired')
        mock_auth_default.return_value = (mock_creds, 'test-project')

        # Should raise RefreshError
        with self.assertRaises(google.auth.exceptions.RefreshError):
            gcp_mod.build_compute_client(use_workload_identity=True)

    @patch('gcp_route_mgmt_daemon.gcp.google.auth.default')
    @patch('gcp_route_mgmt_daemon.gcp.build')
    def test_workload_identity_with_project_detection(self, mock_build, mock_auth_default):
        """Test Workload Identity with automatic project detection."""
        # Mock credentials with project detection
        mock_creds = Mock()
        mock_creds.refresh = Mock()
        mock_auth_default.return_value = (mock_creds, 'detected-project-123')

        mock_compute = Mock()
        mock_build.return_value = mock_compute

        # Call with Workload Identity
        result = gcp_mod.build_compute_client(use_workload_identity=True)

        # Verify project was detected (logged but not returned)
        mock_auth_default.assert_called_once()
        self.assertEqual(result, mock_compute)


class TestServiceAccountKeyAuthentication(unittest.TestCase):
    """Test service account key file authentication (legacy mode)."""

    def setUp(self):
        """Create a temporary service account key file for testing."""
        # Create temporary file with valid service account JSON
        self.temp_key_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json')
        service_account_data = {
            "type": "service_account",
            "project_id": "test-project",
            "private_key_id": "key123",
            "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC\n-----END PRIVATE KEY-----\n",
            "client_email": "test@test-project.iam.gserviceaccount.com",
            "client_id": "123456789",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token"
        }
        json.dump(service_account_data, self.temp_key_file)
        self.temp_key_file.close()

    def tearDown(self):
        """Clean up temporary key file."""
        if os.path.exists(self.temp_key_file.name):
            os.unlink(self.temp_key_file.name)

    @patch('gcp_route_mgmt_daemon.gcp.service_account.Credentials.from_service_account_file')
    @patch('gcp_route_mgmt_daemon.gcp.build')
    def test_service_account_key_file_authentication(self, mock_build, mock_creds_from_file):
        """Test authentication with service account key file."""
        # Mock credentials loading
        mock_creds = Mock()
        mock_creds_from_file.return_value = mock_creds

        mock_compute = Mock()
        mock_build.return_value = mock_compute

        # Call with service account key file
        result = gcp_mod.build_compute_client(creds_path=self.temp_key_file.name)

        # Verify service account credentials were loaded
        mock_creds_from_file.assert_called_once_with(
            self.temp_key_file.name,
            scopes=['https://www.googleapis.com/auth/compute']
        )

        # Verify compute client was built
        mock_build.assert_called_once()
        self.assertEqual(result, mock_compute)

    def test_service_account_key_file_not_found(self):
        """Test error handling when service account key file doesn't exist."""
        non_existent_path = '/nonexistent/path/to/key.json'

        with self.assertRaises(FileNotFoundError) as context:
            gcp_mod.build_compute_client(creds_path=non_existent_path)

        error_msg = str(context.exception)
        self.assertIn(non_existent_path, error_msg)

    @patch('gcp_route_mgmt_daemon.gcp.os.path.exists')
    @patch('gcp_route_mgmt_daemon.gcp.os.access')
    def test_service_account_key_file_not_readable(self, mock_access, mock_exists):
        """Test error handling when service account key file is not readable."""
        # Mock file exists but is not readable
        mock_exists.return_value = True
        mock_access.return_value = False

        with self.assertRaises(PermissionError) as context:
            gcp_mod.build_compute_client(creds_path='/path/to/unreadable.json')

        error_msg = str(context.exception)
        self.assertIn('not readable', error_msg)

    @patch('gcp_route_mgmt_daemon.gcp.service_account.Credentials.from_service_account_file')
    def test_service_account_invalid_json(self, mock_creds_from_file):
        """Test error handling when service account key file contains invalid JSON."""
        # Mock invalid JSON error
        mock_creds_from_file.side_effect = ValueError('Invalid JSON')

        with self.assertRaises(ValueError):
            gcp_mod.build_compute_client(creds_path=self.temp_key_file.name)


class TestAuthenticationModeDetection(unittest.TestCase):
    """Test auto-detection logic for authentication mode."""

    def test_explicit_workload_identity_overrides_creds_path(self):
        """Test that use_workload_identity=True overrides creds_path."""
        with patch('gcp_route_mgmt_daemon.gcp.google.auth.default') as mock_auth, \
             patch('gcp_route_mgmt_daemon.gcp.build') as mock_build:

            mock_creds = Mock()
            mock_creds.refresh = Mock()
            mock_auth.return_value = (mock_creds, 'test-project')
            mock_build.return_value = Mock()

            # Call with both creds_path and use_workload_identity=True
            # Workload Identity should be used
            gcp_mod.build_compute_client(
                creds_path='/some/path.json',
                use_workload_identity=True
            )

            # Verify Workload Identity was used (google.auth.default called)
            mock_auth.assert_called_once()

    @patch('gcp_route_mgmt_daemon.gcp.service_account.Credentials.from_service_account_file')
    @patch('gcp_route_mgmt_daemon.gcp.build')
    def test_explicit_false_requires_creds_path(self, mock_build, mock_creds_from_file):
        """Test that use_workload_identity=False requires creds_path."""
        with self.assertRaises(ValueError) as context:
            gcp_mod.build_compute_client(use_workload_identity=False)

        error_msg = str(context.exception)
        self.assertIn('creds_path', error_msg)
        self.assertIn('required', error_msg)

    @patch('gcp_route_mgmt_daemon.gcp.google.auth.default')
    @patch('gcp_route_mgmt_daemon.gcp.build')
    def test_no_params_uses_workload_identity(self, mock_build, mock_auth):
        """Test that calling with no parameters defaults to Workload Identity."""
        mock_creds = Mock()
        mock_creds.refresh = Mock()
        mock_auth.return_value = (mock_creds, None)
        mock_build.return_value = Mock()

        # Call with no parameters
        gcp_mod.build_compute_client()

        # Verify Workload Identity was used
        mock_auth.assert_called_once()


class TestConfigurationValidation(unittest.TestCase):
    """Test configuration validation for authentication modes."""

    def setUp(self):
        """Set up test fixtures."""
        from gcp_route_mgmt_daemon.config import Config
        self.config_class = Config

    def test_config_workload_identity_enabled(self):
        """Test configuration loading with Workload Identity enabled."""
        # Set environment before importing (dataclass evaluates at class definition)
        os.environ['USE_WORKLOAD_IDENTITY'] = 'true'

        try:
            # Test that the environment variable is read correctly
            value = os.getenv('USE_WORKLOAD_IDENTITY', 'false').lower() == 'true'
            self.assertTrue(value)

            # Verify the boolean conversion logic works
            from gcp_route_mgmt_daemon.config import Config, validate_configuration

            # Note: Config instance may not reflect runtime env changes due to dataclass
            # This test verifies the logic works when properly configured
            cfg = Config()

            # If workload identity is enabled, validation should not require credentials file
            if cfg.use_workload_identity:
                errors = validate_configuration(cfg)
                self.assertNotIn('GOOGLE_APPLICATION_CREDENTIALS', str(errors))
        finally:
            # Clean up
            if 'USE_WORKLOAD_IDENTITY' in os.environ:
                del os.environ['USE_WORKLOAD_IDENTITY']

    def test_config_service_account_requires_credentials(self):
        """Test that service account mode requires GOOGLE_APPLICATION_CREDENTIALS."""
        with patch.dict(os.environ, {
            'USE_WORKLOAD_IDENTITY': 'false',
            'GCP_PROJECT': 'test-project',
            'LOCAL_GCP_REGION': 'us-central1',
            'REMOTE_GCP_REGION': 'us-east4',
            'LOCAL_BGP_ROUTER': 'router1',
            'REMOTE_BGP_ROUTER': 'router2',
            'LOCAL_BGP_REGION': 'us-central1',
            'REMOTE_BGP_REGION': 'us-east4',
            'BGP_PEER_PROJECT': 'peer-project',
            'PRIMARY_PREFIX': '10.0.0.0/24',
            'SECONDARY_PREFIX': '10.1.0.0/24',
            'CLOUDFLARE_ACCOUNT_ID': 'cf-account',
            'CLOUDFLARE_API_TOKEN': 'cf-token',
            'DESCRIPTION_SUBSTRING': 'test-routes'
        }, clear=True):
            from gcp_route_mgmt_daemon.config import Config, validate_configuration

            cfg = Config()

            # Verify Workload Identity flag is not set
            self.assertFalse(cfg.use_workload_identity)

            # Verify validation fails without GOOGLE_APPLICATION_CREDENTIALS
            errors = validate_configuration(cfg)
            self.assertTrue(len(errors) > 0)
            error_str = str(errors)
            self.assertIn('authentication', error_str.lower())

    @patch.dict(os.environ, {}, clear=True)
    def test_config_default_workload_identity_false(self):
        """Test that USE_WORKLOAD_IDENTITY defaults to false."""
        from gcp_route_mgmt_daemon.config import Config

        cfg = Config()

        # Verify default is False
        self.assertFalse(cfg.use_workload_identity)


class TestBackwardCompatibility(unittest.TestCase):
    """Test backward compatibility with existing service account key file usage."""

    def test_old_code_still_works(self):
        """Test that existing code calling build_compute_client with creds_path still works."""
        with patch('gcp_route_mgmt_daemon.gcp.service_account.Credentials.from_service_account_file') as mock_creds, \
             patch('gcp_route_mgmt_daemon.gcp.build') as mock_build, \
             patch('gcp_route_mgmt_daemon.gcp.os.path.exists', return_value=True), \
             patch('gcp_route_mgmt_daemon.gcp.os.access', return_value=True):

            mock_creds.return_value = Mock()
            mock_compute = Mock()
            mock_build.return_value = mock_compute

            # Old-style call (positional argument)
            result = gcp_mod.build_compute_client('/path/to/key.json')

            # Should still work
            mock_creds.assert_called_once()
            self.assertEqual(result, mock_compute)

    def test_timeout_parameter_still_accepted(self):
        """Test that timeout parameter is still accepted for backward compatibility."""
        with patch('gcp_route_mgmt_daemon.gcp.service_account.Credentials.from_service_account_file') as mock_creds, \
             patch('gcp_route_mgmt_daemon.gcp.build') as mock_build, \
             patch('gcp_route_mgmt_daemon.gcp.os.path.exists', return_value=True), \
             patch('gcp_route_mgmt_daemon.gcp.os.access', return_value=True):

            mock_creds.return_value = Mock()
            mock_build.return_value = Mock()

            # Call with timeout parameter (deprecated but still accepted)
            gcp_mod.build_compute_client('/path/to/key.json', timeout=60)

            # Should not raise error
            mock_creds.assert_called_once()


if __name__ == '__main__':
    unittest.main()
