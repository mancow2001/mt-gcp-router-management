"""
Unit Tests for GCP Integration Module

This test module comprehensively validates the GCP API integration functions
including client initialization, connectivity validation, backend health monitoring,
BGP session checking, and BGP route advertisement management.

Test Coverage:
    - Client initialization and credential validation
    - GCP connectivity validation
    - Backend service health checks (all scenarios)
    - BGP session health monitoring
    - BGP route advertisement updates
    - HTTP error handling (permanent and transient)
    - Incomplete API responses
    - Structured logging integration
    - Closure pattern validation

Author: Nathan Bray
Created: 2025-11-01
"""

import unittest
from unittest.mock import Mock, MagicMock, patch, call
import os
from googleapiclient.errors import HttpError
from google.auth.exceptions import RefreshError

# Import GCP integration functions
try:
    from .gcp import (
        build_compute_client,
        validate_gcp_connectivity,
        backend_services_healthy,
        router_bgp_sessions_healthy,
        update_bgp_advertisement,
        HEALTHY_STATE,
        PERMANENT_HTTP_ERRORS,
        TRANSIENT_HTTP_ERRORS
    )
except ImportError:
    from gcp import (
        build_compute_client,
        validate_gcp_connectivity,
        backend_services_healthy,
        router_bgp_sessions_healthy,
        update_bgp_advertisement,
        HEALTHY_STATE,
        PERMANENT_HTTP_ERRORS,
        TRANSIENT_HTTP_ERRORS
    )


class TestBuildComputeClient(unittest.TestCase):
    """Test suite for build_compute_client function."""

    def test_build_client_success(self):
        """Test successful client initialization."""
        # Import the module to get access to it
        from gcp_route_mgmt_daemon import gcp as gcp_module

        with patch.object(gcp_module, 'build') as mock_build, \
             patch.object(gcp_module.service_account.Credentials, 'from_service_account_file') as mock_creds, \
             patch.object(gcp_module.os.path, 'exists') as mock_exists, \
             patch.object(gcp_module.os, 'access') as mock_access:

            mock_exists.return_value = True
            mock_access.return_value = True
            mock_creds.return_value = Mock()
            mock_build.return_value = Mock(name='mock_compute_client')

            # Call the function from the module directly, not the imported reference
            result = gcp_module.build_compute_client('/path/to/credentials.json')

            self.assertIsNotNone(result)
            mock_exists.assert_called_once_with('/path/to/credentials.json')
            mock_access.assert_called_once()
            mock_creds.assert_called_once_with('/path/to/credentials.json')
            mock_build.assert_called_once()

    @patch('gcp_route_mgmt_daemon.gcp.os.path.exists')
    def test_build_client_file_not_found(self, mock_exists):
        """Test that FileNotFoundError is raised when credentials file doesn't exist."""
        mock_exists.return_value = False

        with self.assertRaises(FileNotFoundError) as context:
            build_compute_client('/nonexistent/credentials.json')

        self.assertIn('not found', str(context.exception))

    @patch('gcp_route_mgmt_daemon.gcp.os.path.exists')
    @patch('gcp_route_mgmt_daemon.gcp.os.access')
    def test_build_client_file_not_readable(self, mock_access, mock_exists):
        """Test that PermissionError is raised when credentials file is not readable."""
        mock_exists.return_value = True
        mock_access.return_value = False

        with self.assertRaises(PermissionError) as context:
            build_compute_client('/unreadable/credentials.json')

        self.assertIn('not readable', str(context.exception))

    @patch('gcp_route_mgmt_daemon.gcp.build')
    @patch('gcp_route_mgmt_daemon.gcp.service_account.Credentials.from_service_account_file')
    @patch('gcp_route_mgmt_daemon.gcp.os.path.exists')
    @patch('gcp_route_mgmt_daemon.gcp.os.access')
    def test_build_client_invalid_credentials(self, mock_access, mock_exists, mock_creds, mock_build):
        """Test error handling for invalid credentials file."""
        mock_exists.return_value = True
        mock_access.return_value = True
        mock_creds.side_effect = ValueError("Invalid JSON")

        with self.assertRaises(ValueError):
            build_compute_client('/path/to/invalid.json')


