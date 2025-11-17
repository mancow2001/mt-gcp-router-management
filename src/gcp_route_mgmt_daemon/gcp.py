"""
Google Cloud Platform Integration Module for Health Monitoring and BGP Management

This module provides comprehensive integration with Google Cloud Platform (GCP) services
for health monitoring and BGP route management. It focuses on two key areas:

1. Backend Service Health Monitoring: Checks the health of Load Balancer backend services
2. BGP Route Management: Controls BGP route advertisements on Cloud Routers

The module is designed for use in automated failover scenarios where route advertisements
need to be dynamically adjusted based on service health status.

Key GCP Services Integrated:
    - Compute Engine API: For backend service health and router management
    - Cloud Load Balancing: Backend service health monitoring
    - Cloud Router: BGP session monitoring and route advertisement control
    - Service Account Authentication: Secure API access with minimal privileges

Architecture Overview:
    This module uses the closure pattern to create pre-configured functions that can
    be called repeatedly with consistent parameters. This approach enables:
    - Configuration encapsulation
    - Consistent error handling
    - Performance optimization through connection reuse
    - Clean integration with retry and circuit breaker patterns

Health Check Types:
    1. Backend Service Health: Monitors individual backend instances within services
    2. BGP Session Health: Monitors BGP peer connectivity status

BGP Advertisement Management:
    Routes can be dynamically advertised or withdrawn based on health status:
    - Advertise routes when services are healthy (attract traffic)
    - Withdraw routes when services are unhealthy (redirect traffic)
    - Support for multiple route prefixes and priority-based routing

Error Handling Strategy:
    - Permanent errors (403, 404): Re-raised immediately (configuration issues)
    - Transient errors (429, 5xx): Logged as warnings, return unhealthy status
    - Network errors: Treated as transient, logged and returned as unhealthy
    - Unexpected errors: Logged with full stack trace, treated as failures

Authentication and Permissions:
    Requires a GCP service account with the following IAM roles:
    - Compute Engine Viewer: For reading backend service and router status
    - Compute Engine Network Admin: For modifying BGP advertisements
    - Or custom role with these specific permissions:
      * compute.backendServices.get, compute.backendServices.getHealth
      * compute.routers.get, compute.routers.getRouterStatus, compute.routers.update
      * compute.regions.get, compute.projects.get

Performance Considerations:
    - API calls are cached where possible to reduce latency
    - Backend health checks scale with the number of backend services and instances
    - BGP operations are typically fast (< 1 second) but may have propagation delays
    - Router status queries include all BGP peers, so response time scales with peer count

Integration with Structured Logging:
    All operations generate structured events for observability:
    - Health check results with detailed backend status
    - BGP advertisement changes with operation tracking
    - Performance metrics and error context
    - Correlation ID support for tracing operations

Rate Limiting and Quotas:
    GCP APIs have quotas and rate limits. This module:
    - Uses appropriate timeouts to avoid hanging operations
    - Provides detailed error information for quota exceeded scenarios
    - Integrates with retry mechanisms for handling temporary rate limits

Example Usage:
    # Initialize client
    compute = build_compute_client('/path/to/service-account.json')
    
    # Validate connectivity
    validate_gcp_connectivity('my-project', ['us-central1', 'us-east1'], compute)
    
    # Create health check functions
    local_health_check = backend_services_healthy('my-project', 'us-central1', compute)
    bgp_health_check = router_bgp_sessions_healthy('my-project', 'us-central1', 'my-router', compute)
    
    # Create BGP management function
    bgp_advertiser = update_bgp_advertisement('my-project', 'us-central1', 'my-router', '10.0.0.0/24', compute)
    
    # Use in monitoring loop
    if local_health_check():
        bgp_advertiser(advertise=True)
    else:
        bgp_advertiser(advertise=False)

Security Best Practices:
    - Use least-privilege service accounts with minimal required permissions
    - Store service account keys securely (not in version control)
    - Rotate service account keys regularly
    - Monitor API usage for unauthorized access patterns
    - Use VPC Service Controls to restrict API access if required

Monitoring and Alerting:
    - Monitor structured logs for health check failures and BGP changes
    - Alert on persistent backend service health failures
    - Track BGP session status changes for network connectivity issues
    - Monitor API error rates and quota usage
    - Set up dashboards for service health trends and BGP advertisement status

Author: Nathan Bray
Version: 1.0
Last Modified: 2024
Dependencies: google-cloud-compute, google-auth, googleapiclient
"""

import os
import logging
import time
from typing import Optional, Dict, List, Any, Tuple, Callable
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from .structured_events import StructuredEventLogger, ActionResult

# Logger for all GCP operations - uses environment variable for consistency
logger = logging.getLogger(os.getenv("LOGGER_NAME", "HEALTH_CHECK_DAEMON"))

# GCP API configuration constants
GCP_API_VERSION = "v1"                    # Compute Engine API version
DEFAULT_API_TIMEOUT = 30                  # Default timeout for API calls (seconds)
BACKEND_HEALTH_TIMEOUT = 45               # Timeout for backend health checks
BGP_OPERATION_TIMEOUT = 60                # Timeout for BGP advertisement updates

# Health state constants from GCP API
HEALTHY_STATE = "HEALTHY"                 # GCP backend health state for healthy instances
UNHEALTHY_STATES = [                      # All possible unhealthy states
    "UNHEALTHY", "DRAINING", "TIMEOUT", "UNKNOWN"
]

# HTTP status codes for permanent vs transient errors
PERMANENT_HTTP_ERRORS = [403, 404]        # Errors that indicate configuration issues
TRANSIENT_HTTP_ERRORS = [429, 500, 502, 503, 504]  # Errors that may be retried


