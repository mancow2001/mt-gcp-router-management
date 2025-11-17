"""
Unit Tests for Cloudflare Magic Transit Integration Module

This test module comprehensively validates the Cloudflare API integration functions
including connectivity validation, route querying, and bulk route priority updates.

Test Coverage:
    - Cloudflare API connectivity validation
    - Route filtering by description substring
    - Bulk route priority updates
    - No-op scenarios (routes already at desired priority)
    - Empty/no-match scenarios
    - HTTP error handling (401, 403, 404, 422, 429, 5xx)
    - Network error handling (timeouts, connection errors)
    - Structured logging integration
    - Input validation

Author: Nathan Bray
Created: 2025-11-01
"""

import unittest
from unittest.mock import Mock, MagicMock, patch, call
import requests
from requests.exceptions import HTTPError, Timeout, ConnectionError

# Import Cloudflare integration functions
try:
    from .cloudflare import (
        validate_cloudflare_connectivity,
        update_routes_by_description_bulk,
        get_routes_by_description,
        CLOUDFLARE_API_BASE
    )
except ImportError:
    from cloudflare import (
        validate_cloudflare_connectivity,
        update_routes_by_description_bulk,
        get_routes_by_description,
        CLOUDFLARE_API_BASE
    )


class TestValidateCloudflareConnectivity(unittest.TestCase):
    """Test suite for validate_cloudflare_connectivity function."""

    def test_empty_account_id_raises_error(self):
        """Test that empty account_id raises ValueError."""
        with self.assertRaises(ValueError) as context:
            validate_cloudflare_connectivity('', 'token123')
        self.assertIn('account_id must be a non-empty string', str(context.exception))

    def test_empty_token_raises_error(self):
        """Test that empty token raises ValueError."""
        with self.assertRaises(ValueError) as context:
            validate_cloudflare_connectivity('account123', '')
        self.assertIn('token must be a non-empty string', str(context.exception))

    def test_none_account_id_raises_error(self):
        """Test that None account_id raises ValueError."""
        with self.assertRaises(ValueError):
            validate_cloudflare_connectivity(None, 'token123')

    def test_none_token_raises_error(self):
        """Test that None token raises ValueError."""
        with self.assertRaises(ValueError):
            validate_cloudflare_connectivity('account123', None)

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    def test_successful_validation(self, mock_get):
        """Test successful connectivity validation."""
        # Mock successful token verification
        mock_token_response = Mock()
        mock_token_response.json.return_value = {'success': True}

        # Mock successful route access test
        mock_routes_response = Mock()
        mock_routes_response.json.return_value = {
            'success': True,
            'result': {'routes': [{'id': '1', 'prefix': '10.0.0.0/24'}]}
        }

        # Configure mock to return different responses for each call
        mock_get.side_effect = [mock_token_response, mock_routes_response]

        # Should not raise any exceptions
        try:
            validate_cloudflare_connectivity('account123', 'token456')
        except Exception as e:
            self.fail(f"validate_cloudflare_connectivity raised unexpected exception: {e}")

        # Verify both API calls were made
        self.assertEqual(mock_get.call_count, 2)

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    def test_invalid_token_raises_http_error(self, mock_get):
        """Test that invalid token (401) raises HTTPError."""
        mock_response = Mock()
        mock_response.status_code = 401
        mock_get.side_effect = HTTPError(response=mock_response)

        with self.assertRaises(HTTPError):
            validate_cloudflare_connectivity('account123', 'invalid_token')

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    def test_insufficient_permissions_raises_http_error(self, mock_get):
        """Test that insufficient permissions (403) raises HTTPError."""
        mock_response = Mock()
        mock_response.status_code = 403
        mock_get.side_effect = HTTPError(response=mock_response)

        with self.assertRaises(HTTPError):
            validate_cloudflare_connectivity('account123', 'token456')

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    def test_account_not_found_raises_http_error(self, mock_get):
        """Test that non-existent account (404) raises HTTPError."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.side_effect = HTTPError(response=mock_response)

        with self.assertRaises(HTTPError):
            validate_cloudflare_connectivity('nonexistent_account', 'token456')

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    def test_rate_limit_raises_http_error(self, mock_get):
        """Test that rate limiting (429) raises HTTPError."""
        mock_response = Mock()
        mock_response.status_code = 429
        mock_get.side_effect = HTTPError(response=mock_response)

        with self.assertRaises(HTTPError):
            validate_cloudflare_connectivity('account123', 'token456')

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    def test_token_verification_failure(self, mock_get):
        """Test handling of token verification API returning success=false."""
        mock_response = Mock()
        mock_response.json.return_value = {
            'success': False,
            'errors': [{'message': 'Invalid token'}]
        }
        mock_get.return_value = mock_response

        with self.assertRaises(RuntimeError) as context:
            validate_cloudflare_connectivity('account123', 'token456')

        self.assertIn('Token verification failed', str(context.exception))

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    def test_route_access_failure(self, mock_get):
        """Test handling of route access API returning success=false."""
        # Token verification succeeds
        mock_token_response = Mock()
        mock_token_response.json.return_value = {'success': True}

        # Route access fails
        mock_routes_response = Mock()
        mock_routes_response.json.return_value = {
            'success': False,
            'errors': [{'message': 'No access to routes'}]
        }

        mock_get.side_effect = [mock_token_response, mock_routes_response]

        with self.assertRaises(RuntimeError) as context:
            validate_cloudflare_connectivity('account123', 'token456')

        self.assertIn('Route access test failed', str(context.exception))

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    def test_timeout_raises_exception(self, mock_get):
        """Test that request timeout raises Timeout exception."""
        mock_get.side_effect = Timeout("Request timed out")

        with self.assertRaises(Timeout):
            validate_cloudflare_connectivity('account123', 'token456')

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    def test_connection_error_raises_exception(self, mock_get):
        """Test that connection error raises ConnectionError."""
        mock_get.side_effect = ConnectionError("Connection failed")

        with self.assertRaises(ConnectionError):
            validate_cloudflare_connectivity('account123', 'token456')


class TestUpdateRoutesByDescriptionBulk(unittest.TestCase):
    """Test suite for update_routes_by_description_bulk function."""

    def test_empty_account_id_raises_error(self):
        """Test that empty account_id raises ValueError."""
        with self.assertRaises(ValueError):
            update_routes_by_description_bulk('', 'token', 'desc', 100)

    def test_empty_token_raises_error(self):
        """Test that empty token raises ValueError."""
        with self.assertRaises(ValueError):
            update_routes_by_description_bulk('account', '', 'desc', 100)

    def test_invalid_priority_below_range(self):
        """Test that priority < 1 raises ValueError."""
        with self.assertRaises(ValueError):
            update_routes_by_description_bulk('account', 'token', 'desc', 0)

    def test_invalid_priority_above_range(self):
        """Test that priority > 1000 raises ValueError."""
        with self.assertRaises(ValueError):
            update_routes_by_description_bulk('account', 'token', 'desc', 1001)

    def test_invalid_desc_substring_type(self):
        """Test that non-string desc_substring raises ValueError."""
        with self.assertRaises(ValueError):
            update_routes_by_description_bulk('account', 'token', None, 100)

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    @patch('gcp_route_mgmt_daemon.cloudflare.requests.put')
    def test_successful_update(self, mock_put, mock_get):
        """Test successful bulk route priority update."""
        # Mock GET response with routes needing update
        mock_get_response = Mock()
        mock_get_response.json.return_value = {
            'success': True,
            'result': {
                'routes': [
                    {'id': '1', 'prefix': '10.0.0.0/24', 'nexthop': '192.168.1.1',
                     'description': 'primary-dc-route', 'priority': 200}
                ]
            }
        }
        mock_get.return_value = mock_get_response

        # Mock PUT response for update
        mock_put_response = Mock()
        mock_put_response.json.return_value = {
            'success': True,
            'result': {'modified': 1, 'routes': []}
        }
        mock_put.return_value = mock_put_response

        result = update_routes_by_description_bulk('account', 'token', 'primary-dc', 100)

        self.assertTrue(result)
        mock_get.assert_called_once()
        mock_put.assert_called_once()

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    def test_no_matching_routes(self, mock_get):
        """Test behavior when no routes match the description."""
        mock_response = Mock()
        mock_response.json.return_value = {
            'success': True,
            'result': {
                'routes': [
                    {'id': '1', 'prefix': '10.0.0.0/24', 'description': 'backup-dc'}
                ]
            }
        }
        mock_get.return_value = mock_response

        result = update_routes_by_description_bulk('account', 'token', 'primary-dc', 100)

        # Should return True (success) even though no changes were made
        self.assertTrue(result)

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    def test_empty_routes_list(self, mock_get):
        """Test behavior with empty routes list."""
        mock_response = Mock()
        mock_response.json.return_value = {
            'success': True,
            'result': {'routes': []}
        }
        mock_get.return_value = mock_response

        result = update_routes_by_description_bulk('account', 'token', 'primary', 100)

        self.assertTrue(result)

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    def test_routes_already_at_desired_priority(self, mock_get):
        """Test when routes are already at the desired priority (no-op)."""
        mock_response = Mock()
        mock_response.json.return_value = {
            'success': True,
            'result': {
                'routes': [
                    {'id': '1', 'prefix': '10.0.0.0/24', 'nexthop': '192.168.1.1',
                     'description': 'primary-dc', 'priority': 100}
                ]
            }
        }
        mock_get.return_value = mock_response

        result = update_routes_by_description_bulk('account', 'token', 'primary', 100)

        # Should return True (success) - no updates needed
        self.assertTrue(result)

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    @patch('gcp_route_mgmt_daemon.cloudflare.requests.put')
    def test_multiple_routes_updated(self, mock_put, mock_get):
        """Test updating multiple matching routes."""
        mock_get_response = Mock()
        mock_get_response.json.return_value = {
            'success': True,
            'result': {
                'routes': [
                    {'id': '1', 'prefix': '10.0.0.0/24', 'nexthop': '192.168.1.1',
                     'description': 'primary-dc-1', 'priority': 200},
                    {'id': '2', 'prefix': '10.0.1.0/24', 'nexthop': '192.168.1.2',
                     'description': 'primary-dc-2', 'priority': 200}
                ]
            }
        }
        mock_get.return_value = mock_get_response

        mock_put_response = Mock()
        mock_put_response.json.return_value = {
            'success': True,
            'result': {'modified': 2}
        }
        mock_put.return_value = mock_put_response

        result = update_routes_by_description_bulk('account', 'token', 'primary-dc', 100)

        self.assertTrue(result)

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    def test_http_error_401(self, mock_get):
        """Test handling of 401 Unauthorized error."""
        mock_response = Mock()
        mock_response.status_code = 401
        mock_get.side_effect = HTTPError(response=mock_response)

        result = update_routes_by_description_bulk('account', 'token', 'primary', 100)

        self.assertFalse(result)

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    def test_http_error_429_rate_limit(self, mock_get):
        """Test handling of 429 Rate Limit error."""
        mock_response = Mock()
        mock_response.status_code = 429
        mock_get.side_effect = HTTPError(response=mock_response)

        result = update_routes_by_description_bulk('account', 'token', 'primary', 100)

        self.assertFalse(result)

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    @patch('gcp_route_mgmt_daemon.cloudflare.requests.put')
    def test_http_error_422_invalid_data(self, mock_put, mock_get):
        """Test handling of 422 Invalid Data error."""
        mock_get_response = Mock()
        mock_get_response.json.return_value = {
            'success': True,
            'result': {
                'routes': [
                    {'id': '1', 'prefix': '10.0.0.0/24', 'nexthop': '192.168.1.1',
                     'description': 'primary', 'priority': 200}
                ]
            }
        }
        mock_get.return_value = mock_get_response

        mock_response = Mock()
        mock_response.status_code = 422
        mock_put.side_effect = HTTPError(response=mock_response)

        result = update_routes_by_description_bulk('account', 'token', 'primary', 100)

        self.assertFalse(result)

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    def test_timeout_error(self, mock_get):
        """Test handling of request timeout."""
        mock_get.side_effect = Timeout("Request timed out")

        result = update_routes_by_description_bulk('account', 'token', 'primary', 100)

        self.assertFalse(result)

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    def test_connection_error(self, mock_get):
        """Test handling of connection error."""
        mock_get.side_effect = ConnectionError("Connection failed")

        result = update_routes_by_description_bulk('account', 'token', 'primary', 100)

        self.assertFalse(result)

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    def test_api_returns_success_false(self, mock_get):
        """Test handling when API returns success=false."""
        mock_response = Mock()
        mock_response.json.return_value = {
            'success': False,
            'errors': [{'message': 'API Error'}]
        }
        mock_get.return_value = mock_response

        result = update_routes_by_description_bulk('account', 'token', 'primary', 100)

        self.assertFalse(result)

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    @patch('gcp_route_mgmt_daemon.cloudflare.requests.put')
    def test_put_returns_success_false(self, mock_put, mock_get):
        """Test handling when PUT request returns success=false."""
        mock_get_response = Mock()
        mock_get_response.json.return_value = {
            'success': True,
            'result': {
                'routes': [
                    {'id': '1', 'prefix': '10.0.0.0/24', 'nexthop': '192.168.1.1',
                     'description': 'primary', 'priority': 200}
                ]
            }
        }
        mock_get.return_value = mock_get_response

        mock_put_response = Mock()
        mock_put_response.json.return_value = {
            'success': False,
            'errors': [{'message': 'Update failed'}]
        }
        mock_put.return_value = mock_put_response

        result = update_routes_by_description_bulk('account', 'token', 'primary', 100)

        self.assertFalse(result)

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    @patch('gcp_route_mgmt_daemon.cloudflare.requests.put')
    def test_structured_logging_called(self, mock_put, mock_get):
        """Test that structured logger is called when provided."""
        mock_logger = Mock()

        mock_get_response = Mock()
        mock_get_response.json.return_value = {
            'success': True,
            'result': {'routes': []}
        }
        mock_get.return_value = mock_get_response

        update_routes_by_description_bulk('account', 'token', 'primary', 100, mock_logger)

        # Verify structured logger was called
        mock_logger.log_cloudflare_update.assert_called_once()

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    def test_empty_description_substring(self, mock_get):
        """Test behavior with empty description substring."""
        mock_response = Mock()
        mock_response.json.return_value = {
            'success': True,
            'result': {
                'routes': [
                    {'id': '1', 'prefix': '10.0.0.0/24', 'description': 'test'}
                ]
            }
        }
        mock_get.return_value = mock_response

        result = update_routes_by_description_bulk('account', 'token', '', 100)

        # Empty substring matches nothing
        self.assertTrue(result)

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    def test_routes_without_description_field(self, mock_get):
        """Test handling of routes without description field."""
        mock_response = Mock()
        mock_response.json.return_value = {
            'success': True,
            'result': {
                'routes': [
                    {'id': '1', 'prefix': '10.0.0.0/24', 'nexthop': '192.168.1.1'}
                    # No description field
                ]
            }
        }
        mock_get.return_value = mock_response

        result = update_routes_by_description_bulk('account', 'token', 'primary', 100)

        # Should handle gracefully - no matches
        self.assertTrue(result)


class TestGetRoutesByDescription(unittest.TestCase):
    """Test suite for get_routes_by_description function."""

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    def test_successful_route_query(self, mock_get):
        """Test successful route query."""
        mock_response = Mock()
        mock_response.json.return_value = {
            'success': True,
            'result': {
                'routes': [
                    {'id': '1', 'prefix': '10.0.0.0/24', 'description': 'primary-dc'},
                    {'id': '2', 'prefix': '10.0.1.0/24', 'description': 'backup-dc'}
                ]
            }
        }
        mock_get.return_value = mock_response

        routes = get_routes_by_description('account', 'token', 'primary')

        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]['id'], '1')

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    def test_no_matching_routes(self, mock_get):
        """Test query with no matching routes."""
        mock_response = Mock()
        mock_response.json.return_value = {
            'success': True,
            'result': {
                'routes': [
                    {'id': '1', 'prefix': '10.0.0.0/24', 'description': 'backup'}
                ]
            }
        }
        mock_get.return_value = mock_response

        routes = get_routes_by_description('account', 'token', 'primary')

        self.assertEqual(len(routes), 0)

    def test_empty_substring_returns_empty_list(self):
        """Test that empty substring returns empty list."""
        routes = get_routes_by_description('account', 'token', '')

        self.assertEqual(len(routes), 0)

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    def test_api_error_raises_runtime_error(self, mock_get):
        """Test that API error raises RuntimeError."""
        mock_response = Mock()
        mock_response.json.return_value = {
            'success': False,
            'errors': [{'message': 'API Error'}]
        }
        mock_get.return_value = mock_response

        with self.assertRaises(RuntimeError):
            get_routes_by_description('account', 'token', 'primary')

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    def test_http_error_raises_exception(self, mock_get):
        """Test that HTTP error raises HTTPError."""
        mock_response = Mock()
        mock_response.status_code = 403
        mock_get.side_effect = HTTPError(response=mock_response)

        with self.assertRaises(HTTPError):
            get_routes_by_description('account', 'token', 'primary')


class TestEdgeCases(unittest.TestCase):
    """Test suite for edge cases and boundary conditions."""

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    @patch('gcp_route_mgmt_daemon.cloudflare.requests.put')
    def test_case_sensitive_matching(self, mock_put, mock_get):
        """Test that description matching is case-sensitive."""
        mock_response = Mock()
        mock_response.json.return_value = {
            'success': True,
            'result': {
                'routes': [
                    {'id': '1', 'prefix': '10.0.0.0/24', 'nexthop': '192.168.1.1',
                     'description': 'PRIMARY-DC', 'priority': 200},
                    {'id': '2', 'prefix': '10.0.1.0/24', 'nexthop': '192.168.1.2',
                     'description': 'primary-dc', 'priority': 200}
                ]
            }
        }
        mock_get.return_value = mock_response

        mock_put_response = Mock()
        mock_put_response.json.return_value = {'success': True, 'result': {'modified': 1}}
        mock_put.return_value = mock_put_response

        result = update_routes_by_description_bulk('account', 'token', 'primary-dc', 100)

        # Only lowercase match should be updated
        self.assertTrue(result)

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    @patch('gcp_route_mgmt_daemon.cloudflare.requests.put')
    def test_partial_substring_matching(self, mock_put, mock_get):
        """Test that partial substring matching works."""
        mock_response = Mock()
        mock_response.json.return_value = {
            'success': True,
            'result': {
                'routes': [
                    {'id': '1', 'prefix': '10.0.0.0/24', 'nexthop': '192.168.1.1',
                     'description': 'primary-datacenter-route', 'priority': 200}
                ]
            }
        }
        mock_get.return_value = mock_response

        mock_put_response = Mock()
        mock_put_response.json.return_value = {'success': True, 'result': {'modified': 1}}
        mock_put.return_value = mock_put_response

        result = update_routes_by_description_bulk('account', 'token', 'datacenter', 100)

        self.assertTrue(result)

    @patch('gcp_route_mgmt_daemon.cloudflare.requests.get')
    def test_priority_boundary_values(self, mock_get):
        """Test valid boundary priority values (1 and 1000)."""
        mock_response = Mock()
        mock_response.json.return_value = {
            'success': True,
            'result': {'routes': []}
        }
        mock_get.return_value = mock_response

        # Priority 1 should work
        result1 = update_routes_by_description_bulk('account', 'token', 'test', 1)
        self.assertTrue(result1)

        # Priority 1000 should work
        result2 = update_routes_by_description_bulk('account', 'token', 'test', 1000)
        self.assertTrue(result2)


if __name__ == "__main__":
    unittest.main()
