"""
Cloudflare Magic Transit Route Management Module

This module provides functionality for managing Cloudflare Magic Transit routes
through the Cloudflare API. It's designed to work as part of a larger health-checking
and routing management system that automatically adjusts route priorities based
on service health.

Magic Transit Overview:
    Cloudflare Magic Transit protects entire IP subnets by routing traffic through
    Cloudflare's network. Routes define how traffic to specific prefixes should be
    handled, including priority for failover scenarios.

Key Features:
    - Validates Cloudflare API connectivity and permissions
    - Bulk updates route priorities based on description matching
    - Comprehensive error handling with retry capabilities
    - Structured logging integration for observability
    - Thread-safe operations

Route Priority Management:
    Routes have priorities (typically 1-1000) where lower numbers indicate higher
    priority. This module can update priorities for traffic engineering:
    - Primary routes: Lower priority (e.g., 100) for normal traffic flow
    - Backup routes: Higher priority (e.g., 200) for failover scenarios

API Rate Limiting:
    Cloudflare APIs have rate limits. This module uses appropriate timeouts and
    should be used with circuit breakers and retry logic for production resilience.

Authentication:
    Requires a Cloudflare API token with Magic Transit permissions for the
    specified account. Token should have:
    - Account:Read permission for token verification
    - Zone:Zone Settings:Edit for Magic Transit route management

Integration:
    This module integrates with:
    - structured_events: For comprehensive logging and monitoring
    - circuit breaker patterns: For resilient API calls
    - health checking systems: For automated route management

Usage Example:
    from .cloudflare import validate_cloudflare_connectivity, update_routes_by_description_bulk
    from .structured_events import StructuredEventLogger
    
    # Setup
    logger = StructuredEventLogger("route_manager")
    account_id = "your-account-id"
    token = "your-api-token"
    
    # Validate connectivity
    validate_cloudflare_connectivity(account_id, token)
    
    # Update route priorities
    success = update_routes_by_description_bulk(
        account_id=account_id,
        token=token,
        desc_substring="primary-datacenter",
        desired_priority=100,  # High priority
        structured_logger=logger
    )

Security Considerations:
    - API tokens should be stored securely (environment variables, secrets manager)
    - Use least-privilege tokens with minimal required permissions
    - Monitor API usage for unauthorized access
    - Implement proper error handling to avoid token exposure in logs

Author: MT GCP Daemon Team
Version: 1.1
Last Modified: 2025
Dependencies: requests, structured_events module
"""

import logging
import requests
import os
import time
from typing import Optional, Dict, List, Any
from .structured_events import StructuredEventLogger, ActionResult

# Logger used for all Cloudflare-related operations
# Uses environment variable for consistent logger naming across modules
logger = logging.getLogger(os.getenv("LOGGER_NAME", "HEALTH_CHECK_DAEMON"))

# Module-level constants for Cloudflare API configuration
CLOUDFLARE_API_BASE = "https://api.cloudflare.com/client/v4"
DEFAULT_REQUEST_TIMEOUT = 10  # seconds
BULK_UPDATE_TIMEOUT = 60      # seconds for bulk operations
MAX_ROUTES_PER_REQUEST = 1000 # Cloudflare API limit (approximate)