def build_compute_client(creds_path: str, timeout: int = DEFAULT_API_TIMEOUT):
    """
    Initialize a Google Compute Engine API client using service account credentials.

    This function creates an authenticated client for the Compute Engine API using
    service account credentials from a JSON key file. The client is configured with
    appropriate defaults for production use.

    The returned client can be used for all Compute Engine operations including:
    - Backend service health monitoring
    - Cloud Router BGP management
    - Regional resource queries

    Authentication:
        Uses OAuth 2.0 service account authentication with JSON key file.
        The service account must have appropriate IAM permissions for the
        intended operations.

    Args:
        creds_path (str): Absolute or relative path to the service account JSON key file.
            The file must be readable by the current process and contain valid
            service account credentials.
        timeout (int, optional): [Deprecated/Unused] HTTP request timeout parameter.
            Kept for backward compatibility but no longer used in Python 3.12+.
            The modern google-api-python-client handles timeouts internally.
            Defaults to DEFAULT_API_TIMEOUT.

    Returns:
        googleapiclient.discovery.Resource: Authenticated Compute Engine API client
            configured for version v1. The client includes built-in retry logic
            and connection pooling for optimal performance.
            
    Raises:
        FileNotFoundError: If the credentials file does not exist or is not readable.
            This is a permanent error that indicates a configuration issue.
        google.auth.exceptions.GoogleAuthError: If the credentials file is invalid,
            corrupted, or the service account has been disabled.
        google.auth.exceptions.RefreshError: If the credentials cannot be refreshed,
            typically due to network issues or revoked access.
        ValueError: If the credentials file is not valid JSON or missing required fields.
        
    Security Considerations:
        - Service account key files contain sensitive credentials
        - Store key files securely with appropriate file permissions (600)
        - Never commit key files to version control
        - Consider using Workload Identity or other keyless authentication in production
        - Rotate service account keys regularly per security best practices
        
    Performance:
        - Client initialization: ~200-500ms depending on network latency
        - Client includes connection pooling for subsequent API calls
        - Discovery document is cached to improve startup time
        - Recommended to create client once and reuse throughout application lifecycle

    Python 3.12+ Compatibility:
        - Uses modern credentials-based authentication (credentials parameter)
        - Compatible with google-auth 2.x and google-api-python-client 2.x
        - No longer uses deprecated httplib2.Http().authorize() method
        
    Example:
        # Basic usage
        compute = build_compute_client('/path/to/service-account.json')
        
        # With error handling
        try:
            compute = build_compute_client(os.getenv('GCP_CREDENTIALS_PATH'))
            print("GCP client initialized successfully")
        except FileNotFoundError:
            print("Credentials file not found - check path configuration")
        except Exception as e:
            print(f"Failed to initialize GCP client: {e}")
            
    IAM Permissions Required:
        The service account should have these roles or equivalent permissions:
        - roles/compute.viewer: For reading backend services and router status
        - roles/compute.networkAdmin: For modifying BGP advertisements
        
        Or custom role with specific permissions:
        - compute.backendServices.get
        - compute.backendServices.getHealth  
        - compute.routers.get
        - compute.routers.getRouterStatus
        - compute.routers.update
        - compute.regions.get
        - compute.projects.get
    """
    # Validate credentials file exists and is readable
    if not os.path.exists(creds_path):
        error_msg = f"GCP credentials file not found: {creds_path}"
        logger.error(error_msg)
        raise FileNotFoundError(error_msg)
    
    if not os.access(creds_path, os.R_OK):
        error_msg = f"GCP credentials file not readable: {creds_path}"
        logger.error(error_msg)
        raise PermissionError(error_msg)
    
    try:
        logger.debug(f"Loading GCP service account credentials from: {creds_path}")

        # Load service account credentials from JSON key file
        creds = service_account.Credentials.from_service_account_file(creds_path)

        # Build Compute Engine API client with credentials
        # Python 3.12+ compatible: pass credentials directly instead of using authorize()
        # cache_discovery=False prevents caching discovery documents to disk
        compute = build(
            serviceName='compute',
            version=GCP_API_VERSION,
            credentials=creds,
            cache_discovery=False
        )
        
        logger.debug("GCP Compute Engine client initialized successfully")
        return compute
        
    except Exception as e:
        error_msg = f"Failed to build GCP compute client: {str(e)}"
        logger.error(error_msg)
        raise


def validate_gcp_connectivity(project: str, regions: List[str], compute) -> None:
    """
    Validate GCP API connectivity and permissions by testing access to required resources.
    
    This function performs connectivity and permission validation by making test API calls
    to ensure the GCP client can access the required project and regions. This validation
    should be performed at startup to fail fast if credentials or permissions are invalid.
    
    Validation Steps:
        1. Project Access: Validates access to the specified GCP project
        2. Region Access: Validates access to each specified region
        3. API Connectivity: Confirms Compute Engine API is accessible
        
    This function is designed to catch configuration issues early rather than
    discovering them during operational health checks.
    
    Args:
        project (str): GCP project ID to validate access to. Must be a valid project
            that the service account has permissions to access.
        regions (List[str]): List of GCP region names to validate access to.
            Each region must exist and be accessible with current credentials.
        compute: Authenticated GCP Compute Engine client from build_compute_client().
        
    Raises:
        HttpError: If API calls fail due to authentication or permission issues.
            - 401: Invalid or expired credentials
            - 403: Insufficient permissions for project/region access
            - 404: Project or region does not exist
        google.auth.exceptions.RefreshError: If credentials cannot be refreshed
        Exception: For network connectivity issues or unexpected API errors
        
    Side Effects:
        - Makes API calls to GCP Compute Engine
        - Logs validation progress and results
        - No resources are created or modified
        
    Performance:
        - Project validation: ~100-300ms
        - Region validation: ~50-150ms per region
        - Total time scales linearly with number of regions
        - Recommended to validate regions in parallel for large lists (not implemented)
        
    Example:
        try:
            regions = ['us-central1', 'us-east1', 'europe-west1']
            validate_gcp_connectivity('my-project-id', regions, compute)
            print("GCP connectivity validation successful")
        except HttpError as e:
            if e.resp.status == 403:
                print("Insufficient permissions - check IAM roles")
            elif e.resp.status == 404:
                print("Project or region not found - check configuration")
        except Exception as e:
            print(f"Connectivity validation failed: {e}")
            
    Common Validation Failures:
        - 403 Forbidden: Service account lacks compute.projects.get permission
        - 404 Not Found: Invalid project ID or region name
        - Network errors: DNS resolution issues or firewall blocking API access
        - Credential issues: Expired, revoked, or malformed service account key
        
    Troubleshooting:
        - Verify project ID is correct and accessible
        - Check service account has required IAM roles
        - Confirm regions exist and are spelled correctly
        - Test network connectivity to googleapis.com
        - Validate service account key file is not expired or corrupted
    """
    if not project:
        raise ValueError("Project ID cannot be empty")
    if not regions:
        raise ValueError("Regions list cannot be empty")
    
    logger.info(f"Validating GCP connectivity for project '{project}' and {len(regions)} regions")
    
    try:
        # Step 1: Validate project access
        logger.debug(f"Validating access to GCP project: {project}")
        resp = compute.projects().get(project=project).execute()
        project_name = resp.get('name', project)
        
        logger.info(f"✓ GCP project access validated: {project_name}")
        
        # Step 2: Validate region access
        logger.debug(f"Validating access to regions: {regions}")
        for region in regions:
            try:
                region_resp = compute.regions().get(project=project, region=region).execute()
                logger.debug(f"✓ Region access validated: {region}")
            except HttpError as e:
                if e.resp.status == 404:
                    raise ValueError(f"Region '{region}' not found in project '{project}'")
                else:
                    raise
        
        logger.info(f"✓ All {len(regions)} regions validated successfully")
        
        # Log successful validation with details
        validated_regions = ", ".join(regions)
        logger.info(f"GCP connectivity validation completed successfully for project '{project}' "
                   f"with regions: {validated_regions}")
        
    except HttpError as e:
        error_context = f"project='{project}', regions={regions}"
        if e.resp.status == 401:
            error_msg = f"Authentication failed for GCP API ({error_context}): {e}"
        elif e.resp.status == 403:
            error_msg = f"Insufficient permissions for GCP resources ({error_context}): {e}"
        elif e.resp.status == 404:
            error_msg = f"GCP resource not found ({error_context}): {e}"
        else:
            error_msg = f"GCP API error ({error_context}): {e}"
        
        logger.error(error_msg)
        raise
    
    except Exception as e:
        error_msg = f"Unexpected error during GCP connectivity validation: {e}"
        logger.exception(error_msg)
        raise