class TestValidateGCPConnectivity(unittest.TestCase):
    """Test suite for validate_gcp_connectivity function."""

    def test_validate_empty_project(self):
        """Test that empty project ID raises ValueError."""
        mock_compute = Mock()
        with self.assertRaises(ValueError) as context:
            validate_gcp_connectivity('', ['us-central1'], mock_compute)
        self.assertIn('Project ID cannot be empty', str(context.exception))

    def test_validate_empty_regions(self):
        """Test that empty regions list raises ValueError."""
        mock_compute = Mock()
        with self.assertRaises(ValueError) as context:
            validate_gcp_connectivity('my-project', [], mock_compute)
        self.assertIn('Regions list cannot be empty', str(context.exception))

    def test_validate_success(self):
        """Test successful connectivity validation."""
        mock_compute = Mock()

        # Mock project get response
        mock_compute.projects().get().execute.return_value = {
            'name': 'Test Project'
        }

        # Mock region get responses
        mock_compute.regions().get().execute.return_value = {
            'name': 'us-central1'
        }

        # Should not raise any exceptions
        try:
            validate_gcp_connectivity('my-project', ['us-central1'], mock_compute)
        except Exception as e:
            self.fail(f"validate_gcp_connectivity raised unexpected exception: {e}")

    def test_validate_project_not_found(self):
        """Test handling of 404 error for non-existent project."""
        mock_compute = Mock()
        mock_response = Mock()
        mock_response.status = 404

        mock_compute.projects().get().execute.side_effect = HttpError(
            resp=mock_response, content=b'Not Found'
        )

        with self.assertRaises(HttpError):
            validate_gcp_connectivity('nonexistent-project', ['us-central1'], mock_compute)

    def test_validate_insufficient_permissions(self):
        """Test handling of 403 error for insufficient permissions."""
        mock_compute = Mock()
        mock_response = Mock()
        mock_response.status = 403

        mock_compute.projects().get().execute.side_effect = HttpError(
            resp=mock_response, content=b'Forbidden'
        )

        with self.assertRaises(HttpError):
            validate_gcp_connectivity('my-project', ['us-central1'], mock_compute)

    def test_validate_invalid_region(self):
        """Test handling of invalid region name."""
        mock_compute = Mock()

        # Project validation succeeds
        mock_compute.projects().get().execute.return_value = {
            'name': 'Test Project'
        }

        # Region validation fails with 404
        mock_response = Mock()
        mock_response.status = 404
        mock_compute.regions().get().execute.side_effect = HttpError(
            resp=mock_response, content=b'Not Found'
        )

        with self.assertRaises(ValueError) as context:
            validate_gcp_connectivity('my-project', ['invalid-region'], mock_compute)

        self.assertIn('not found', str(context.exception))

    def test_validate_multiple_regions_success(self):
        """Test validation of multiple regions."""
        mock_compute = Mock()

        mock_compute.projects().get().execute.return_value = {'name': 'Test Project'}
        mock_compute.regions().get().execute.return_value = {'name': 'region'}

        try:
            validate_gcp_connectivity('my-project',
                                     ['us-central1', 'us-east1', 'europe-west1'],
                                     mock_compute)
        except Exception as e:
            self.fail(f"validate_gcp_connectivity raised unexpected exception: {e}")


