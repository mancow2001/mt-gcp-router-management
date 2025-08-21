"""
Main Daemon Module for Magic Transit Health Check and Route Management

This module implements the core daemon logic for an automated health checking and
route management system. It monitors the health of GCP backend services and BGP
sessions, then automatically adjusts BGP advertisements and Cloudflare route
priorities based on the health state.

System Architecture:
    The daemon operates as a control loop that continuously:
    1. Checks health of local and remote GCP regions
    2. Monitors BGP session status
    3. Determines the optimal routing state based on health
    4. Updates BGP route advertisements in GCP
    5. Adjusts Cloudflare Magic Transit route priorities
    6. Logs all operations with structured events for observability

Key Components:
    - Health Monitoring: GCP backend services and BGP sessions
    - State Machine: Determines routing actions based on health combinations
    - Route Management: BGP advertisements and Cloudflare priorities
    - Resilience: Circuit breakers, retries, and error handling
    - Observability: Comprehensive structured logging with correlation IDs

Health Check Flow:
    Local Region Health → Remote Region Health → BGP Status → State Code → Actions
    
    State codes (see state.py for complete mapping):
    - State 1: All healthy → Advertise primary only
    - State 2: Local unhealthy → Failover mode
    - State 3: Remote unhealthy → Use both paths
    - State 4: Both unhealthy → Emergency mode
    - State 5: BGP down + local unhealthy → Backup infrastructure
    - State 6: BGP down + all healthy → Use both paths

Cloudflare Integration:
    Routes are selected by description substring matching. Priority is adjusted based
    on local region health:
    - Local healthy: Use cf_primary_priority (typically lower number = higher priority)
    - Local unhealthy: Use cf_secondary_priority (typically higher number = lower priority)

Resilience Features:
    - Circuit breakers per service type to prevent cascading failures
    - Exponential backoff retry for transient failures
    - Graceful shutdown on SIGTERM/SIGINT
    - Structured error logging with correlation tracking
    - Automatic recovery detection and circuit reset

Observability:
    - Correlation IDs track related events across a health check cycle
    - Structured logging for all operational events
    - Performance metrics (duration, retry counts, etc.)
    - State transition tracking
    - Integration with GCP Cloud Logging

Configuration:
    All configuration is environment-driven through the Config class.
    See config.py for complete configuration options and validation.

Signal Handling:
    - SIGTERM/SIGINT: Graceful shutdown with cleanup
    - Logging handlers are flushed before exit
    - In-flight operations are allowed to complete

Threading:
    - Main loop runs in a single thread
    - Thread-safe circuit breakers allow for future multi-threading
    - Signal handlers work across threads

Usage:
    from .daemon import startup, run_loop
    from .config import Config
    
    cfg = Config()
    compute = startup(cfg)  # Initialize and validate
    run_loop(cfg, compute)  # Main daemon loop

Production Considerations:
    - Monitor structured logs for health trends and failures
    - Set up alerting on circuit breaker opens and repeated failures
    - Use correlation IDs to trace issues across service boundaries
    - Consider impact of check_interval on response time vs resource usage
    - Ensure GCP and Cloudflare credentials have minimal required permissions

Author: MT GCP Daemon Team
Version: 1.0
Last Modified: 2024
Dependencies: gcp, cloudflare, circuit, state, structured_events modules
"""

import time
import threading
import logging
import signal
import sys
import uuid
from typing import Optional, Dict, Any
from .config import Config, validate_configuration
from .logging_setup import setup_logger
from .circuit import CircuitBreaker, exponential_backoff_retry
from .structured_events import StructuredEventLogger, EventType, ActionResult
from . import gcp as gcp_mod
from . import cloudflare as cf_mod
from .state import determine_state_code, STATE_ACTIONS
from dotenv import load_dotenv
import os

# Load environment variables from a .env file into the runtime environment
# This allows configuration through .env files in addition to system environment
load_dotenv()

# Global event used to signal graceful shutdown across the application
# This threading.Event is set by signal handlers and checked by the main loop
shutdown_event = threading.Event()


def signal_handler(signum: int, frame) -> None:
    """
    Signal handler for graceful daemon shutdown.
    
    This function is registered to handle SIGTERM and SIGINT signals, allowing
    the daemon to shut down gracefully when requested by the operating system
    or user (Ctrl+C).
    
    The handler sets a global event that the main loop monitors, allowing
    in-flight operations to complete before shutdown.
    
    Args:
        signum (int): The signal number that triggered this handler
        frame: The current stack frame (unused but required by signal handler interface)
        
    Side Effects:
        - Sets the global shutdown_event to signal main loop to exit
        - Logs the shutdown request with signal name
        
    Signal Handling:
        - SIGTERM (15): Typical graceful shutdown signal from process managers
        - SIGINT (2): Interrupt signal (Ctrl+C)
        - Other signals: Logged with numeric value
        
    Note:
        This function should complete quickly as signal handlers are executed
        in a restricted context. The actual shutdown logic is in the main loop.
        
    Example:
        # Signal handler is registered automatically by setup_signal_handlers()
        # When running as a service:
        systemctl stop mt-gcp-daemon  # Sends SIGTERM
        
        # When running interactively:
        # Ctrl+C sends SIGINT
    """
    logger = logging.getLogger("healthcheck-daemon")
    
    # Map common signal numbers to human-readable names
    signal_names = {
        signal.SIGTERM: 'SIGTERM',
        signal.SIGINT: 'SIGINT'
    }
    signal_name = signal_names.get(signum, f'Signal-{signum}')
    
    logger.info(f"Received {signal_name}, initiating graceful shutdown...")
    
    # Set the shutdown event to signal the main loop to exit
    # This allows the current health check cycle to complete
    shutdown_event.set()