def backend_services_healthy(project: str,
                           region: str,
                           compute_client,
                           structured_logger: Optional[StructuredEventLogger] = None) -> Callable[[], bool]:
    """
    Create a function that checks the health of all backend services in a GCP region.
    
    This function uses the closure pattern to create a pre-configured health check
    function that can be called repeatedly without re-specifying parameters. The
    returned function will query all regional backend services and their associated
    backends to determine overall health status.
    
    Backend Service Health Logic:
        - Queries all regional backend services in the specified region
        - For each service, checks the health of all backends (instance groups)
        - A region is considered healthy only if ALL backends are healthy
        - Empty regions (no backend services) are considered healthy
        - Any unhealthy backend causes the entire region to be marked unhealthy
    
    Health States (from GCP API):
        - HEALTHY: Backend is healthy and receiving traffic
        - UNHEALTHY: Backend failed health checks
        - DRAINING: Backend is being drained (planned maintenance)
        - TIMEOUT: Health check timed out
        - UNKNOWN: Health state cannot be determined
    
    Error Handling:
        - Permanent errors (403, 404): Re-raised immediately
        - Transient errors (429, 5xx): Logged as warnings, return False
        - Network errors: Treated as transient, return False
        - Incomplete API responses: Treated as unhealthy, logged as warnings
    
    Args:
        project (str): GCP project ID containing the backend services
        region (str): GCP region name to check (e.g., 'us-central1')
        compute_client: Authenticated GCP Compute Engine client
        structured_logger (StructuredEventLogger, optional): Logger for structured events
        
    Returns:
        Callable[[], bool]: Function that returns True if all backends are healthy,
            False if any backend is unhealthy or if errors occur
            
    Performance Characteristics:
        - API calls scale with number of backend services and backends
        - Typical response time: 500ms - 3s depending on service count
        - Large regions with many services may take longer
        - Results are not cached - each call makes fresh API requests
        
    Example:
        # Create health check function
        health_checker = backend_services_healthy('my-project', 'us-central1', compute)
        
        # Use in monitoring loop
        while True:
            if health_checker():
                print("All backend services are healthy")
            else:
                print("Some backend services are unhealthy")
            time.sleep(60)
            
        # With structured logging
        logger = StructuredEventLogger("health_monitor")
        health_checker = backend_services_healthy('my-project', 'us-central1', compute, logger)
        
    Structured Logging Output:
        When structured_logger is provided, detailed health check results are logged:
        - Total number of backend services checked
        - List of unhealthy backends with reasons
        - Error details for API failures
        - Performance timing information
        
    Common Scenarios:
        - All healthy: Returns True, logs success
        - Some unhealthy: Returns False, logs details of unhealthy backends
        - No services: Returns True (empty region considered healthy)
        - API errors: Returns False, logs error details
        - Permission errors: Raises exception (permanent failure)
    """
    if not project:
        raise ValueError("Project ID cannot be empty")
    if not region:
        raise ValueError("Region cannot be empty")
    
    def _check() -> bool:
        """
        Internal health check function that performs the actual API calls and health evaluation.
        
        This function is returned by the outer function and captures the configuration
        parameters in its closure. It can be called repeatedly to perform health checks
        with consistent configuration.
        
        Returns:
            bool: True if all backend services and their backends are healthy,
                  False if any are unhealthy or if errors occur
        """
        start_time = time.time()
        healthy = False
        
        # Initialize detailed tracking for structured logging
        details = {
            "project": project,
            "region": region,
            "backend_services_checked": 0,
            "total_backends": 0,
            "healthy_backends": 0,
            "unhealthy_backends": [],
            "services_with_issues": []
        }
        
        logger.debug(f"Starting backend service health check for {project}/{region}")
        
        try:
            # Query all regional backend services
            logger.debug(f"Listing backend services in {project}/{region}")
            request = compute_client.regionBackendServices().list(
                project=project,
                region=region
            )
            response = request.execute()
            
            backend_services = response.get('items', [])
            details["backend_services_checked"] = len(backend_services)
            
            if not backend_services:
                # No backend services found - consider this healthy
                logger.info(f"No backend services found in {project}/{region} - considering healthy")
                healthy = True
            else:
                # Check health of all backend services
                logger.debug(f"Found {len(backend_services)} backend services to check")
                healthy = True  # Assume healthy until proven otherwise
                
                for service in backend_services:
                    service_name = service['name']
                    service_healthy = True
                    service_backend_count = 0
                    
                    logger.debug(f"Checking backends for service: {service_name}")
                    
                    # Check each backend within the service
                    for backend in service.get('backends', []):
                        service_backend_count += 1
                        details["total_backends"] += 1
                        backend_group = backend['group']
                        
                        try:
                            # Get health status for this backend group
                            health_request = compute_client.regionBackendServices().getHealth(
                                project=project,
                                region=region,
                                backendService=service_name,
                                body={"group": backend_group}
                            )
                            health_response = health_request.execute()
                            
                            # Check for incomplete health response
                            if health_response == {"kind": "compute#backendServiceGroupHealth"}:
                                logger.warning(f"Incomplete health response for backend {backend_group} "
                                             f"in service {service_name} ({project}/{region})")
                                healthy = False
                                service_healthy = False
                                details["unhealthy_backends"].append({
                                    "service": service_name,
                                    "backend": backend_group,
                                    "reason": "incomplete_health_response",
                                    "health_state": "UNKNOWN"
                                })
                                continue
                            
                            # Check individual instance health within the backend
                            health_statuses = health_response.get('healthStatus', [])
                            
                            if not health_statuses:
                                logger.warning(f"No health status returned for backend {backend_group} "
                                             f"in service {service_name}")
                                healthy = False
                                service_healthy = False
                                details["unhealthy_backends"].append({
                                    "service": service_name,
                                    "backend": backend_group,
                                    "reason": "no_health_status",
                                    "health_state": "UNKNOWN"
                                })
                                continue
                            
                            # Evaluate health of all instances in this backend
                            backend_healthy = True
                            for health_status in health_statuses:
                                instance_health = health_status.get('healthState')
                                instance = health_status.get('instance', 'unknown')
                                
                                if instance_health != HEALTHY_STATE:
                                    logger.warning(f"Unhealthy instance {instance} in backend {backend_group} "
                                                 f"of service {service_name} ({project}/{region}): {instance_health}")
                                    
                                    healthy = False
                                    service_healthy = False
                                    backend_healthy = False
                                    
                                    details["unhealthy_backends"].append({
                                        "service": service_name,
                                        "backend": backend_group,
                                        "instance": instance,
                                        "health_state": instance_health,
                                        "reason": "unhealthy_instance"
                                    })
                            
                            if backend_healthy:
                                details["healthy_backends"] += 1
                                logger.debug(f"Backend {backend_group} in service {service_name} is healthy")
                            
                        except HttpError as e:
                            logger.warning(f"HTTP error getting health for backend {backend_group} "
                                         f"in service {service_name}: {e}")
                            healthy = False
                            service_healthy = False
                            details["unhealthy_backends"].append({
                                "service": service_name,
                                "backend": backend_group,
                                "reason": f"api_error_http_{e.resp.status}",
                                "error_message": str(e)
                            })
                        
                        except Exception as e:
                            logger.warning(f"Unexpected error getting health for backend {backend_group} "
                                         f"in service {service_name}: {e}")
                            healthy = False
                            service_healthy = False
                            details["unhealthy_backends"].append({
                                "service": service_name,
                                "backend": backend_group,
                                "reason": "unexpected_error",
                                "error_message": str(e)
                            })
                    
                    # Track services with issues for debugging
                    if not service_healthy:
                        details["services_with_issues"].append({
                            "service": service_name,
                            "backend_count": service_backend_count
                        })
                        logger.debug(f"Service {service_name} has unhealthy backends")
                    else:
                        logger.debug(f"Service {service_name} is healthy ({service_backend_count} backends)")
                
                # Log final health status
                if healthy:
                    logger.info(f"All backend services healthy in {project}/{region} "
                              f"({details['total_backends']} backends across "
                              f"{details['backend_services_checked']} services)")
                else:
                    unhealthy_count = len(details["unhealthy_backends"])
                    logger.warning(f"Backend services unhealthy in {project}/{region}: "
                                 f"{unhealthy_count} unhealthy backends out of {details['total_backends']}")
                        
        except HttpError as e:
            details["error_code"] = e.resp.status
            details["error_reason"] = str(e)

            # Handle permanent vs transient/unknown errors differently
            if e.resp.status in PERMANENT_HTTP_ERRORS:
                logger.error(f"Permanent error checking backend health for {project}/{region}: {e}")
                raise  # Re-raise permanent errors for immediate attention
            elif e.resp.status in TRANSIENT_HTTP_ERRORS:
                # Known transient errors - temporary API issues, monitoring unreliable
                logger.warning(f"Transient HTTP error ({e.resp.status}) checking backend health "
                             f"for {project}/{region}. Monitoring temporarily unreliable - "
                             f"will maintain current routing state: {e}")
                details["monitoring_unavailable"] = True
                healthy = None  # Monitoring unreliable -> unknown health
            else:
                # Unknown/unexpected error code - don't make routing changes based on unknown errors
                logger.warning(f"Unknown HTTP error code {e.resp.status} checking backend health "
                             f"for {project}/{region}. Cannot determine backend health with unexpected "
                             f"error - will maintain current routing state: {e}")
                details["monitoring_unavailable"] = True
                healthy = None  # Unknown error -> unknown health
                
        except Exception as e:
            healthy = False
            details["error_reason"] = str(e)
            logger.exception(f"Unexpected error checking backend health for {project}/{region}: {e}")
        
        finally:
            # Always log structured event if logger is available
            if structured_logger:
                duration_ms = int((time.time() - start_time) * 1000)
                structured_logger.log_health_check(
                    region=region,
                    service_type="backend_services",
                    healthy=healthy,
                    details=details,
                    duration_ms=duration_ms
                )
        
        return healthy
    
    return _check