class TestBackendServicesHealthy(unittest.TestCase):
    """Test suite for backend_services_healthy function and closure."""

    def test_closure_creation(self):
        """Test that backend_services_healthy returns a callable."""
        mock_compute = Mock()
        health_checker = backend_services_healthy('project', 'us-central1', mock_compute)

        self.assertTrue(callable(health_checker))

    def test_empty_project_raises_error(self):
        """Test that empty project raises ValueError."""
        mock_compute = Mock()
        with self.assertRaises(ValueError) as context:
            backend_services_healthy('', 'us-central1', mock_compute)
        self.assertIn('Project ID cannot be empty', str(context.exception))

    def test_empty_region_raises_error(self):
        """Test that empty region raises ValueError."""
        mock_compute = Mock()
        with self.assertRaises(ValueError) as context:
            backend_services_healthy('project', '', mock_compute)
        self.assertIn('Region cannot be empty', str(context.exception))

    def test_no_backend_services_returns_healthy(self):
        """Test that empty region (no services) is considered healthy."""
        mock_compute = Mock()
        mock_compute.regionBackendServices().list().execute.return_value = {
            'items': []
        }

        health_checker = backend_services_healthy('project', 'us-central1', mock_compute)
        result = health_checker()

        self.assertTrue(result)

    def test_all_backends_healthy(self):
        """Test health check with all backends healthy."""
        mock_compute = Mock()

        # Mock backend services list
        mock_compute.regionBackendServices().list().execute.return_value = {
            'items': [{
                'name': 'test-service',
                'backends': [{'group': 'backend-group-1'}]
            }]
        }

        # Mock healthy backend response
        mock_compute.regionBackendServices().getHealth().execute.return_value = {
            'kind': 'compute#backendServiceGroupHealth',
            'healthStatus': [{
                'instance': 'instance-1',
                'healthState': 'HEALTHY'
            }]
        }

        health_checker = backend_services_healthy('project', 'us-central1', mock_compute)
        result = health_checker()

        self.assertTrue(result)

    def test_some_backends_unhealthy(self):
        """Test health check with some backends unhealthy."""
        mock_compute = Mock()

        mock_compute.regionBackendServices().list().execute.return_value = {
            'items': [{
                'name': 'test-service',
                'backends': [{'group': 'backend-group-1'}]
            }]
        }

        # Mock unhealthy backend response
        mock_compute.regionBackendServices().getHealth().execute.return_value = {
            'kind': 'compute#backendServiceGroupHealth',
            'healthStatus': [{
                'instance': 'instance-1',
                'healthState': 'UNHEALTHY'
            }]
        }

        health_checker = backend_services_healthy('project', 'us-central1', mock_compute)
        result = health_checker()

        self.assertFalse(result)

    def test_mixed_health_states(self):
        """Test with mixed healthy and unhealthy backends."""
        mock_compute = Mock()

        mock_compute.regionBackendServices().list().execute.return_value = {
            'items': [{
                'name': 'test-service',
                'backends': [{'group': 'backend-group-1'}]
            }]
        }

        # Multiple instances with mixed states
        mock_compute.regionBackendServices().getHealth().execute.return_value = {
            'kind': 'compute#backendServiceGroupHealth',
            'healthStatus': [
                {'instance': 'instance-1', 'healthState': 'HEALTHY'},
                {'instance': 'instance-2', 'healthState': 'UNHEALTHY'}
            ]
        }

        health_checker = backend_services_healthy('project', 'us-central1', mock_compute)
        result = health_checker()

        self.assertFalse(result)  # Any unhealthy = overall unhealthy

    def test_incomplete_health_response(self):
        """Test handling of incomplete health response."""
        mock_compute = Mock()

        mock_compute.regionBackendServices().list().execute.return_value = {
            'items': [{
                'name': 'test-service',
                'backends': [{'group': 'backend-group-1'}]
            }]
        }

        # Incomplete response (only kind field)
        mock_compute.regionBackendServices().getHealth().execute.return_value = {
            'kind': 'compute#backendServiceGroupHealth'
        }

        health_checker = backend_services_healthy('project', 'us-central1', mock_compute)
        result = health_checker()

        self.assertFalse(result)

    def test_no_health_status_in_response(self):
        """Test handling when healthStatus field is missing."""
        mock_compute = Mock()

        mock_compute.regionBackendServices().list().execute.return_value = {
            'items': [{
                'name': 'test-service',
                'backends': [{'group': 'backend-group-1'}]
            }]
        }

        # Response without healthStatus
        mock_compute.regionBackendServices().getHealth().execute.return_value = {
            'kind': 'compute#backendServiceGroupHealth',
            'healthStatus': []
        }

        health_checker = backend_services_healthy('project', 'us-central1', mock_compute)
        result = health_checker()

        self.assertFalse(result)

    def test_http_error_permanent(self):
        """Test that permanent HTTP errors (403, 404) are re-raised."""
        mock_compute = Mock()
        mock_response = Mock()
        mock_response.status = 403

        mock_compute.regionBackendServices().list().execute.side_effect = HttpError(
            resp=mock_response, content=b'Forbidden'
        )

        health_checker = backend_services_healthy('project', 'us-central1', mock_compute)

        with self.assertRaises(HttpError):
            health_checker()

    def test_http_error_transient(self):
        """Test that transient HTTP errors (429, 5xx) return None."""
        mock_compute = Mock()
        mock_response = Mock()
        mock_response.status = 429

        mock_compute.regionBackendServices().list().execute.side_effect = HttpError(
            resp=mock_response, content=b'Rate Limited'
        )

        health_checker = backend_services_healthy('project', 'us-central1', mock_compute)
        result = health_checker()

        # Should return None (monitoring unavailable) instead of False
        self.assertIsNone(result, "Known transient errors should return None")

    def test_multiple_services_all_healthy(self):
        """Test with multiple backend services, all healthy."""
        mock_compute = Mock()

        mock_compute.regionBackendServices().list().execute.return_value = {
            'items': [
                {'name': 'service-1', 'backends': [{'group': 'backend-1'}]},
                {'name': 'service-2', 'backends': [{'group': 'backend-2'}]}
            ]
        }

        mock_compute.regionBackendServices().getHealth().execute.return_value = {
            'kind': 'compute#backendServiceGroupHealth',
            'healthStatus': [{'instance': 'inst-1', 'healthState': 'HEALTHY'}]
        }

        health_checker = backend_services_healthy('project', 'us-central1', mock_compute)
        result = health_checker()

        self.assertTrue(result)

    def test_structured_logging_integration(self):
        """Test that structured logging is called when provided."""
        mock_compute = Mock()
        mock_logger = Mock()

        mock_compute.regionBackendServices().list().execute.return_value = {'items': []}

        health_checker = backend_services_healthy('project', 'us-central1',
                                                 mock_compute, mock_logger)
        result = health_checker()

        # Verify structured logger was called
        mock_logger.log_health_check.assert_called_once()
        call_args = mock_logger.log_health_check.call_args
        self.assertEqual(call_args[1]['region'], 'us-central1')
        self.assertEqual(call_args[1]['service_type'], 'backend_services')