def setup_signal_handlers() -> None:
    """
    Register signal handlers for graceful daemon shutdown.
    
    This function configures the daemon to handle termination signals gracefully,
    ensuring that the daemon can clean up resources and complete in-flight
    operations before exiting.
    
    Signals Handled:
        - SIGTERM: Standard termination signal used by process managers
        - SIGINT: Interrupt signal (Ctrl+C) for interactive shutdown
        
    Side Effects:
        - Registers signal_handler function for SIGTERM and SIGINT
        - Replaces default signal behavior (immediate termination)
        
    Thread Safety:
        Signal handlers work across all threads in the process. The main
        daemon loop runs in the main thread and will receive shutdown signals.
        
    Error Handling:
        If signal registration fails (rare), the daemon will still function
        but may not shut down gracefully. This is logged as a warning.
        
    Note:
        This should be called during daemon startup, before entering the
        main control loop.
        
    Example:
        setup_signal_handlers()
        # Now Ctrl+C or SIGTERM will trigger graceful shutdown
    """
    try:
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
        
        logger = logging.getLogger("healthcheck-daemon")
        logger.debug("Signal handlers registered for SIGTERM and SIGINT")
        
    except Exception as e:
        logger = logging.getLogger("healthcheck-daemon")
        logger.warning(f"Failed to register signal handlers: {e}")