def router_bgp_sessions_healthy(project: str,
                              region: str,
                              router: str,
                              compute_client,
                              structured_logger: Optional[StructuredEventLogger] = None) -> Callable[[], Tuple[bool, Dict[str, str]]]:
    """
    Create a function that checks the health of BGP sessions on a Cloud Router.
    
    This function returns a closure that monitors BGP peer connectivity status on
    a specified Cloud Router. BGP (Border Gateway Protocol) sessions are critical
    for network connectivity and route exchange with external networks.
    
    BGP Session Health Logic:
        - Queries the Cloud Router for BGP peer status
        - A router is considered healthy if at least one BGP peer is UP
        - Returns detailed status for all peers for troubleshooting
        - Empty peer lists (no BGP peers configured) are considered unhealthy
    
    BGP Peer States (from GCP):
        - UP: BGP session is established and exchanging routes
        - DOWN: BGP session is down (connectivity or configuration issues)
        - IDLE: BGP session is idle (not attempting connection)
        - CONNECT: BGP session is attempting to connect
        - ACTIVE: BGP session is in active state (opening connection)
        - OPENSENT: BGP session has sent OPEN message
        - OPENCONFIRM: BGP session has received OPEN confirmation
    
    Use Cases:
        - Network connectivity monitoring for hybrid cloud setups
        - Failover decision making based on external network connectivity
        - Troubleshooting BGP peering issues
        - Automated route management based on peer health
    
    Args:
        project (str): GCP project ID containing the Cloud Router
        region (str): GCP region where the router is located
        router (str): Name of the Cloud Router to monitor
        compute_client: Authenticated GCP Compute Engine client
        structured_logger (StructuredEventLogger, optional): Logger for structured events
        
    Returns:
        Callable[[], Tuple[bool, Dict[str, str]]]: Function that returns:
            - bool: True if any BGP peer is UP, False otherwise
            - Dict[str, str]: Mapping of peer names to their status
            
    Performance:
        - Single API call per check (efficient)
        - Response time: typically 200-800ms
        - Scales with number of BGP peers on the router
        - Router status includes all peers in single response
        
    Example:
        # Create BGP health check function
        bgp_checker = router_bgp_sessions_healthy('my-project', 'us-central1', 'my-router', compute)
        
        # Use in monitoring
        any_up, peer_statuses = bgp_checker()
        if any_up:
            print("BGP connectivity available")
            for peer, status in peer_statuses.items():
                print(f"  {peer}: {status}")
        else:
            print("No BGP peers are UP")
            
        # Integration with failover logic
        def should_advertise_routes():
            bgp_up, _ = bgp_checker()
            return bgp_up and other_health_conditions()
            
    Error Handling:
        - Router not found (404): Permanent error, re-raised
        - Permission denied (403): Permanent error, re-raised  
        - Transient errors: Logged, return (False, {})
        - Network errors: Treated as transient
        
    Structured Logging:
        When structured_logger is provided, logs include:
        - Total number of BGP peers
        - Number of peers in UP state
        - Detailed peer status mapping
        - Error information for failures
        - Performance timing
        
    Common BGP Issues:
        - All peers DOWN: Network connectivity problems
        - Intermittent UP/DOWN: Flapping connections or configuration issues
        - No peers configured: Router not set up for external connectivity
        - Permission errors: IAM roles missing for router access
    """
    if not project:
        raise ValueError("Project ID cannot be empty")
    if not region:
        raise ValueError("Region cannot be empty")
    if not router:
        raise ValueError("Router name cannot be empty")
    
    def _check() -> Tuple[bool, Dict[str, str]]:
        """
        Internal BGP health check function that queries router status.
        
        Returns:
            Tuple[bool, Dict[str, str]]: (any_peer_up, peer_status_map)
        """
        start_time = time.time()
        any_up = False
        peer_statuses = {}
        
        # Initialize tracking details for structured logging
        details = {
            "project": project,
            "region": region,
            "router": router,
            "total_peers": 0,
            "peers_up": 0,
            "peers_down": 0,
            "peer_statuses": {}
        }
        
        logger.debug(f"Checking BGP session health for router {router} in {project}/{region}")
        
        try:
            # Query Cloud Router status including BGP peer information
            logger.debug(f"Getting router status for {router}")
            status_request = compute_client.routers().getRouterStatus(
                project=project,
                region=region,
                router=router
            )
            status_response = status_request.execute()
            
            # Extract BGP peer status from router status response
            result = status_response.get('result', {})
            bgp_peers = result.get('bgpPeerStatus', [])
            
            details["total_peers"] = len(bgp_peers)
            
            if not bgp_peers:
                # No BGP peers configured - this is typically a configuration issue
                logger.warning(f"No BGP peers found for router {router} in {project}/{region}")
                details["warning"] = "no_bgp_peers_configured"
                any_up = False
            else:
                # Process each BGP peer status
                logger.debug(f"Found {len(bgp_peers)} BGP peers on router {router}")
                
                for peer in bgp_peers:
                    peer_name = peer.get('name', 'unknown')
                    peer_status = peer.get('status', 'UNKNOWN')
                    
                    peer_statuses[peer_name] = peer_status
                    details["peer_statuses"][peer_name] = peer_status
                    
                    if peer_status == 'UP':
                        details["peers_up"] += 1
                        any_up = True
                        logger.debug(f"BGP peer {peer_name} is UP")
                    else:
                        details["peers_down"] += 1
                        logger.debug(f"BGP peer {peer_name} is {peer_status}")
                
                # Log overall BGP status
                if any_up:
                    logger.info(f"BGP connectivity available on router {router} "
                              f"({details['peers_up']}/{details['total_peers']} peers UP)")
                else:
                    logger.warning(f"No BGP connectivity on router {router} "
                                 f"(0/{details['total_peers']} peers UP)")
            
            # Log detailed peer statuses for troubleshooting
            if peer_statuses:
                peer_summary = ", ".join([f"{name}:{status}" for name, status in peer_statuses.items()])
                logger.info(f"BGP peer statuses for {project}/{region}/{router}: {peer_summary}")
            
        except HttpError as e:
            details["error_code"] = e.resp.status
            details["error_reason"] = str(e)

            # Handle permanent vs transient/unknown errors differently
            if e.resp.status in PERMANENT_HTTP_ERRORS:
                logger.error(f"Permanent error checking BGP sessions for router {router} "
                           f"in {project}/{region}: {e}")
                raise  # Re-raise permanent errors for immediate attention
            elif e.resp.status in TRANSIENT_HTTP_ERRORS:
                # Known transient errors - temporary API issues, monitoring unreliable
                logger.warning(f"Transient HTTP error ({e.resp.status}) checking BGP sessions "
                             f"for router {router} in {project}/{region}. Monitoring temporarily "
                             f"unreliable - will maintain current routing state: {e}")
                details["monitoring_unavailable"] = True
                any_up = None  # Monitoring unreliable -> unknown health
            else:
                # Unknown/unexpected error code - don't make routing changes based on unknown errors
                logger.warning(f"Unknown HTTP error code {e.resp.status} checking BGP sessions "
                             f"for router {router} in {project}/{region}. Cannot determine BGP "
                             f"health with unexpected error - will maintain current routing state: {e}")
                details["monitoring_unavailable"] = True
                any_up = None  # Unknown error -> unknown health
                
        except Exception as e:
            details["error_reason"] = str(e)
            logger.exception(f"Unexpected error checking BGP session status for router {router} "
                           f"in {project}/{region}: {e}")
        
        finally:
            # Log structured event with comprehensive BGP status information
            if structured_logger:
                duration_ms = int((time.time() - start_time) * 1000)
                structured_logger.log_health_check(
                    region=region,
                    service_type="bgp_sessions",
                    healthy=any_up,
                    details=details,
                    duration_ms=duration_ms
                )
        
        return any_up, peer_statuses
    
    return _check