class TestRouterBGPSessionsHealthy(unittest.TestCase):
    """Test suite for router_bgp_sessions_healthy function and closure."""

    def test_closure_creation(self):
        """Test that router_bgp_sessions_healthy returns a callable."""
        mock_compute = Mock()
        bgp_checker = router_bgp_sessions_healthy('project', 'us-central1', 'router1', mock_compute)

        self.assertTrue(callable(bgp_checker))

    def test_empty_parameters_raise_errors(self):
        """Test that empty parameters raise ValueError."""
        mock_compute = Mock()

        with self.assertRaises(ValueError):
            router_bgp_sessions_healthy('', 'region', 'router', mock_compute)

        with self.assertRaises(ValueError):
            router_bgp_sessions_healthy('project', '', 'router', mock_compute)

        with self.assertRaises(ValueError):
            router_bgp_sessions_healthy('project', 'region', '', mock_compute)

    def test_all_bgp_peers_up(self):
        """Test with all BGP peers in UP state."""
        mock_compute = Mock()

        mock_compute.routers().getRouterStatus().execute.return_value = {
            'result': {
                'bgpPeerStatus': [
                    {'name': 'peer1', 'status': 'UP'},
                    {'name': 'peer2', 'status': 'UP'}
                ]
            }
        }

        bgp_checker = router_bgp_sessions_healthy('project', 'us-central1', 'router1', mock_compute)
        any_up, peer_statuses = bgp_checker()

        self.assertTrue(any_up)
        self.assertEqual(len(peer_statuses), 2)
        self.assertEqual(peer_statuses['peer1'], 'UP')
        self.assertEqual(peer_statuses['peer2'], 'UP')

    def test_some_bgp_peers_down(self):
        """Test with some BGP peers DOWN."""
        mock_compute = Mock()

        mock_compute.routers().getRouterStatus().execute.return_value = {
            'result': {
                'bgpPeerStatus': [
                    {'name': 'peer1', 'status': 'UP'},
                    {'name': 'peer2', 'status': 'DOWN'}
                ]
            }
        }

        bgp_checker = router_bgp_sessions_healthy('project', 'us-central1', 'router1', mock_compute)
        any_up, peer_statuses = bgp_checker()

        self.assertTrue(any_up)  # At least one UP
        self.assertEqual(peer_statuses['peer1'], 'UP')
        self.assertEqual(peer_statuses['peer2'], 'DOWN')

    def test_all_bgp_peers_down(self):
        """Test with all BGP peers DOWN."""
        mock_compute = Mock()

        mock_compute.routers().getRouterStatus().execute.return_value = {
            'result': {
                'bgpPeerStatus': [
                    {'name': 'peer1', 'status': 'DOWN'},
                    {'name': 'peer2', 'status': 'DOWN'}
                ]
            }
        }

        bgp_checker = router_bgp_sessions_healthy('project', 'us-central1', 'router1', mock_compute)
        any_up, peer_statuses = bgp_checker()

        self.assertFalse(any_up)
        self.assertEqual(len(peer_statuses), 2)

    def test_no_bgp_peers_configured(self):
        """Test router with no BGP peers configured."""
        mock_compute = Mock()

        mock_compute.routers().getRouterStatus().execute.return_value = {
            'result': {
                'bgpPeerStatus': []
            }
        }

        bgp_checker = router_bgp_sessions_healthy('project', 'us-central1', 'router1', mock_compute)
        any_up, peer_statuses = bgp_checker()

        self.assertFalse(any_up)
        self.assertEqual(len(peer_statuses), 0)

    def test_missing_bgp_peer_status_field(self):
        """Test handling when bgpPeerStatus field is missing."""
        mock_compute = Mock()

        mock_compute.routers().getRouterStatus().execute.return_value = {
            'result': {}
        }

        bgp_checker = router_bgp_sessions_healthy('project', 'us-central1', 'router1', mock_compute)
        any_up, peer_statuses = bgp_checker()

        self.assertFalse(any_up)
        self.assertEqual(len(peer_statuses), 0)

    def test_http_error_404_raises(self):
        """Test that 404 error (router not found) is re-raised."""
        mock_compute = Mock()
        mock_response = Mock()
        mock_response.status = 404

        mock_compute.routers().getRouterStatus().execute.side_effect = HttpError(
            resp=mock_response, content=b'Not Found'
        )

        bgp_checker = router_bgp_sessions_healthy('project', 'us-central1', 'router1', mock_compute)

        with self.assertRaises(HttpError):
            bgp_checker()

    def test_http_error_transient_returns_none(self):
        """Test that known transient errors return (None, {})."""
        mock_compute = Mock()
        mock_response = Mock()
        mock_response.status = 503

        mock_compute.routers().getRouterStatus().execute.side_effect = HttpError(
            resp=mock_response, content=b'Service Unavailable'
        )

        bgp_checker = router_bgp_sessions_healthy('project', 'us-central1', 'router1', mock_compute)
        any_up, peer_statuses = bgp_checker()

        # Should return None (monitoring unavailable) instead of False
        self.assertIsNone(any_up, "Known transient errors should return None")
        self.assertEqual(peer_statuses, {})

    def test_http_error_unknown_code_returns_none(self):
        """Test that unknown HTTP error codes return (None, {})."""
        # Test with an unusual error code like 432
        mock_compute = Mock()
        mock_response = Mock()
        mock_response.status = 432

        error_message = b'Unknown error from GCP'
        mock_compute.routers().getRouterStatus().execute.side_effect = HttpError(
            resp=mock_response, content=error_message
        )

        bgp_checker = router_bgp_sessions_healthy('project', 'us-central1', 'router1', mock_compute)
        any_up, peer_statuses = bgp_checker()

        # Should return None for unknown error codes
        self.assertIsNone(any_up, "Unknown error codes should return None (unknown health)")
        self.assertEqual(peer_statuses, {})

    def test_http_error_all_known_transient_codes(self):
        """Test that all known transient error codes return None."""
        # Test all known transient error codes: 429, 500, 502, 503, 504
        transient_codes = [429, 500, 502, 503, 504]

        for status_code in transient_codes:
            with self.subTest(status_code=status_code):
                mock_compute = Mock()
                mock_response = Mock()
                mock_response.status = status_code

                mock_compute.routers().getRouterStatus().execute.side_effect = HttpError(
                    resp=mock_response, content=b'Error'
                )

                bgp_checker = router_bgp_sessions_healthy('project', 'us-central1',
                                                         'router1', mock_compute)
                any_up, peer_statuses = bgp_checker()

                self.assertIsNone(any_up,
                                f"Transient error {status_code} should return None")
                self.assertEqual(peer_statuses, {})

    def test_structured_logging_called(self):
        """Test structured logging integration."""
        mock_compute = Mock()
        mock_logger = Mock()

        mock_compute.routers().getRouterStatus().execute.return_value = {
            'result': {
                'bgpPeerStatus': [{'name': 'peer1', 'status': 'UP'}]
            }
        }

        bgp_checker = router_bgp_sessions_healthy('project', 'us-central1',
                                                 'router1', mock_compute, mock_logger)
        bgp_checker()

        mock_logger.log_health_check.assert_called_once()
        call_args = mock_logger.log_health_check.call_args
        self.assertEqual(call_args[1]['service_type'], 'bgp_sessions')

    def test_peer_statuses_returned(self):
        """Test that peer status dictionary is correctly populated."""
        mock_compute = Mock()

        mock_compute.routers().getRouterStatus().execute.return_value = {
            'result': {
                'bgpPeerStatus': [
                    {'name': 'peer-us-east', 'status': 'UP'},
                    {'name': 'peer-us-west', 'status': 'DOWN'},
                    {'name': 'peer-europe', 'status': 'IDLE'}
                ]
            }
        }

        bgp_checker = router_bgp_sessions_healthy('project', 'us-central1', 'router1', mock_compute)
        any_up, peer_statuses = bgp_checker()

        self.assertTrue(any_up)
        self.assertEqual(peer_statuses['peer-us-east'], 'UP')
        self.assertEqual(peer_statuses['peer-us-west'], 'DOWN')
        self.assertEqual(peer_statuses['peer-europe'], 'IDLE')