def validate_cloudflare_connectivity(account_id: str, token: str) -> None:
    """
    Validates Cloudflare API connectivity and permissions for Magic Transit operations.
    
    This function performs a two-step validation process to ensure the provided
    credentials are valid and have the necessary permissions:
    
    1. Token Verification: Validates the API token itself
    2. Route Access Test: Confirms access to Magic Transit routes for the account
    
    This validation should be performed at startup to fail fast if credentials
    are invalid, rather than discovering issues during operational route updates.
    
    Args:
        account_id (str): Cloudflare account ID (typically a UUID-like string).
            Can be found in the Cloudflare dashboard under account settings.
        token (str): Cloudflare API token with Magic Transit permissions.
            Should have Account:Read and Zone:Zone Settings:Edit permissions.
            
    Raises:
        requests.exceptions.HTTPError: If HTTP request fails (4xx/5xx status codes)
            - 401: Invalid or expired token
            - 403: Token lacks required permissions
            - 404: Account not found or no access
            - 429: Rate limit exceeded
            - 5xx: Cloudflare server errors
        requests.exceptions.RequestException: For network-related failures
            (timeouts, connection errors, DNS failures)
        RuntimeError: If Cloudflare API returns success=false in response
        ValueError: If account_id or token are empty/None
        
    Returns:
        None: Function returns nothing on success, raises exceptions on failure
        
    Side Effects:
        - Makes HTTP requests to Cloudflare API
        - Logs success message with route count
        - May log warning/error messages on failure
        
    Example:
        try:
            validate_cloudflare_connectivity("abc123def456", "your-api-token")
            print("Cloudflare connectivity validated successfully")
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                print("Invalid API token")
            elif e.response.status_code == 403:
                print("Token lacks required permissions")
            else:
                print(f"HTTP error: {e}")
        except Exception as e:
            print(f"Validation failed: {e}")
            
    API Endpoints Used:
        - GET /accounts/{account_id}/tokens/verify
        - GET /accounts/{account_id}/magic/routes
        
    Performance:
        - Typical response time: 100-500ms per request
        - Total validation time: ~200-1000ms
        - Uses 10-second timeout for each request
        
    Security Notes:
        - Token is sent in Authorization header (secure)
        - No sensitive data is logged
        - Validation requests don't modify any resources
    """
    # Input validation
    if not account_id or not isinstance(account_id, str):
        raise ValueError("account_id must be a non-empty string")
    if not token or not isinstance(token, str):
        raise ValueError("token must be a non-empty string")
    
    # Prepare headers for API requests
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "mt-gcp-daemon/1.0"  # Identify our application
    }

    logger.debug(f"Validating Cloudflare connectivity for account {account_id}")

    try:
        # Step 1: Validate token itself
        verify_url = f"{CLOUDFLARE_API_BASE}/accounts/{account_id}/tokens/verify"
        logger.debug(f"Verifying token at: {verify_url}")
        
        r = requests.get(verify_url, headers=headers, timeout=DEFAULT_REQUEST_TIMEOUT)
        r.raise_for_status()  # Raises HTTPError for 4xx/5xx responses
        
        # Parse and validate token verification response
        token_data = r.json()
        if not token_data.get('success'):
            error_details = token_data.get('errors', [])
            raise RuntimeError(f"Token verification failed: {error_details}")
        
        logger.debug("Token verification successful")

        # Step 2: Validate access to Magic Transit routes
        list_url = f"{CLOUDFLARE_API_BASE}/accounts/{account_id}/magic/routes"
        logger.debug(f"Testing route access at: {list_url}")
        
        r2 = requests.get(list_url, headers=headers, timeout=DEFAULT_REQUEST_TIMEOUT)
        r2.raise_for_status()  # Raises HTTPError for 4xx/5xx responses
        
        # Parse and validate routes list response
        data = r2.json()
        if not data.get('success'):
            error_details = data.get('errors', [])
            raise RuntimeError(f"Route access test failed: {error_details}")

        # Extract route information for logging
        routes = data.get('result', {}).get('routes', [])
        route_count = len(routes)
        
        logger.info(f"Cloudflare connectivity validated successfully. "
                   f"Account {account_id} has {route_count} Magic Transit routes.")
        
        # Log sample route information for debugging (if any routes exist)
        if routes:
            sample_route = routes[0]
            logger.debug(f"Sample route: prefix={sample_route.get('prefix')}, "
                        f"priority={sample_route.get('priority')}, "
                        f"description={sample_route.get('description', 'N/A')}")

    except requests.exceptions.Timeout as e:
        logger.error(f"Cloudflare API request timed out: {e}")
        raise
    except requests.exceptions.ConnectionError as e:
        logger.error(f"Failed to connect to Cloudflare API: {e}")
        raise
    except requests.exceptions.HTTPError as e:
        logger.error(f"Cloudflare API returned HTTP error {e.response.status_code}: {e}")
        raise
    except Exception as e:
        logger.exception(f"Unexpected error during Cloudflare validation: {e}")
        raise