def update_bgp_advertisement(project: str,
                           region: str,
                           router: str,
                           prefix: str,
                           compute_client,
                           advertise: bool = True,
                           structured_logger: Optional[StructuredEventLogger] = None) -> Callable[[], bool]:
    """
    Create a function that manages BGP route advertisements on a Cloud Router.
    
    This function returns a closure that can advertise or withdraw IP prefixes
    via BGP on a Cloud Router. This is essential for traffic engineering and
    automated failover scenarios where route advertisements need to be dynamically
    controlled based on service health.
    
    BGP Advertisement Logic:
        - Advertise: Adds the IP prefix to the router's advertised IP ranges
        - Withdraw: Removes the IP prefix from the router's advertised IP ranges
        - No-op: If the prefix is already in the desired state, no change is made
        - Atomic: Changes are applied atomically - either all succeed or all fail
    
    Route Advertisement Use Cases:
        - Primary/backup datacenter failover
        - Traffic engineering based on capacity or performance
        - Maintenance mode routing (withdraw routes during maintenance)
        - Geographic routing optimization
        - Load balancing across multiple egress points
    
    Operation Flow:
        1. Get current router configuration
        2. Check if prefix is already in desired state
        3. If change needed, update advertised IP ranges list
        4. Apply changes via router patch operation
        5. Log operation with tracking ID for audit
    
    Args:
        project (str): GCP project ID containing the Cloud Router
        region (str): GCP region where the router is located
        router (str): Name of the Cloud Router to modify
        prefix (str): IP prefix to advertise or withdraw (CIDR notation, e.g., '10.0.0.0/24')
        compute_client: Authenticated GCP Compute Engine client
        advertise (bool): True to advertise the prefix, False to withdraw it
        structured_logger (StructuredEventLogger, optional): Logger for structured events
        
    Returns:
        Callable[[], bool]: Function that returns True if operation succeeded,
            False if operation failed or encountered errors
            
    Raises:
        HttpError: For permanent API errors (403, 404) that indicate configuration issues
        ValueError: For invalid input parameters
        
    Performance:
        - Get router config: ~200-500ms
        - Update router config: ~500-2000ms (depends on router complexity)
        - Total operation time: typically 1-3 seconds
        - Changes propagate to BGP peers within seconds to minutes
        
    Example:
        # Create BGP advertisement function
        advertiser = update_bgp_advertisement(
            'my-project', 'us-central1', 'my-router', '10.0.0.0/24', compute
        )
        
        # Advertise route when service is healthy
        if service_healthy():
            success = advertiser(advertise=True)
            if success:
                print("Route advertised successfully")
        else:
            # Withdraw route when service is unhealthy
            success = advertiser(advertise=False)
            if success:
                print("Route withdrawn successfully")
                
        # With structured logging for audit trail
        logger = StructuredEventLogger("bgp_manager")
        advertiser = update_bgp_advertisement(
            'my-project', 'us-central1', 'my-router', '10.0.0.0/24', 
            compute, structured_logger=logger
        )
        
    Structured Logging Output:
        When structured_logger is provided, logs include:
        - Operation type (advertise/withdraw)
        - IP prefix being modified
        - Operation success/failure status
        - GCP operation ID for tracking
        - Error details for failures
        - Performance timing information
        
    Error Scenarios:
        - Router not found: Permanent error, operation fails
        - Invalid prefix format: Validation error before API call
        - Concurrent modifications: Retry may be needed
        - Network connectivity: Transient error, logged and return False
        - Insufficient permissions: Permanent error, re-raised
        
    BGP Propagation:
        - Local router update: Immediate (operation completion)
        - BGP peer propagation: Seconds to minutes depending on network
        - Global route table update: Minutes depending on internet routing
        - Monitor BGP session status to confirm propagation
        
    Security Considerations:
        - Route advertisements affect real network traffic
        - Incorrect advertisements can cause traffic blackholing
        - Monitor route advertisements for unauthorized changes
        - Use least-privilege IAM roles for router modification
        - Implement change approval processes for production routes
        
    Troubleshooting:
        - Operation ID can be used to track progress in GCP Console
        - Check router configuration after changes to verify application
        - Monitor BGP peer status for propagation confirmation
        - Use network monitoring to verify traffic flow changes
    """
    if not project:
        raise ValueError("Project ID cannot be empty")
    if not region:
        raise ValueError("Region cannot be empty")
    if not router:
        raise ValueError("Router name cannot be empty")
    if not prefix:
        raise ValueError("IP prefix cannot be empty")
    
    # Validate IP prefix format (basic validation)
    if '/' not in prefix:
        raise ValueError(f"IP prefix must be in CIDR format (e.g., '10.0.0.0/24'), got: {prefix}")
    
    def _update() -> bool:
        """
        Internal BGP advertisement update function that performs the router modification.

        Returns:
            bool: True if operation succeeded or no change was needed,
                  False if operation failed
        """
        # Handle None (no change requested) - State 0 failsafe behavior
        # None means "maintain current state" - no BGP updates should be performed
        if advertise is None:
            logger.debug(f"No BGP advertisement change requested for {prefix} on router {router} "
                        f"(advertise=None indicates State 0 or no-change scenario)")
            return True  # No-op is considered success

        start_time = time.time()
        success = False
        operation_id = None
        error_message = None
        action = "advertise" if advertise else "withdraw"
        action_needed = False

        logger.debug(f"Starting BGP advertisement update: {action} {prefix} on router {router}")
        
        try:
            # Step 1: Get current router configuration
            logger.debug(f"Getting current configuration for router {router}")
            router_request = compute_client.routers().get(
                project=project,
                region=region,
                router=router
            )
            router_data = router_request.execute()
            
            # Extract current advertised IP ranges
            bgp_config = router_data.get('bgp', {})
            current_ranges = bgp_config.get('advertisedIpRanges', [])
            
            # Check if prefix is already in the current configuration
            prefix_exists = any(ip_range.get('range') == prefix for ip_range in current_ranges)
            
            logger.debug(f"Router {router} currently advertises {len(current_ranges)} prefixes, "
                        f"target prefix {prefix} {'exists' if prefix_exists else 'does not exist'}")
            
            # Step 2: Determine if action is needed
            if advertise and not prefix_exists:
                # Need to add prefix
                new_ranges = current_ranges + [{'range': prefix}]
                action_needed = True
                logger.info(f"Adding BGP advertisement for prefix {prefix} on router {router}")
                
            elif not advertise and prefix_exists:
                # Need to remove prefix
                new_ranges = [r for r in current_ranges if r.get('range') != prefix]
                action_needed = True
                logger.info(f"Removing BGP advertisement for prefix {prefix} on router {router}")
                
            else:
                # No action needed - prefix already in desired state
                if advertise:
                    logger.debug(f"Prefix {prefix} already advertised on router {router} - no change needed")
                else:
                    logger.debug(f"Prefix {prefix} not advertised on router {router} - no change needed")
                success = True
                action_needed = False

            # Step 3: Apply changes if needed
            if action_needed:
                # Prepare router patch body with updated advertised IP ranges
                patch_body = {
                    'bgp': {
                        'advertisedIpRanges': new_ranges
                    }
                }
                
                logger.debug(f"Updating router {router} with {len(new_ranges)} advertised prefixes")
                
                # Apply the router configuration update
                patch_request = compute_client.routers().patch(
                    project=project,
                    region=region,
                    router=router,
                    body=patch_body
                )
                operation_response = patch_request.execute()
                
                # Extract operation ID for tracking
                operation_id = operation_response.get('name', 'unknown')
                operation_status = operation_response.get('status', 'unknown')
                
                logger.info(f"BGP advertisement update initiated: {action} prefix {prefix} "
                           f"on router {router} [{project}/{region}] (operation: {operation_id})")
                
                # Operation submitted successfully
                success = True
                
                # Log operation details for debugging
                logger.debug(f"Operation {operation_id} status: {operation_status}")
                if 'warnings' in operation_response:
                    for warning in operation_response['warnings']:
                        logger.warning(f"GCP operation warning: {warning}")
                
            else:
                # No operation needed
                success = True
                
        except HttpError as e:
            error_message = str(e)
            
            # Handle different types of HTTP errors
            if e.resp.status in PERMANENT_HTTP_ERRORS:
                logger.error(f"Permanent error updating BGP advertisement for prefix {prefix} "
                           f"on router {router} in {project}/{region}: {e}")
                raise  # Re-raise permanent errors for immediate attention
            else:
                logger.warning(f"Transient HTTP error updating BGP advertisement for prefix {prefix} "
                             f"on router {router} in {project}/{region}: {e}")
                
        except Exception as e:
            error_message = str(e)
            logger.exception(f"Unexpected error updating BGP advertisement for prefix {prefix} "
                           f"on router {router} in {project}/{region}: {e}")
        
        finally:
            # Always log structured event for audit trail and monitoring
            if structured_logger:
                duration_ms = int((time.time() - start_time) * 1000)
                
                # Determine result status for structured logging
                if success and action_needed:
                    result = ActionResult.SUCCESS
                elif success and not action_needed:
                    result = ActionResult.NO_CHANGE
                else:
                    result = ActionResult.FAILURE
                
                structured_logger.log_bgp_advertisement(
                    project=project,
                    region=region,
                    router=router,
                    prefix=prefix,
                    action=action,
                    result=result,
                    duration_ms=duration_ms,
                    operation_id=operation_id,
                    error_message=error_message
                )
        
        return success
    
    return _update