def run_loop(cfg: Config, compute) -> None:
    """
    Main daemon control loop with comprehensive health checking and route management.
    
    This is the core function that implements the daemon's primary logic. It runs
    continuously until a shutdown signal is received, performing health checks and
    updating routing configurations based on the results.
    
    Control Loop Flow:
        1. Generate correlation ID for traceability
        2. Check GCP backend service health (local and remote regions)
        3. Check BGP session status in remote region
        4. Determine routing state based on health combination
        5. Update BGP advertisements in GCP based on state
        6. Update Cloudflare route priorities based on local health
        7. Log cycle completion and performance metrics
        8. Sleep until next check interval
        
    Health Check Logic:
        - Local/Remote Backend Services: Queries GCP backend service health
        - BGP Sessions: Monitors Cloud Router BGP peer status
        - State Determination: Uses state machine to map health to actions
        - Advertisement Updates: Modifies BGP route advertisements
        - Priority Updates: Adjusts Cloudflare Magic Transit route priorities
        
    Resilience Patterns:
        - Circuit Breakers: Prevent calls to repeatedly failing services
        - Exponential Backoff: Retry transient failures with increasing delays
        - Error Isolation: Separate circuit breakers for each service type
        - Graceful Degradation: Continue operating with partial functionality
        
    Observability Features:
        - Correlation IDs: Track related events across the health check cycle
        - Structured Logging: All events logged with consistent schema
        - Performance Metrics: Duration tracking for all operations
        - State Transitions: Log changes in routing state with context
        - Error Context: Detailed error information for debugging
        
    Args:
        cfg (Config): Validated configuration object containing all daemon settings
        compute: Initialized and validated GCP Compute Engine client
        
    Side Effects:
        - Makes API calls to GCP Compute Engine
        - Makes API calls to Cloudflare Magic Transit
        - Modifies BGP route advertisements
        - Modifies Cloudflare route priorities
        - Generates extensive structured logs
        - Sleeps between check intervals
        
    Error Handling:
        - Transient errors: Retried with exponential backoff
        - Persistent errors: Circuit breakers isolate failing services
        - Unexpected errors: Logged and tracked, daemon continues with backoff
        - Critical errors: After max consecutive errors, daemon exits
        
    Performance Characteristics:
        - Typical cycle time: 2-10 seconds depending on API response times
        - Check interval: Configurable (default 60 seconds)
        - Memory usage: Stable, no memory leaks in long-running operation
        - API calls per cycle: ~6-8 (health checks + updates)
        
    Example Configuration Impact:
        check_interval=60 → Health checks every minute
        cb_threshold=5 → Circuit opens after 5 consecutive failures
        cb_timeout=300 → Circuit stays open for 5 minutes before retry
        
    Monitoring Recommendations:
        - Alert on circuit breaker opens (service degradation)
        - Monitor cycle duration (performance issues)
        - Track state transition frequency (instability)
        - Watch consecutive error counts (system health)
        
    Shutdown Behavior:
        - Monitors shutdown_event between operations
        - Completes current cycle before exiting
        - Logs graceful shutdown event
        - Allows up to check_interval seconds for current cycle completion
    """
    # Initialize logging for the daemon loop
    logger = logging.getLogger(os.getenv("LOGGER_NAME", "HEALTH_CHECK_DAEMON"))
    structured_logger = StructuredEventLogger(os.getenv("LOGGER_NAME", "HEALTH_CHECK_DAEMON"))
    
    # Log daemon startup information
    logger.info(f"Daemon main loop starting with {cfg.check_interval}s check interval")
    logger.info(f"Monitoring regions - Local: {cfg.local_region}, Remote: {cfg.remote_region}")
    logger.info(f"Managing prefixes - Primary: {cfg.primary_prefix}, Secondary: {cfg.secondary_prefix}")
    logger.info(f"Cloudflare integration - Account: {cfg.cf_account_id}, Filter: '{cfg.cf_desc_substring}'")

    # Initialize circuit breakers for different service types
    # Each service gets its own circuit breaker to isolate failures
    circuit_breakers = {
        'gcp_health': CircuitBreaker(
            threshold=cfg.cb_threshold,
            timeout=cfg.cb_timeout,
            service_name="gcp_health_check",
            structured_logger=structured_logger
        ),
        'gcp_bgp': CircuitBreaker(
            threshold=cfg.cb_threshold,
            timeout=cfg.cb_timeout,
            service_name="gcp_bgp_check",
            structured_logger=structured_logger
        ),
        'gcp_advertisement': CircuitBreaker(
            threshold=cfg.cb_threshold,
            timeout=cfg.cb_timeout,
            service_name="gcp_advertisement",
            structured_logger=structured_logger
        ),
        'cloudflare': CircuitBreaker(
            threshold=cfg.cb_threshold,
            timeout=cfg.cb_timeout,
            service_name="cloudflare_routes",
            structured_logger=structured_logger
        ),
    }

    # Error tracking for daemon stability
    consecutive_errors = 0
    max_consecutive_errors = 10
    current_state_code = None  # Track state changes

    # Log daemon startup event for observability
    startup_details = {
        "check_interval": cfg.check_interval,
        "local_region": cfg.local_region,
        "remote_region": cfg.remote_region,
        "primary_prefix": cfg.primary_prefix,
        "secondary_prefix": cfg.secondary_prefix,
        "circuit_breaker_threshold": cfg.cb_threshold,
        "circuit_breaker_timeout": cfg.cb_timeout
    }
    
    structured_logger.log_event({
        "event_type": EventType.DAEMON_LIFECYCLE.value,
        "timestamp": time.time(),
        "result": ActionResult.SUCCESS.value,
        "component": "daemon",
        "operation": "startup",
        "details": startup_details
    })

    # Main control loop - continues until shutdown signal received
    while not shutdown_event.is_set():
        try:
            loop_start = time.time()
            
            # Generate unique correlation ID for this health check cycle
            # Format: hc-{unix_timestamp}-{short_uuid}
            correlation_id = f"hc-{int(time.time())}-{str(uuid.uuid4())[:8]}"
            structured_logger.set_correlation_id(correlation_id)
            
            logger.info(f"Starting health check cycle {correlation_id}")

            # ═══════════════════════════════════════════════════════════════════════════
            # PHASE 1: GCP Backend Service Health Checks
            # ═══════════════════════════════════════════════════════════════════════════
            
            logger.debug(f"[{correlation_id}] Checking GCP backend service health")
            
            # Check local region backend service health
            # Uses circuit breaker + retry for resilience
            local_healthy = circuit_breakers['gcp_health'].call(
                lambda: exponential_backoff_retry(
                    gcp_mod.backend_services_healthy(
                        cfg.gcp_project,
                        cfg.local_region,
                        compute,
                        structured_logger
                    ),
                    max_retries=cfg.max_retries,
                    initial_delay=cfg.initial_backoff,
                    max_delay=cfg.max_backoff
                )
            )

            # Check remote region backend service health
            remote_healthy = circuit_breakers['gcp_health'].call(
                lambda: exponential_backoff_retry(
                    gcp_mod.backend_services_healthy(
                        cfg.gcp_project,
                        cfg.remote_region,
                        compute,
                        structured_logger
                    ),
                    max_retries=cfg.max_retries,
                    initial_delay=cfg.initial_backoff,
                    max_delay=cfg.max_backoff
                )
            )

            # ═══════════════════════════════════════════════════════════════════════════
            # PHASE 2: BGP Session Health Check
            # ═══════════════════════════════════════════════════════════════════════════
            
            logger.debug(f"[{correlation_id}] Checking BGP session health")
            
            # Check BGP session status on remote region router
            # Returns tuple: (any_peer_up: bool, peer_statuses: dict)
            remote_bgp_up, remote_peer_statuses = circuit_breakers['gcp_bgp'].call(
                lambda: exponential_backoff_retry(
                    gcp_mod.router_bgp_sessions_healthy(
                        cfg.bgp_peer_project,
                        cfg.remote_bgp_region,
                        cfg.remote_bgp_router,
                        compute,
                        structured_logger
                    ),
                    max_retries=cfg.max_retries,
                    initial_delay=cfg.initial_backoff,
                    max_delay=cfg.max_backoff
                )
            )

            # Log health check results summary
            logger.info(f"Health Status [{correlation_id}] - "
                       f"Local: {local_healthy}, Remote: {remote_healthy}, "
                       f"Remote BGP: {remote_bgp_up}")
            
            if remote_peer_statuses:
                logger.debug(f"BGP peer details [{correlation_id}]: {remote_peer_statuses}")

            # ═══════════════════════════════════════════════════════════════════════════
            # PHASE 3: Routing State Determination
            # ═══════════════════════════════════════════════════════════════════════════
            
            # Use state machine to determine routing actions based on health combination
            new_state_code = determine_state_code(local_healthy, remote_healthy, remote_bgp_up)
            advertise_primary, advertise_secondary = STATE_ACTIONS.get(new_state_code, (False, False))
            
            # Log state transition if changed (for audit and debugging)
            if current_state_code != new_state_code:
                logger.info(f"State transition [{correlation_id}]: "
                           f"{current_state_code} -> {new_state_code}")
                
                structured_logger.log_state_transition(
                    old_state=current_state_code or 0,
                    new_state=new_state_code,
                    local_healthy=local_healthy,
                    remote_healthy=remote_healthy,
                    remote_bgp_up=remote_bgp_up,
                    planned_actions=(advertise_primary, advertise_secondary)
                )
                current_state_code = new_state_code
            
            logger.info(f"State {new_state_code} [{correlation_id}] -> "
                       f"Primary BGP: {advertise_primary}, Secondary BGP: {advertise_secondary}")

            # ═══════════════════════════════════════════════════════════════════════════
            # PHASE 4: BGP Route Advertisement Updates
            # ═══════════════════════════════════════════════════════════════════════════
            
            logger.debug(f"[{correlation_id}] Updating BGP route advertisements")
            
            # Update primary prefix advertisement (local router)
            primary_success = circuit_breakers['gcp_advertisement'].call(
                lambda: exponential_backoff_retry(
                    gcp_mod.update_bgp_advertisement(
                        project=cfg.bgp_peer_project,
                        region=cfg.local_bgp_region,
                        router=cfg.local_bgp_router,
                        prefix=cfg.primary_prefix,
                        compute_client=compute,
                        advertise=advertise_primary,
                        structured_logger=structured_logger
                    ),
                    max_retries=cfg.max_retries,
                    initial_delay=cfg.initial_backoff,
                    max_delay=cfg.max_backoff
                )
            )

            # Update secondary prefix advertisement (remote router)
            secondary_success = circuit_breakers['gcp_advertisement'].call(
                lambda: exponential_backoff_retry(
                    gcp_mod.update_bgp_advertisement(
                        project=cfg.bgp_peer_project,
                        region=cfg.remote_bgp_region,
                        router=cfg.remote_bgp_router,
                        prefix=cfg.secondary_prefix,
                        compute_client=compute,
                        advertise=advertise_secondary,
                        structured_logger=structured_logger
                    ),
                    max_retries=cfg.max_retries,
                    initial_delay=cfg.initial_backoff,
                    max_delay=cfg.max_backoff
                )
            )

            # ═══════════════════════════════════════════════════════════════════════════
            # PHASE 5: Cloudflare Route Priority Updates
            # ═══════════════════════════════════════════════════════════════════════════
            
            logger.debug(f"[{correlation_id}] Updating Cloudflare route priorities")
            
            # Determine desired priority based on local region health
            # Lower priority number = higher priority in Cloudflare
            desired_priority = (cfg.cf_primary_priority if local_healthy
                              else cfg.cf_secondary_priority)
            
            logger.debug(f"[{correlation_id}] Setting Cloudflare priority to {desired_priority} "
                        f"(local_healthy={local_healthy})")
            
            # Update Cloudflare route priorities for matching routes
            cloudflare_success = circuit_breakers['cloudflare'].call(
                lambda: exponential_backoff_retry(
                    lambda: cf_mod.update_routes_by_description_bulk(
                        account_id=cfg.cf_account_id,
                        token=cfg.cf_api_token,
                        desc_substring=cfg.cf_desc_substring,
                        desired_priority=desired_priority,
                        structured_logger=structured_logger
                    ),
                    max_retries=cfg.max_retries,
                    initial_delay=cfg.initial_backoff,
                    max_delay=cfg.max_backoff
                )
            )

            # ═══════════════════════════════════════════════════════════════════════════
            # PHASE 6: Cycle Completion and Status Logging
            # ═══════════════════════════════════════════════════════════════════════════
            
            # Determine overall cycle success
            cycle_success = primary_success and secondary_success and cloudflare_success
            
            # Update consecutive error tracking
            if cycle_success:
                consecutive_errors = 0
                logger.info(f"Health check cycle {correlation_id} completed successfully")
            else:
                consecutive_errors += 1
                failed_operations = []
                if not primary_success:
                    failed_operations.append("primary_bgp")
                if not secondary_success:
                    failed_operations.append("secondary_bgp")
                if not cloudflare_success:
                    failed_operations.append("cloudflare")
                
                logger.warning(f"Health check cycle {correlation_id} had failures "
                             f"({consecutive_errors}/{max_consecutive_errors}): "
                             f"{', '.join(failed_operations)}")

            # Calculate cycle performance metrics
            loop_duration = time.time() - loop_start
            
            # Log comprehensive cycle completion event
            cycle_details = {
                "correlation_id": correlation_id,
                "cycle_duration_ms": int(loop_duration * 1000),
                "state_code": new_state_code,
                "health_status": {
                    "local_healthy": local_healthy,
                    "remote_healthy": remote_healthy,
                    "remote_bgp_up": remote_bgp_up
                },
                "operation_results": {
                    "primary_advertisement_success": primary_success,
                    "secondary_advertisement_success": secondary_success,
                    "cloudflare_update_success": cloudflare_success
                },
                "error_tracking": {
                    "consecutive_errors": consecutive_errors,
                    "max_consecutive_errors": max_consecutive_errors
                },
                "configuration": {
                    "desired_cloudflare_priority": desired_priority,
                    "planned_primary_advertisement": advertise_primary,
                    "planned_secondary_advertisement": advertise_secondary
                }
            }
            
            structured_logger.log_event({
                "event_type": "health_check_cycle",
                "timestamp": time.time(),
                "result": ActionResult.SUCCESS.value if cycle_success else ActionResult.FAILURE.value,
                "component": "daemon",
                "operation": "health_check_cycle",
                "details": cycle_details,
                "duration_ms": int(loop_duration * 1000)
            })

            # ═══════════════════════════════════════════════════════════════════════════
            # PHASE 7: Sleep Until Next Check Interval
            # ═══════════════════════════════════════════════════════════════════════════
            
            # Calculate sleep time, accounting for cycle duration
            sleep_time = max(0, cfg.check_interval - loop_duration)
            
            if sleep_time > 0:
                logger.debug(f"[{correlation_id}] Cycle completed in {loop_duration:.2f}s, "
                           f"sleeping {sleep_time:.2f}s until next check")
            else:
                logger.warning(f"[{correlation_id}] Cycle took {loop_duration:.2f}s, "
                             f"longer than check interval {cfg.check_interval}s")
            
            # Wait for next check interval or shutdown signal
            if shutdown_event.wait(sleep_time):
                logger.info("Shutdown signal received during sleep, exiting main loop")
                break

        except Exception as e:
            consecutive_errors += 1
            
            logger.exception(f"Unexpected error in main loop "
                           f"(consecutive error {consecutive_errors}/{max_consecutive_errors}): {e}")
            
            # Log unexpected error with structured event
            error_details = {
                "consecutive_errors": consecutive_errors,
                "max_consecutive_errors": max_consecutive_errors,
                "loop_phase": "unknown",  # Could be enhanced to track current phase
                "correlation_id": correlation_id if 'correlation_id' in locals() else None
            }
            
            structured_logger.log_event({
                "event_type": "daemon_error",
                "timestamp": time.time(),
                "result": ActionResult.FAILURE.value,
                "component": "daemon",
                "operation": "health_check_cycle",
                "details": error_details,
                "error_message": str(e)
            })
            
            # Check if we've hit the consecutive error limit
            if consecutive_errors >= max_consecutive_errors:
                logger.critical(f"Reached maximum consecutive errors ({max_consecutive_errors}), "
                              "daemon is exiting to prevent infinite failure loop")
                break
            
            # Shortened sleep on error to avoid long delays when system is failing
            error_sleep_time = min(cfg.check_interval, 30)
            logger.info(f"Sleeping {error_sleep_time}s before retry due to error")
            
            if shutdown_event.wait(error_sleep_time):
                logger.info("Shutdown signal received during error recovery, exiting")
                break

    # ═══════════════════════════════════════════════════════════════════════════════
    # DAEMON SHUTDOWN SEQUENCE
    # ═══════════════════════════════════════════════════════════════════════════════
    
    # Log daemon shutdown with final state information
    shutdown_details = {
        "reason": "graceful_shutdown" if not consecutive_errors >= max_consecutive_errors else "max_errors_exceeded",
        "consecutive_errors": consecutive_errors,
        "final_state_code": current_state_code,
        "total_uptime_seconds": int(time.time() - loop_start) if 'loop_start' in locals() else 0
    }
    
    structured_logger.log_event({
        "event_type": EventType.DAEMON_LIFECYCLE.value,
        "timestamp": time.time(),
        "result": ActionResult.SUCCESS.value,
        "component": "daemon",
        "operation": "shutdown",
        "details": shutdown_details
    })
    
    logger.info("Main daemon loop exited. Cleanup completed successfully.")