class TestUpdateBGPAdvertisement(unittest.TestCase):
    """Test suite for update_bgp_advertisement function and closure."""

    def test_closure_creation(self):
        """Test that update_bgp_advertisement returns a callable."""
        mock_compute = Mock()
        advertiser = update_bgp_advertisement('project', 'region', 'router',
                                             '10.0.0.0/24', mock_compute)

        self.assertTrue(callable(advertiser))

    def test_empty_parameters_raise_errors(self):
        """Test that empty parameters raise ValueError."""
        mock_compute = Mock()

        with self.assertRaises(ValueError):
            update_bgp_advertisement('', 'region', 'router', '10.0.0.0/24', mock_compute)

        with self.assertRaises(ValueError):
            update_bgp_advertisement('project', '', 'router', '10.0.0.0/24', mock_compute)

        with self.assertRaises(ValueError):
            update_bgp_advertisement('project', 'region', '', '10.0.0.0/24', mock_compute)

        with self.assertRaises(ValueError):
            update_bgp_advertisement('project', 'region', 'router', '', mock_compute)

    def test_invalid_prefix_format(self):
        """Test that invalid prefix format raises ValueError."""
        mock_compute = Mock()

        with self.assertRaises(ValueError) as context:
            update_bgp_advertisement('project', 'region', 'router', '10.0.0.0', mock_compute)

        self.assertIn('CIDR format', str(context.exception))

    def test_advertise_parameter_default(self):
        """Test that advertise parameter defaults to True."""
        mock_compute = Mock()

        # This just tests the closure is created without error
        advertiser = update_bgp_advertisement('project', 'region', 'router',
                                             '10.0.0.0/24', mock_compute, advertise=True)
        self.assertTrue(callable(advertiser))

        advertiser = update_bgp_advertisement('project', 'region', 'router',
                                             '10.0.0.0/24', mock_compute, advertise=False)
        self.assertTrue(callable(advertiser))

    def test_advertise_none_returns_true_no_op(self):
        """Test that advertise=None returns True without making any API calls (State 0 behavior)."""
        mock_compute = Mock()

        # Create advertiser with advertise=None (State 0 failsafe)
        advertiser = update_bgp_advertisement('project', 'region', 'router',
                                             '10.0.0.0/24', mock_compute, advertise=None)

        # Execute the function
        result = advertiser()

        # Should return True (no-op is success)
        self.assertTrue(result, "advertise=None should return True (no-op)")

        # Should NOT make any API calls to GCP
        mock_compute.routers().get.assert_not_called()
        mock_compute.routers().patch.assert_not_called()