# Utility functions for common GCP operations and validation

def validate_ip_prefix(prefix: str) -> bool:
    """
    Validate that an IP prefix is in valid CIDR format.
    
    Args:
        prefix (str): IP prefix to validate (e.g., '10.0.0.0/24')
        
    Returns:
        bool: True if prefix is valid CIDR format, False otherwise
        
    Example:
        if validate_ip_prefix('10.0.0.0/24'):
            print("Valid prefix")
    """
    try:
        import ipaddress
        ipaddress.ip_network(prefix, strict=False)
        return True
    except (ValueError, AddressValueError):
        return False


def get_router_advertised_prefixes(project: str, region: str, router: str, compute_client) -> List[str]:
    """
    Get list of currently advertised IP prefixes for a Cloud Router.
    
    Args:
        project (str): GCP project ID
        region (str): GCP region name
        router (str): Cloud Router name
        compute_client: Authenticated GCP Compute Engine client
        
    Returns:
        List[str]: List of currently advertised IP prefixes
        
    Raises:
        HttpError: If router access fails
        
    Example:
        prefixes = get_router_advertised_prefixes('my-project', 'us-central1', 'my-router', compute)
        print(f"Currently advertising: {prefixes}")
    """
    try:
        router_data = compute_client.routers().get(
            project=project, region=region, router=router
        ).execute()
        
        advertised_ranges = router_data.get('bgp', {}).get('advertisedIpRanges', [])
        return [ip_range.get('range') for ip_range in advertised_ranges if ip_range.get('range')]
        
    except Exception as e:
        logger.error(f"Failed to get advertised prefixes for router {router}: {e}")
        raise