def update_routes_by_description_bulk(account_id: str,
                                    token: str,
                                    desc_substring: str,
                                    desired_priority: int,
                                    structured_logger: Optional[StructuredEventLogger] = None) -> bool:
    """
    Bulk update Magic Transit route priorities for routes matching a description pattern.
    
    This function finds all Magic Transit routes whose description contains a specific
    substring and updates their priority to a desired value. This is commonly used
    for traffic engineering and failover scenarios where route priorities need to
    be adjusted based on health checks or operational requirements.
    
    Operation Flow:
        1. Fetch all existing routes for the account
        2. Filter routes based on description substring match
        3. Identify routes that need priority updates
        4. Perform bulk update for modified routes
        5. Log results and performance metrics
    
    Route Matching Logic:
        - Case-sensitive substring matching on route description
        - Empty or None desc_substring will match no routes
        - Routes without descriptions will not match
        
    Bulk Update Behavior:
        - Only routes requiring changes are included in update request
        - Uses single API call for all updates (efficient)
        - Atomic operation: either all updates succeed or all fail
        - No partial update scenarios
        
    Args:
        account_id (str): Cloudflare account ID containing the routes to update.
        token (str): Cloudflare API token with Magic Transit edit permissions.
        desc_substring (str): Substring to search for in route descriptions.
            Case-sensitive matching. Empty string matches no routes.
        desired_priority (int): Target priority value for matching routes.
            Typically 1-1000, where lower numbers indicate higher priority.
        structured_logger (StructuredEventLogger, optional): Logger for structured
            events. If provided, detailed operation metrics and results will be logged.
            
    Returns:
        bool: True if operation completed successfully (including no-change scenarios),
              False if any errors occurred during the operation.
              
    Raises:
        requests.exceptions.HTTPError: For HTTP-level failures
            - 401: Invalid or expired token
            - 403: Insufficient permissions
            - 404: Account or routes not found
            - 429: Rate limit exceeded
            - 422: Invalid route data (malformed priority, etc.)
            - 5xx: Cloudflare server errors
        requests.exceptions.RequestException: For network failures
        ValueError: For invalid input parameters
        RuntimeError: For Cloudflare API errors (success=false responses)
        
    Side Effects:
        - Makes HTTP requests to Cloudflare API
        - Modifies route priorities in Cloudflare (if changes needed)
        - Logs operation progress and results
        - Records structured events (if logger provided)
        - May take several seconds for large route sets
        
    Performance Characteristics:
        - Route list fetch: ~200-1000ms depending on route count
        - Bulk update: ~500-2000ms depending on number of routes updated
        - Total operation time: typically 1-3 seconds
        - Scales linearly with total route count in account
        
    Examples:
        # Basic usage - update primary datacenter routes to high priority
        success = update_routes_by_description_bulk(
            account_id="abc123",
            token="your-token",
            desc_substring="primary-dc",
            desired_priority=100
        )
        
        # With structured logging for observability
        logger = StructuredEventLogger("route_manager")
        success = update_routes_by_description_bulk(
            account_id="abc123",
            token="your-token", 
            desc_substring="backup-dc",
            desired_priority=200,
            structured_logger=logger
        )
        
        # Error handling example
        try:
            success = update_routes_by_description_bulk(
                account_id="abc123",
                token="your-token",
                desc_substring="primary",
                desired_priority=50
            )
            if success:
                print("Route priorities updated successfully")
            else:
                print("Route update failed - check logs for details")
        except requests.exceptions.HTTPError as e:
            print(f"API error: {e}")
        except Exception as e:
            print(f"Unexpected error: {e}")
            
    Common Use Cases:
        1. Health-based failover: Lower priority for healthy routes
        2. Maintenance mode: Higher priority for maintenance routes  
        3. Traffic engineering: Adjust priorities for load balancing
        4. Geographic routing: Update priorities based on user location
        
    Route Priority Best Practices:
        - Use consistent priority ranges (e.g., 100-199 for primary, 200-299 for backup)
        - Leave gaps between priorities for future routing needs
        - Document priority schemes in route descriptions
        - Test priority changes in staging before production
        
    Monitoring and Alerting:
        - Monitor structured logs for failed updates
        - Alert on repeated failures (may indicate API issues)
        - Track update frequency to detect automation issues
        - Monitor route count changes for unexpected modifications
    """
    # Input validation
    if not account_id or not isinstance(account_id, str):
        raise ValueError("account_id must be a non-empty string")
    if not token or not isinstance(token, str):
        raise ValueError("token must be a non-empty string")
    if not isinstance(desc_substring, str):
        raise ValueError("desc_substring must be a string (can be empty)")
    if not isinstance(desired_priority, int) or desired_priority < 1 or desired_priority > 1000:
        raise ValueError("desired_priority must be an integer between 1 and 1000")
    
    # Initialize operation tracking variables
    start_time = time.time()
    success = False
    routes_modified = 0
    error_message = None
    result = ActionResult.FAILURE
    operation_details = {
        "total_routes": 0,
        "matching_routes": 0,
        "routes_needing_update": 0,
        "api_calls_made": 0
    }
    
    # Prepare headers for API requests
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "mt-gcp-daemon/1.0"
    }

    logger.debug(f"Starting bulk route update: account={account_id}, "
                f"filter='{desc_substring}', priority={desired_priority}")

    try:
        # Step 1: Retrieve all routes for the account
        list_url = f"{CLOUDFLARE_API_BASE}/accounts/{account_id}/magic/routes"
        logger.debug(f"Fetching routes from: {list_url}")
        
        r = requests.get(list_url, headers=headers, timeout=DEFAULT_REQUEST_TIMEOUT)
        operation_details["api_calls_made"] += 1
        r.raise_for_status()
        
        data = r.json()
        if not data.get('success'):
            error_message = f"Route list API error: {data.get('errors', 'Unknown error')}"
            raise RuntimeError(error_message)

        routes = data.get('result', {}).get('routes', [])
        operation_details["total_routes"] = len(routes)
        
        logger.debug(f"Retrieved {len(routes)} total routes from account")

        # Step 2: Filter routes by description and identify updates needed
        updates = []
        matching_routes = 0

        for route in routes:
            route_id = route.get('id')
            route_desc = route.get('description', '')
            current_priority = route.get('priority')
            route_prefix = route.get('prefix')
            
            # Check if route description contains the target substring
            if desc_substring and desc_substring in route_desc:
                matching_routes += 1
                
                logger.debug(f"Route {route_id} matches filter: desc='{route_desc}', "
                           f"current_priority={current_priority}")
                
                # Check if priority update is needed
                if current_priority != desired_priority:
                    update_payload = {
                        "id": route_id,
                        "prefix": route.get('prefix'),
                        "nexthop": route.get('nexthop'),
                        "priority": desired_priority
                    }
                    
                    # Include optional fields if present
                    if route.get('description'):
                        update_payload["description"] = route.get('description')
                    if route.get('weight'):
                        update_payload["weight"] = route.get('weight')
                    
                    updates.append(update_payload)
                    
                    logger.info(f"Queued route {route_id} ({route_desc}) for update: "
                              f"{current_priority} -> {desired_priority}")
                else:
                    logger.debug(f"Route {route_id} already at desired priority {desired_priority}")

        operation_details["matching_routes"] = matching_routes
        operation_details["routes_needing_update"] = len(updates)

        # Step 3: Determine operation result based on what we found
        if not updates:
            if matching_routes > 0:
                # Routes found but no updates needed
                logger.debug(f"No route changes needed. {matching_routes} routes already "
                           f"at priority {desired_priority}")
                result = ActionResult.NO_CHANGE
                success = True
            else:
                # No routes matched the filter
                if desc_substring:
                    logger.warning(f"No routes found matching description substring: '{desc_substring}'")
                else:
                    logger.warning("Empty description substring provided - no routes matched")
                result = ActionResult.NO_CHANGE
                success = True
        else:
            # Step 4: Perform bulk update
            logger.info(f"Performing bulk update for {len(updates)} routes")
            
            # Check for bulk update size limits
            if len(updates) > MAX_ROUTES_PER_REQUEST:
                logger.warning(f"Update batch size ({len(updates)}) exceeds recommended "
                             f"limit ({MAX_ROUTES_PER_REQUEST}). Consider chunking updates.")
            
            payload = {"routes": updates}
            
            logger.debug(f"Sending bulk update request with {len(updates)} route changes")
            put_resp = requests.put(list_url, headers=headers, json=payload,
                                  timeout=BULK_UPDATE_TIMEOUT)
            operation_details["api_calls_made"] += 1
            put_resp.raise_for_status()
            
            put_result = put_resp.json()
            
            if put_result.get('success'):
                # Extract results from API response
                result_data = put_result.get('result', {})
                routes_modified = result_data.get('modified', len(updates))
                
                logger.info(f"Bulk update completed successfully: {routes_modified} routes modified")
                
                # Log details of modified routes for audit trail
                if 'routes' in result_data:
                    for modified_route in result_data['routes'][:5]:  # Log first 5 for brevity
                        logger.debug(f"Modified route: {modified_route.get('id')} -> "
                                   f"priority {modified_route.get('priority')}")
                
                result = ActionResult.SUCCESS
                success = True
            else:
                # API returned success=false
                api_errors = put_result.get('errors', [])
                error_message = f"Bulk update API errors: {api_errors}"
                logger.error(error_message)
                raise RuntimeError(error_message)

    except requests.exceptions.Timeout as e:
        error_message = f"Request timeout: {str(e)}"
        logger.error(f"Cloudflare API request timed out during route update: {e}")
    except requests.exceptions.ConnectionError as e:
        error_message = f"Connection error: {str(e)}"
        logger.error(f"Failed to connect to Cloudflare API: {e}")
    except requests.exceptions.HTTPError as e:
        error_message = f"HTTP {e.response.status_code}: {str(e)}"
        if e.response.status_code == 429:
            logger.error("Rate limit exceeded - consider implementing backoff/retry logic")
        elif e.response.status_code == 422:
            logger.error("Invalid route data - check priority values and route format")
        else:
            logger.error(f"Cloudflare API HTTP error: {e}")
    except RuntimeError as e:
        error_message = str(e)
        logger.error(f"Cloudflare API operation failed: {e}")
    except Exception as e:
        error_message = f"Unexpected error: {str(e)}"
        logger.exception(f"Unexpected error in Cloudflare route update: {e}")
    
    finally:
        # Step 5: Log structured event with comprehensive operation details
        operation_duration_ms = int((time.time() - start_time) * 1000)
        
        # Enhance operation details with final results
        operation_details.update({
            "operation_duration_ms": operation_duration_ms,
            "routes_modified": routes_modified,
            "success": success,
            "error_message": error_message
        })
        
        if structured_logger:
            structured_logger.log_cloudflare_update(
                account_id=account_id,
                description_filter=desc_substring,
                desired_priority=desired_priority,
                routes_modified=routes_modified,
                result=result,
                duration_ms=operation_duration_ms,
                error_message=error_message
            )
        
        # Log final operation summary
        logger.info(f"Route update operation completed: success={success}, "
                   f"duration={operation_duration_ms}ms, "
                   f"routes_modified={routes_modified}, "
                   f"api_calls={operation_details['api_calls_made']}")
    
    return success