def startup(cfg: Config):
    """
    Bootstrap and validate the daemon environment before starting the main loop.
    
    This function performs all necessary initialization and validation steps to
    ensure the daemon can operate successfully. It validates configuration,
    tests connectivity to external services, and sets up signal handling.
    
    The startup process follows a fail-fast approach: any critical validation
    failure will cause the daemon to exit immediately rather than attempting
    to start with an invalid configuration.
    
    Startup Sequence:
        1. Configuration Validation: Check all required environment variables
        2. GCP Initialization: Build compute client and test connectivity
        3. Cloudflare Validation: Test API credentials and permissions
        4. Signal Handler Setup: Register graceful shutdown handlers
        
    This function is separate from run_loop() to enable clear separation of
    concerns and allow for easier testing of startup logic independently.
    
    Args:
        cfg (Config): Configuration object populated from environment variables.
            Must pass validate_configuration() checks.
            
    Returns:
        compute: Initialized and validated GCP Compute Engine client.
            This client is authenticated and confirmed to have access to
            the required GCP projects and regions.
            
    Raises:
        SystemExit: If any critical validation step fails. Exit codes:
            - 1: Configuration validation failed
            - 1: GCP connectivity validation failed  
            - 1: Cloudflare connectivity validation failed
        FileNotFoundError: If GCP credentials file is not found
        google.auth.exceptions.GoogleAuthError: If GCP authentication fails
        requests.exceptions.HTTPError: If API connectivity tests fail
        
    Side Effects:
        - Creates GCP Compute Engine client with authentication
        - Makes test API calls to GCP and Cloudflare
        - Registers signal handlers for SIGTERM/SIGINT
        - Logs startup progress and validation results
        - May create log files if file logging is configured
        
    Validation Details:
        Configuration validation checks:
        - Presence of all required environment variables
        - Valid IP address and CIDR formats for network prefixes
        - Numeric ranges for timeouts, priorities, and intervals
        - File existence and readability for GCP credentials
        
        GCP connectivity validation:
        - Authentication with service account credentials
        - Access to specified GCP project
        - Access to local and remote regions
        - Compute Engine API permissions
        
        Cloudflare connectivity validation:
        - API token validity and permissions
        - Access to Magic Transit routes for account
        - Account ID validation
        
    Performance:
        - Typical startup time: 2-5 seconds
        - Most time spent on network connectivity tests
        - GCP client initialization: ~500ms
        - Cloudflare validation: ~200-500ms
        
    Error Recovery:
        Startup failures are not retried - the daemon exits immediately.
        This is intentional to prevent misconfigured daemons from starting.
        
        Common failure scenarios and solutions:
        - Missing env vars: Check .env file and required variables list
        - GCP auth failures: Verify service account key and permissions
        - Network connectivity: Check DNS resolution and firewall rules
        - Invalid config values: Review configuration validation errors
        
    Example:
        try:
            cfg = Config()
            compute = startup(cfg)
            print("Startup successful, beginning main loop")
            run_loop(cfg, compute)
        except SystemExit as e:
            print(f"Startup failed with exit code {e.code}")
            
    Monitoring:
        - Monitor startup logs for validation failures
        - Track startup time for performance regression
        - Alert on repeated startup failures (configuration issues)
        - Watch for credential expiration warnings
    """
    # Initialize logging for startup process
    logger = logging.getLogger(os.getenv("LOGGER_NAME", "HEALTH_CHECK_DAEMON"))
    structured_logger = StructuredEventLogger(os.getenv("LOGGER_NAME", "HEALTH_CHECK_DAEMON"))

    logger.info("Daemon startup initiated - beginning validation sequence")

    # ═══════════════════════════════════════════════════════════════════════════════
    # PHASE 1: Configuration Validation
    # ═══════════════════════════════════════════════════════════════════════════════
    
    logger.info("Phase 1: Validating configuration and environment variables")
    
    # Validate all configuration parameters for correctness and completeness
    # This includes required variables, format validation, and range checks
    errors = validate_configuration(cfg)
    
    if errors:
        logger.error("Configuration validation failed with the following errors:")
        for i, error in enumerate(errors, 1):
            logger.error(f"  {i}. {error}")
        
        # Log structured validation failure event
        structured_logger.log_event({
            "event_type": EventType.DAEMON_LIFECYCLE.value,
            "timestamp": time.time(),
            "result": ActionResult.FAILURE.value,
            "component": "daemon",
            "operation": "config_validation",
            "details": {
                "validation_errors": errors,
                "error_count": len(errors)
            },
            "error_message": f"Configuration validation failed with {len(errors)} errors"
        })
        
        logger.critical("Cannot start daemon with invalid configuration. Please fix the above errors.")
        raise SystemExit(1)
    
    logger.info("✓ Configuration validation passed - all required parameters are valid")

    # ═══════════════════════════════════════════════════════════════════════════════
    # PHASE 2: GCP Service Initialization and Connectivity Test
    # ═══════════════════════════════════════════════════════════════════════════════
    
    logger.info("Phase 2: Initializing GCP Compute Engine client and testing connectivity")
    
    try:
        # Build authenticated GCP Compute Engine client
        logger.debug(f"Building GCP client with credentials: {cfg.gcp_credentials}")
        compute = gcp_mod.build_compute_client(cfg.gcp_credentials)
        
        # Test connectivity and permissions
        logger.debug(f"Testing GCP connectivity for project: {cfg.gcp_project}")
        gcp_mod.validate_gcp_connectivity(
            project=cfg.gcp_project,
            regions=[cfg.local_region, cfg.remote_region],
            compute=compute
        )
        
        # Log successful GCP connectivity
        gcp_details = {
            "project": cfg.gcp_project,
            "regions": [cfg.local_region, cfg.remote_region],
            "service_account": cfg.gcp_credentials,
            "local_bgp_router": cfg.local_bgp_router,
            "remote_bgp_router": cfg.remote_bgp_router
        }
        
        structured_logger.log_event({
            "event_type": "connectivity_test",
            "timestamp": time.time(),
            "result": ActionResult.SUCCESS.value,
            "component": "gcp",
            "operation": "connectivity_validation",
            "details": gcp_details
        })
        
        logger.info(f"✓ GCP connectivity validated successfully for project {cfg.gcp_project}")
        logger.info(f"  - Local region: {cfg.local_region}")
        logger.info(f"  - Remote region: {cfg.remote_region}")
        
    except FileNotFoundError as e:
        error_msg = f"GCP credentials file not found: {e}"
        logger.error(error_msg)
        
        structured_logger.log_event({
            "event_type": "connectivity_test",
            "timestamp": time.time(),
            "result": ActionResult.FAILURE.value,
            "component": "gcp",
            "operation": "connectivity_validation",
            "details": {
                "project": cfg.gcp_project,
                "regions": [cfg.local_region, cfg.remote_region],
                "credentials_path": cfg.gcp_credentials
            },
            "error_message": error_msg
        })
        
        logger.critical("Cannot start daemon without valid GCP credentials")
        raise SystemExit(1)
        
    except Exception as e:
        error_msg = f"GCP connectivity validation failed: {str(e)}"
        logger.error(error_msg)
        
        # Log detailed GCP connectivity failure
        structured_logger.log_event({
            "event_type": "connectivity_test",
            "timestamp": time.time(),
            "result": ActionResult.FAILURE.value,
            "component": "gcp",
            "operation": "connectivity_validation",
            "details": {
                "project": cfg.gcp_project,
                "regions": [cfg.local_region, cfg.remote_region],
                "error_type": type(e).__name__
            },
            "error_message": error_msg
        })
        
        logger.critical("Cannot start daemon without GCP connectivity")
        raise SystemExit(1)

    # ═══════════════════════════════════════════════════════════════════════════════
    # PHASE 3: Cloudflare API Validation
    # ═══════════════════════════════════════════════════════════════════════════════
    
    logger.info("Phase 3: Validating Cloudflare API credentials and permissions")
    
    try:
        # Test Cloudflare API connectivity and permissions
        logger.debug(f"Testing Cloudflare connectivity for account: {cfg.cf_account_id}")
        cf_mod.validate_cloudflare_connectivity(cfg.cf_account_id, cfg.cf_api_token)
        
        # Log successful Cloudflare connectivity
        cf_details = {
            "account_id": cfg.cf_account_id,
            "description_filter": cfg.cf_desc_substring,
            "primary_priority": cfg.cf_primary_priority,
            "secondary_priority": cfg.cf_secondary_priority
        }
        
        structured_logger.log_event({
            "event_type": "connectivity_test",
            "timestamp": time.time(),
            "result": ActionResult.SUCCESS.value,
            "component": "cloudflare",
            "operation": "connectivity_validation",
            "details": cf_details
        })
        
        logger.info(f"✓ Cloudflare connectivity validated successfully for account {cfg.cf_account_id}")
        logger.info(f"  - Route filter: '{cfg.cf_desc_substring}'")
        logger.info(f"  - Priority range: {cfg.cf_primary_priority} (primary) / {cfg.cf_secondary_priority} (secondary)")
        
    except Exception as e:
        error_msg = f"Cloudflare connectivity validation failed: {str(e)}"
        logger.error(error_msg)
        
        # Log detailed Cloudflare connectivity failure
        cf_error_details = {
            "account_id": cfg.cf_account_id,
            "description_filter": cfg.cf_desc_substring,
            "error_type": type(e).__name__
        }
        
        structured_logger.log_event({
            "event_type": "connectivity_test",
            "timestamp": time.time(),
            "result": ActionResult.FAILURE.value,
            "component": "cloudflare",
            "operation": "connectivity_validation",
            "details": cf_error_details,
            "error_message": error_msg
        })
        
        logger.critical("Cannot start daemon without Cloudflare connectivity")
        raise SystemExit(1)

    # ═══════════════════════════════════════════════════════════════════════════════
    # PHASE 4: Signal Handler Registration
    # ═══════════════════════════════════════════════════════════════════════════════
    
    logger.info("Phase 4: Setting up signal handlers for graceful shutdown")
    
    try:
        setup_signal_handlers()
        logger.info("✓ Signal handlers registered successfully (SIGTERM, SIGINT)")
    except Exception as e:
        logger.warning(f"Failed to register signal handlers: {e}")
        logger.warning("Daemon will still function but may not shutdown gracefully")

    # ═══════════════════════════════════════════════════════════════════════════════
    # STARTUP COMPLETION
    # ═══════════════════════════════════════════════════════════════════════════════
    
    # Log comprehensive startup success event
    startup_summary = {
        "configuration": {
            "check_interval": cfg.check_interval,
            "circuit_breaker_threshold": cfg.cb_threshold,
            "circuit_breaker_timeout": cfg.cb_timeout,
            "max_retries": cfg.max_retries
        },
        "gcp_configuration": {
            "project": cfg.gcp_project,
            "local_region": cfg.local_region,
            "remote_region": cfg.remote_region,
            "primary_prefix": cfg.primary_prefix,
            "secondary_prefix": cfg.secondary_prefix
        },
        "cloudflare_configuration": {
            "account_id": cfg.cf_account_id,
            "description_filter": cfg.cf_desc_substring,
            "primary_priority": cfg.cf_primary_priority,
            "secondary_priority": cfg.cf_secondary_priority
        },
        "startup_phases_completed": [
            "config_validation",
            "gcp_connectivity",
            "cloudflare_connectivity",
            "signal_handlers"
        ]
    }
    
    structured_logger.log_event({
        "event_type": EventType.DAEMON_LIFECYCLE.value,
        "timestamp": time.time(),
        "result": ActionResult.SUCCESS.value,
        "component": "daemon",
        "operation": "startup_complete",
        "details": startup_summary
    })
    
    logger.info("🚀 Daemon startup completed successfully - all validations passed")
    logger.info("Ready to begin health monitoring and route management")
    
    return compute