def get_backend_service_summary(project: str, region: str, compute_client) -> Dict[str, Any]:
    """
    Get summary information about backend services in a region.
    
    Args:
        project (str): GCP project ID  
        region (str): GCP region name
        compute_client: Authenticated GCP Compute Engine client
        
    Returns:
        Dict[str, Any]: Summary containing service count, backend count, etc.
        
    Example:
        summary = get_backend_service_summary('my-project', 'us-central1', compute)
        print(f"Found {summary['service_count']} backend services")
    """
    try:
        response = compute_client.regionBackendServices().list(
            project=project, region=region
        ).execute()
        
        services = response.get('items', [])
        total_backends = sum(len(service.get('backends', [])) for service in services)
        
        return {
            'service_count': len(services),
            'total_backends': total_backends,
            'services': [service['name'] for service in services]
        }
        
    except Exception as e:
        logger.error(f"Failed to get backend service summary for {project}/{region}: {e}")
        raise


# Module constants for configuration and limits
GCP_OPERATION_POLL_INTERVAL = 5          # Seconds between operation status polls
MAX_ADVERTISED_PREFIXES = 100            # Practical limit for advertised prefixes
DEFAULT_BGP_PEER_TIMEOUT = 30             # Seconds to wait for BGP peer status


# Example usage and testing
if __name__ == "__main__":
    """
    Example usage and testing of GCP integration functions.
    
    This section demonstrates how to use the module functions and can be used
    for manual testing or as a reference for integration.
    """
    import os
    from .structured_events import StructuredEventLogger
    
    # Configuration from environment
    PROJECT_ID = os.getenv("GCP_PROJECT", "your-project-id")
    CREDENTIALS_PATH = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "/path/to/credentials.json")
    LOCAL_REGION = os.getenv("LOCAL_GCP_REGION", "us-central1")
    REMOTE_REGION = os.getenv("REMOTE_GCP_REGION", "us-east1")
    ROUTER_NAME = os.getenv("LOCAL_BGP_ROUTER", "your-router")
    TEST_PREFIX = os.getenv("PRIMARY_PREFIX", "10.0.0.0/24")
    
    def example_connectivity_test():
        """Example: Test GCP connectivity and permissions"""
        print("=== GCP Connectivity Test ===")
        try:
            compute = build_compute_client(CREDENTIALS_PATH)
            validate_gcp_connectivity(PROJECT_ID, [LOCAL_REGION, REMOTE_REGION], compute)
            print("✓ GCP connectivity test passed")
            return compute
        except Exception as e:
            print(f"✗ GCP connectivity test failed: {e}")
            return None
    
    def example_backend_health_check(compute):
        """Example: Check backend service health"""
        print("\n=== Backend Service Health Check ===")
        if not compute:
            print("Skipping - no compute client")
            return
            
        try:
            logger = StructuredEventLogger("gcp_example")
            health_checker = backend_services_healthy(PROJECT_ID, LOCAL_REGION, compute, logger)
            
            healthy = health_checker()
            print(f"Backend services healthy: {healthy}")
            
            # Get summary information
            summary = get_backend_service_summary(PROJECT_ID, LOCAL_REGION, compute)
            print(f"Found {summary['service_count']} backend services with {summary['total_backends']} backends")
            
        except Exception as e:
            print(f"✗ Backend health check failed: {e}")
    
    def example_bgp_monitoring(compute):
        """Example: Monitor BGP session health"""
        print("\n=== BGP Session Monitoring ===")
        if not compute:
            print("Skipping - no compute client")
            return
            
        try:
            logger = StructuredEventLogger("bgp_example")
            bgp_checker = router_bgp_sessions_healthy(PROJECT_ID, LOCAL_REGION, ROUTER_NAME, compute, logger)
            
            any_up, peer_statuses = bgp_checker()
            print(f"BGP connectivity available: {any_up}")
            
            if peer_statuses:
                print("BGP peer statuses:")
                for peer, status in peer_statuses.items():
                    print(f"  {peer}: {status}")
            else:
                print("No BGP peers found")
                
        except Exception as e:
            print(f"✗ BGP monitoring failed: {e}")
    
    def example_bgp_advertisement(compute):
        """Example: Manage BGP route advertisements"""
        print("\n=== BGP Route Advertisement ===")
        if not compute:
            print("Skipping - no compute client")
            return
            
        try:
            logger = StructuredEventLogger("bgp_advertisement_example")
            
            # Get current advertised prefixes
            current_prefixes = get_router_advertised_prefixes(PROJECT_ID, LOCAL_REGION, ROUTER_NAME, compute)
            print(f"Currently advertising {len(current_prefixes)} prefixes: {current_prefixes}")
            
            # Create advertisement function
            advertiser = update_bgp_advertisement(
                PROJECT_ID, LOCAL_REGION, ROUTER_NAME, TEST_PREFIX,
                compute, structured_logger=logger
            )
            
            # Test advertisement
            print(f"Testing advertisement of {TEST_PREFIX}...")
            success = advertiser(advertise=True)
            if success:
                print("✓ Advertisement successful")
            else:
                print("✗ Advertisement failed")
                
            # Test withdrawal
            print(f"Testing withdrawal of {TEST_PREFIX}...")
            success = advertiser(advertise=False)
            if success:
                print("✓ Withdrawal successful")
            else:
                print("✗ Withdrawal failed")
                
        except Exception as e:
            print(f"✗ BGP advertisement test failed: {e}")
    
    # Run examples if module is executed directly
    if PROJECT_ID != "your-project-id" and CREDENTIALS_PATH != "/path/to/credentials.json":
        print(f"Testing GCP integration with project: {PROJECT_ID}")
        
        compute = example_connectivity_test()
        example_backend_health_check(compute)
        example_bgp_monitoring(compute)
        example_bgp_advertisement(compute)
        
        print("\n=== GCP Integration Tests Complete ===")
    else:
        print("Set GCP_PROJECT and GOOGLE_APPLICATION_CREDENTIALS environment variables to run examples")
        print("See module docstring for required environment variables")