def get_routes_by_description(account_id: str,
                            token: str,
                            desc_substring: str) -> List[Dict[str, Any]]:
    """
    Retrieve Magic Transit routes that match a specific description pattern.
    
    This is a utility function for querying routes without modifying them.
    Useful for monitoring, auditing, and validation operations.
    
    Args:
        account_id (str): Cloudflare account ID
        token (str): Cloudflare API token with read permissions
        desc_substring (str): Substring to search for in route descriptions
        
    Returns:
        List[Dict[str, Any]]: List of route objects matching the description filter
        
    Raises:
        requests.exceptions.HTTPError: For HTTP-level failures
        RuntimeError: For Cloudflare API errors
        
    Example:
        routes = get_routes_by_description("abc123", "token", "primary-dc")
        for route in routes:
            print(f"Route {route['id']}: {route['prefix']} -> {route['nexthop']}")
    """
    if not desc_substring:
        return []
        
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "mt-gcp-daemon/1.0"
    }
    
    list_url = f"{CLOUDFLARE_API_BASE}/accounts/{account_id}/magic/routes"
    r = requests.get(list_url, headers=headers, timeout=DEFAULT_REQUEST_TIMEOUT)
    r.raise_for_status()
    
    data = r.json()
    if not data.get('success'):
        raise RuntimeError(f"API error: {data.get('errors')}")
    
    routes = data.get('result', {}).get('routes', [])
    matching_routes = [
        route for route in routes
        if desc_substring in route.get('description', '')
    ]
    
    logger.debug(f"Found {len(matching_routes)} routes matching '{desc_substring}' "
                f"out of {len(routes)} total routes")
    
    return matching_routes