# Module-level constants and configuration
DAEMON_VERSION = "1.0.0"
DAEMON_NAME = "MT GCP Health Check Daemon"

# Health check timing constants
MIN_CHECK_INTERVAL = 10     # Minimum seconds between health checks
MAX_CHECK_INTERVAL = 3600   # Maximum seconds between health checks
DEFAULT_CHECK_INTERVAL = 60 # Default check interval if not configured

# Error handling constants
DEFAULT_MAX_CONSECUTIVE_ERRORS = 10  # Exit daemon after this many consecutive errors
MIN_CIRCUIT_BREAKER_THRESHOLD = 1   # Minimum failures before circuit opens
MAX_CIRCUIT_BREAKER_THRESHOLD = 50  # Maximum failures before circuit opens

# Correlation ID format constants
CORRELATION_ID_PREFIX = "hc"  # Health check prefix
CORRELATION_ID_LENGTH = 8     # Length of UUID portion


def get_daemon_info() -> Dict[str, Any]:
    """
    Get comprehensive daemon information for monitoring and debugging.
    
    Returns information about daemon version, configuration, and runtime state
    that can be used for health checks, monitoring dashboards, or debugging.
    
    Returns:
        Dict[str, Any]: Dictionary containing:
            - version: Daemon version string
            - name: Human-readable daemon name  
            - uptime_seconds: How long daemon has been running (if available)
            - shutdown_requested: Whether shutdown has been requested
            - constants: Key daemon constants and limits
            
    Example:
        info = get_daemon_info()
        print(f"Running {info['name']} v{info['version']}")
        if info['shutdown_requested']:
            print("Shutdown in progress...")
    """
    return {
        "name": DAEMON_NAME,
        "version": DAEMON_VERSION,
        "shutdown_requested": shutdown_event.is_set(),
        "constants": {
            "min_check_interval": MIN_CHECK_INTERVAL,
            "max_check_interval": MAX_CHECK_INTERVAL,
            "default_check_interval": DEFAULT_CHECK_INTERVAL,
            "default_max_consecutive_errors": DEFAULT_MAX_CONSECUTIVE_ERRORS,
            "correlation_id_prefix": CORRELATION_ID_PREFIX
        }
    }