class TestEdgeCases(unittest.TestCase):
    """Test suite for edge cases and error scenarios."""

    def test_backend_health_with_draining_state(self):
        """Test that DRAINING state is considered unhealthy."""
        mock_compute = Mock()

        mock_compute.regionBackendServices().list().execute.return_value = {
            'items': [{
                'name': 'test-service',
                'backends': [{'group': 'backend-group-1'}]
            }]
        }

        mock_compute.regionBackendServices().getHealth().execute.return_value = {
            'kind': 'compute#backendServiceGroupHealth',
            'healthStatus': [{'instance': 'inst-1', 'healthState': 'DRAINING'}]
        }

        health_checker = backend_services_healthy('project', 'us-central1', mock_compute)
        result = health_checker()

        self.assertFalse(result)

    def test_backend_health_with_timeout_state(self):
        """Test that TIMEOUT state is considered unhealthy."""
        mock_compute = Mock()

        mock_compute.regionBackendServices().list().execute.return_value = {
            'items': [{
                'name': 'test-service',
                'backends': [{'group': 'backend-group-1'}]
            }]
        }

        mock_compute.regionBackendServices().getHealth().execute.return_value = {
            'kind': 'compute#backendServiceGroupHealth',
            'healthStatus': [{'instance': 'inst-1', 'healthState': 'TIMEOUT'}]
        }

        health_checker = backend_services_healthy('project', 'us-central1', mock_compute)
        result = health_checker()

        self.assertFalse(result)

    def test_backend_health_with_unknown_state(self):
        """Test that UNKNOWN state is considered unhealthy."""
        mock_compute = Mock()

        mock_compute.regionBackendServices().list().execute.return_value = {
            'items': [{
                'name': 'test-service',
                'backends': [{'group': 'backend-group-1'}]
            }]
        }

        mock_compute.regionBackendServices().getHealth().execute.return_value = {
            'kind': 'compute#backendServiceGroupHealth',
            'healthStatus': [{'instance': 'inst-1', 'healthState': 'UNKNOWN'}]
        }

        health_checker = backend_services_healthy('project', 'us-central1', mock_compute)
        result = health_checker()

        self.assertFalse(result)

    def test_bgp_peer_with_various_states(self):
        """Test BGP peers with various connection states."""
        mock_compute = Mock()

        # Test various BGP states
        for state in ['IDLE', 'CONNECT', 'ACTIVE', 'OPENSENT', 'OPENCONFIRM']:
            mock_compute.routers().getRouterStatus().execute.return_value = {
                'result': {
                    'bgpPeerStatus': [{'name': 'peer1', 'status': state}]
                }
            }

            bgp_checker = router_bgp_sessions_healthy('project', 'us-central1',
                                                     'router1', mock_compute)
            any_up, peer_statuses = bgp_checker()

            # Only UP state should return True
            self.assertFalse(any_up)
            self.assertEqual(peer_statuses['peer1'], state)

    def test_unexpected_exception_in_backend_health(self):
        """Test handling of unexpected exceptions."""
        mock_compute = Mock()

        mock_compute.regionBackendServices().list().execute.side_effect = Exception("Unexpected error")

        health_checker = backend_services_healthy('project', 'us-central1', mock_compute)
        result = health_checker()

        # Should return False on unexpected errors
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