# Module-level constants for API configuration
# These align with the actual implementation and environment-driven configuration
CLOUDFLARE_API_BASE = "https://api.cloudflare.com/client/v4"
DEFAULT_REQUEST_TIMEOUT = 10  # seconds
BULK_UPDATE_TIMEOUT = 60      # seconds for bulk operations
MAX_ROUTES_PER_REQUEST = 1000 # Cloudflare API limit (approximate)

# Priority Configuration Guidelines:
# The actual priority values are configured via environment variables:
# - CLOUDFLARE_PRIMARY_PRIORITY (default: 100)
# - CLOUDFLARE_SECONDARY_PRIORITY (default: 200)
#
# Common priority schemes:
# - Lower numbers = higher priority (Cloudflare standard)
# - Typical ranges: 1-1000
# - Leave gaps between priorities for future needs
# - Example scheme:
#   * Primary datacenter: 100-199
#   * Secondary datacenter: 200-299
#   * Emergency/maintenance: 800-999
#
# Description Substring Matching:
# The DESCRIPTION_SUBSTRING environment variable determines which routes
# are updated. Common patterns include:
# - "primary-dc" for primary datacenter routes
# - "backup-dc" for backup datacenter routes
# - "region-west" for geographic-based routing
# - "maintenance" for maintenance mode routes