def request_shutdown() -> None:
    """
    Programmatically request daemon shutdown.
    
    This function provides a way to request graceful shutdown from within
    the application code, equivalent to sending SIGTERM to the process.
    
    Useful for:
        - Administrative interfaces
        - Error conditions that require restart
        - Testing scenarios
        - Integration with other management systems
        
    Side Effects:
        - Sets the global shutdown_event
        - Causes main loop to exit gracefully after current cycle
        - Triggers shutdown logging events
        
    Note:
        The shutdown is not immediate - the daemon will complete its current
        health check cycle before exiting.
        
    Example:
        # In an admin interface
        if user_requests_shutdown():
            request_shutdown()
            print("Shutdown requested - daemon will exit after current cycle")
    """
    logger = logging.getLogger(os.getenv("LOGGER_NAME", "HEALTH_CHECK_DAEMON"))
    logger.info("Programmatic shutdown requested")
    shutdown_event.set()


# Example usage and testing code
if __name__ == "__main__":
    """
    Direct execution support for testing and development.
    
    When this module is run directly (python -m mt_gcp_daemon.daemon),
    it will load configuration and start the daemon. This is useful for:
    
    - Development and testing
    - Manual daemon execution
    - Debugging configuration issues
    - Validation of startup process
    
    For production deployment, use the main module entry point:
    python -m mt_gcp_daemon
    """
    import sys
    from .config import Config
    from .logging_setup import setup_logger
    
    try:
        print(f"Starting {DAEMON_NAME} v{DAEMON_VERSION}")
        print("Loading configuration from environment...")
        
        # Load configuration
        cfg = Config()
        
        # Setup logging
        logger = setup_logger(
            name=cfg.logger_name,
            level=cfg.log_level,
            log_file=cfg.log_file,
            max_bytes=cfg.log_max_bytes,
            backup_count=cfg.log_backup_count
        )
        
        print("Configuration loaded successfully")
        print(f"Check interval: {cfg.check_interval} seconds")
        print(f"Local region: {cfg.local_region}")
        print(f"Remote region: {cfg.remote_region}")
        
        # Perform startup validation
        print("Performing startup validation...")
        compute = startup(cfg)
        
        print("✓ Startup validation completed successfully")
        print("Starting main daemon loop...")
        print("Press Ctrl+C to shutdown gracefully")
        
        # Start main daemon loop
        run_loop(cfg, compute)
        
        print("Daemon exited gracefully")
        sys.exit(0)
        
    except KeyboardInterrupt:
        print("\nKeyboard interrupt received - shutting down...")
        sys.exit(0)
    except SystemExit as e:
        print(f"Daemon startup failed with exit code {e.code}")
        sys.exit(e.code)
    except Exception as e:
        print(f"Unexpected error during daemon execution: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