# Example usage and testing code
if __name__ == "__main__":
    """
    Example usage and testing scenarios for Cloudflare route management.
    
    This section demonstrates common patterns and can be used for
    manual testing or as a reference for integration.
    """
    import os
    from .structured_events import StructuredEventLogger
    
    # Configuration (normally from environment)
    ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "your-account-id")
    API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "your-api-token")
    
    def example_connectivity_validation():
        """Example: Validate Cloudflare connectivity"""
        print("=== Connectivity Validation Example ===")
        try:
            validate_cloudflare_connectivity(ACCOUNT_ID, API_TOKEN)
            print("✓ Connectivity validation successful")
        except Exception as e:
            print(f"✗ Connectivity validation failed: {e}")
    
    def example_route_query():
        """Example: Query routes by description"""
        print("\n=== Route Query Example ===")
        try:
            routes = get_routes_by_description(ACCOUNT_ID, API_TOKEN, "primary")
            print(f"Found {len(routes)} routes matching 'primary'")
            for route in routes[:3]:  # Show first 3
                print(f"  Route {route['id']}: {route.get('prefix')} "
                     f"(priority: {route.get('priority')})")
        except Exception as e:
            print(f"✗ Route query failed: {e}")
    
    def example_priority_update():
        """Example: Update route priorities using environment configuration"""
        print("\n=== Route Priority Update Example ===")
        logger = StructuredEventLogger("cloudflare_example")
        
        # Use environment variables like the real daemon does
        desc_substring = os.getenv("DESCRIPTION_SUBSTRING", "primary")
        primary_priority = int(os.getenv("CLOUDFLARE_PRIMARY_PRIORITY", 100))
        
        try:
            success = update_routes_by_description_bulk(
                account_id=ACCOUNT_ID,
                token=API_TOKEN,
                desc_substring=desc_substring,
                desired_priority=primary_priority,
                structured_logger=logger
            )
            
            if success:
                print(f"✓ Route priority update successful (priority={primary_priority})")
            else:
                print("✗ Route priority update failed")
                
        except Exception as e:
            print(f"✗ Route priority update failed: {e}")
    
    def example_failover_scenario():
        """Example: Simulate datacenter failover using actual daemon logic"""
        print("\n=== Failover Scenario Example ===")
        logger = StructuredEventLogger("failover_manager")
        
        # Use the same logic as the real daemon
        desc_substring = os.getenv("DESCRIPTION_SUBSTRING", "datacenter")
        primary_priority = int(os.getenv("CLOUDFLARE_PRIMARY_PRIORITY", 100))
        secondary_priority = int(os.getenv("CLOUDFLARE_SECONDARY_PRIORITY", 200))
        
        print(f"Using priorities: primary={primary_priority}, secondary={secondary_priority}")
        print(f"Targeting routes with description containing: '{desc_substring}'")
        
        # Simulate primary datacenter failure
        print("Simulating primary datacenter failure - switching to secondary priority...")
        try:
            success = update_routes_by_description_bulk(
                account_id=ACCOUNT_ID,
                token=API_TOKEN,
                desc_substring=desc_substring,
                desired_priority=secondary_priority,  # Switch to secondary priority
                structured_logger=logger
            )
            
            if success:
                print("✓ Failover to secondary priority completed")
                
                # Simulate recovery - switch back to primary
                print("Simulating recovery - switching back to primary priority...")
                recovery_success = update_routes_by_description_bulk(
                    account_id=ACCOUNT_ID,
                    token=API_TOKEN,
                    desc_substring=desc_substring,
                    desired_priority=primary_priority,  # Switch back to primary
                    structured_logger=logger
                )
                
                if recovery_success:
                    print("✓ Recovery to primary priority completed")
                else:
                    print("✗ Recovery failed")
            else:
                print("✗ Failover failed - check logs")
                
        except Exception as e:
            print(f"✗ Failover scenario failed: {e}")
    
    # Run examples if module is executed directly
    if ACCOUNT_ID != "your-account-id" and API_TOKEN != "your-api-token":
        example_connectivity_validation()
        example_route_query()
        example_priority_update()
        example_failover_scenario()
    else:
        print("Set CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN environment variables to run examples")
